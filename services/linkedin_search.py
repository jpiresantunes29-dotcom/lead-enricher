"""
Busca a página da empresa no LinkedIn a partir do domínio, com VALIDAÇÃO.

Estratégia:
  1. Extrair candidatos do site (home + /about + /sobre + /contact + /empresa + /quem-somos)
     - prioriza âncoras dentro de <footer> ou <header>
     - coleta TODOS os matches; escolhe o mais provável por similaridade
  2. Fallback DuckDuckGo + Bing (multi-engine compartilhado)
  3. Valida o LinkedIn encontrado:
     - Faz GET na página pública
     - Procura "website":"<domain>" no JSON ou og:url
     - Devolve confidence: "verified" | "probable" | "unverified"
"""
import re
import requests
from bs4 import BeautifulSoup
from typing import Optional, List
from difflib import SequenceMatcher

from ._utils import HEADERS, normalize_domain, LINKEDIN_COMPANY_RE
from ._ddg import search_multi


def _normalize(slug: str) -> str:
    """Normaliza URL do LinkedIn em formato canônico."""
    slug = slug.lower().rstrip("/").split("?")[0].split("#")[0]
    return f"https://www.linkedin.com/company/{slug}"


def _slug_from_url(url: str) -> Optional[str]:
    m = LINKEDIN_COMPANY_RE.search(url)
    return m.group(1).lower() if m else None


def _similarity(a: str, b: str) -> float:
    """Similaridade entre dois textos normalizados (0..1)."""
    a = re.sub(r"[^a-z0-9]", "", a.lower())
    b = re.sub(r"[^a-z0-9]", "", b.lower())
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _extract_candidates_from_html(html: str, soup: BeautifulSoup) -> List[dict]:
    """
    Extrai todos os candidatos LinkedIn do HTML, com metadados de origem
    (footer/header/body).
    """
    candidates = []
    seen = set()

    def add(url: str, source: str, weight: float):
        slug = _slug_from_url(url)
        if not slug or slug in seen:
            return
        seen.add(slug)
        candidates.append({"slug": slug, "url": _normalize(slug), "source": source, "weight": weight})

    # Footer / header (mais confiável)
    for container_name, weight in [("footer", 1.0), ("header", 0.8)]:
        container = soup.find(container_name)
        if container:
            for a in container.find_all("a", href=True):
                if "linkedin.com/company/" in a["href"]:
                    add(a["href"], container_name, weight)

    # Qualquer âncora
    for a in soup.find_all("a", href=True):
        if "linkedin.com/company/" in a["href"]:
            add(a["href"], "anchor", 0.5)

    # Regex no texto bruto
    for url in LINKEDIN_COMPANY_RE.findall(html):
        full = f"https://linkedin.com/company/{url}"
        add(full, "regex", 0.3)

    return candidates


def _fetch_html(url: str, timeout: int = 10) -> Optional[tuple]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text, BeautifulSoup(resp.text, "html.parser")
    except Exception:
        pass
    return None


def _pick_best_candidate(candidates: List[dict], domain: str) -> Optional[str]:
    """Escolhe melhor candidato: peso da origem * similaridade do slug com domínio."""
    if not candidates:
        return None
    domain_root = domain.split(".")[0]
    scored = []
    for c in candidates:
        sim = _similarity(c["slug"], domain_root)
        score = c["weight"] * (0.5 + sim * 0.5)
        scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]["url"]


def _extract_from_site(domain: str) -> Optional[str]:
    """Tenta achar o link do LinkedIn no próprio site (home + páginas comuns)."""
    paths = ["", "/about", "/sobre", "/contact", "/contato", "/about-us",
             "/quem-somos", "/empresa", "/company"]
    all_candidates = []
    # Home tem prioridade
    for scheme in ("https://", "http://"):
        for prefix in ("", "www."):
            base = f"{scheme}{prefix}{domain}"
            for path in paths:
                fetched = _fetch_html(base + path)
                if not fetched:
                    continue
                html, soup = fetched
                all_candidates.extend(_extract_candidates_from_html(html, soup))
                # Se já achou na home, não precisa buscar /about
                if path == "" and all_candidates:
                    break
            if all_candidates:
                break
        if all_candidates:
            break
    return _pick_best_candidate(all_candidates, domain)


def _search_engines(domain: str, company_name: Optional[str]) -> Optional[str]:
    """Busca via DDG/Bing (compartilhado), prioriza match com nome da empresa."""
    queries = [
        f'site:linkedin.com/company "{domain}"',
    ]
    if company_name:
        queries.insert(0, f'site:linkedin.com/company "{company_name}"')

    candidates = []
    domain_root = domain.split(".")[0]
    for query in queries:
        results = search_multi(query)
        for r in results:
            slug = _slug_from_url(r.get("url", ""))
            if not slug:
                continue
            # Score: similaridade do slug com domínio + título com company_name
            sim_slug = _similarity(slug, domain_root)
            sim_title = _similarity(r.get("title", ""), company_name or domain_root)
            score = (sim_slug * 0.6) + (sim_title * 0.4)
            candidates.append((score, _normalize(slug)))
        if candidates:
            break

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def validate_linkedin(linkedin_url: str, domain: str) -> str:
    """
    Verifica se o LinkedIn pertence ao domínio.
    Retorna: "verified" | "probable" | "unverified"
    """
    if not linkedin_url:
        return "unverified"
    try:
        resp = requests.get(linkedin_url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code != 200:
            return "probable"
        text = resp.text.lower()
        domain_lower = domain.lower()
        # Se a página menciona o domínio em um campo "website", é verified
        if f'"website":"http' in text and domain_lower in text:
            # checagem mais forte: website explicitamente bate com domínio
            if re.search(r'"website":"https?://(?:www\.)?' + re.escape(domain_lower), text):
                return "verified"
            return "probable"
        if domain_lower in text:
            return "probable"
        return "unverified"
    except Exception:
        return "probable"


def find_company_linkedin(domain: str, company_name: Optional[str] = None) -> dict:
    """
    Busca o LinkedIn da empresa com múltiplas estratégias e devolve dict
    com {url, confidence, source}.
    """
    domain = normalize_domain(domain)

    # Estratégia 1: extração do site
    found = _extract_from_site(domain)
    source = "site"
    # Estratégia 2: busca em engines
    if not found:
        found = _search_engines(domain, company_name)
        source = "search_engine"

    if not found:
        return {"url": None, "confidence": "none", "source": None}

    confidence = validate_linkedin(found, domain)
    return {"url": found, "confidence": confidence, "source": source}
