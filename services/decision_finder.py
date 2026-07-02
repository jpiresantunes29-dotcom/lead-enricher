"""
Encontra decisores em uma empresa filtrando por cargo, com VERIFICAÇÃO.

Estratégia (em ordem de prioridade):
  1. Aba People/Pessoas do LinkedIn (linkedin.com/company/{slug}/people/)
     — fonte direta; associação empresa→pessoa é garantida pela página
  2. Fallback: Multi-engine search (SearXNG → DDG → Bing → Google)
     com query: site:linkedin.com/in "{cargo}" "{empresa}"

Cargos de decisão reconhecidos e priorizados por TITLE_PRIORITY.
Sem dependência de APIs pagas.
"""
import logging
import re
import requests
from typing import List, Optional
from html import unescape
from urllib.parse import unquote
from bs4 import BeautifulSoup

from ._utils import normalize_domain, HEADERS, LINKEDIN_COMPANY_RE
from ._ddg import search_multi
from .email_verifier import verify_emails

logger = logging.getLogger(__name__)


LINKEDIN_PROFILE_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([a-zA-Z0-9\-._%]+)",
    re.IGNORECASE,
)

# Cargos que indicam poder de decisão
DECISION_MAKER_TITLES = [
    "Founder", "Co-Founder", "Co-founder",
    "CEO", "Owner",
    "Chief", "VP", "Vice President", "Partner",
    "Director", "Head of",
    "CTO", "CFO", "COO", "CMO", "CRO", "CPO",
    "Presidente", "Sócio", "Diretor", "Gerente Geral",
]

# Menor número = maior prioridade
TITLE_PRIORITY: dict = {
    "founder": 1, "co-founder": 1, "ceo": 1, "owner": 1, "presidente": 1,
    "sócio": 1, "socio": 1,
    "chief": 2, "cto": 2, "cfo": 2, "coo": 2, "cmo": 2, "cro": 2, "cpo": 2,
    "vp": 2, "vice president": 2, "partner": 2,
    "director": 3, "diretor": 3, "head of": 3,
    "gerente geral": 4,
}

_CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def _title_priority(title: str) -> int:
    """Retorna prioridade numérica para um título (menor = mais relevante)."""
    t = title.lower()
    for keyword, prio in TITLE_PRIORITY.items():
        if keyword in t:
            return prio
    return 99


def _is_decision_maker_title(title: str) -> bool:
    """Verifica se o título é de um decisor reconhecido."""
    t = title.lower()
    return any(dm.lower() in t for dm in DECISION_MAKER_TITLES)


def _slug_to_name(slug: str) -> str:
    slug = unquote(slug)
    parts = re.split(r"[\-_]", slug)
    parts = [p for p in parts if not p.isdigit() and not re.fullmatch(r"[a-f0-9]{6,}", p)]
    return " ".join(p.capitalize() for p in parts[:4]) if parts else slug.title()


def _extract_name(title: str, slug: str) -> str:
    """
    Extrai nome do decisor do título do resultado de busca.
    Padrões: "João Silva - CTO - Acme Corp | LinkedIn"
    """
    if not title:
        return _slug_to_name(slug)
    title = unescape(title)
    title = re.sub(r"^\(\d+\+?\)\s*", "", title)
    title = re.sub(r"\s*[\|·]\s*LinkedIn.*$", "", title, flags=re.IGNORECASE)
    name_part = re.split(r"\s+[\-–—|·]\s+", title)[0].strip()
    if 3 <= len(name_part) <= 80 and not name_part.lower().startswith(
        ("cto", "ceo", "cfo", "vp ", "diretor", "head", "chief")
    ):
        return name_part
    return _slug_to_name(slug)


def _generate_emails(name: str, domain: str) -> List[str]:
    """Gera padrões prováveis de email corporativo."""
    if not name or not domain:
        return []
    parts = [p for p in re.split(r"\s+", name.lower()) if p.isalpha()]
    if not parts:
        return []
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else None
    emails = [f"{first}@{domain}"]
    if last and last != first:
        emails.extend([
            f"{first}.{last}@{domain}",
            f"{first}{last}@{domain}",
            f"{first[0]}{last}@{domain}",
        ])
    return list(dict.fromkeys(emails))[:4]


def _match_confidence(snippet: str, title: str, role: str, company: str) -> str:
    """Avalia se snippet+título confirmam cargo e empresa."""
    text = (snippet + " " + title).lower()
    has_role = bool(role) and role.lower() in text
    has_company = bool(company) and company.lower() in text
    if has_role and has_company:
        return "high"
    if has_role or has_company:
        return "medium"
    return "low"


def _build_decisor(name: str, title_found: str, slug: str, domain: str,
                   source_role: str, snippet: str, confidence: str,
                   verify_emails_smtp: bool) -> dict:
    emails_raw = _generate_emails(name, domain)
    if verify_emails_smtp and emails_raw:
        probable_emails = verify_emails(emails_raw, max_checks=3)
    else:
        probable_emails = [{"email": e, "status": "unknown"} for e in emails_raw]
    return {
        "name": name,
        "title_searched": source_role,
        "title_found": title_found,
        "snippet": snippet,
        "linkedin_url": f"https://www.linkedin.com/in/{slug}",
        "probable_emails": probable_emails,
        "match_confidence": confidence,
        "phone": None,
    }


# ---------------------------------------------------------------------------
# Fonte 1 — Aba People do LinkedIn
# ---------------------------------------------------------------------------

def _fetch_people_tab_decisors(
    linkedin_url: str,
    company_name: str,
    roles: List[str],
    domain: str,
    verify_emails_smtp: bool,
) -> List[dict]:
    """
    Extrai decisores diretamente da aba People/Pessoas do LinkedIn.
    Retorna lista de dicts no schema padrão.
    Requer que o LinkedIn retorne HTML sem autenticação (melhor esforço).
    """
    slug_match = LINKEDIN_COMPANY_RE.search(linkedin_url)
    if not slug_match:
        return []

    company_slug = slug_match.group(1).rstrip("/")
    people_url = f"https://www.linkedin.com/company/{company_slug}/people/"

    try:
        resp = requests.get(people_url, headers=HEADERS, timeout=12, allow_redirects=True)
        if resp.status_code != 200 or len(resp.text) < 2000:
            return []
        html = resp.text
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    found: List[dict] = []
    seen_slugs: set = set()

    # Estratégia A: cartões de perfil visíveis na página
    # LinkedIn renderiza alguns perfis em <li> ou <div> com nome e cargo
    for card in soup.find_all(["li", "div"], class_=re.compile(r"(profile|member|result|person|employee)", re.I)):
        name_tag = card.find(["h3", "h4", "span", "a"], string=re.compile(r"[A-ZÀ-Ü][a-zà-ü]"))
        title_tag = card.find(["p", "span", "div"], string=re.compile(r"[A-Za-z]{3,}"))

        if not name_tag:
            continue

        name = name_tag.get_text(strip=True)
        title_found = title_tag.get_text(strip=True) if title_tag else ""

        if not _is_decision_maker_title(title_found):
            continue

        # Tenta extrair LinkedIn URL do cartão
        a_tag = card.find("a", href=re.compile(r"linkedin\.com/in/"))
        if a_tag:
            m = LINKEDIN_PROFILE_RE.search(a_tag["href"])
            slug = m.group(1).lower() if m else _slug_to_name(name).replace(" ", "-").lower()
        else:
            slug = name.lower().replace(" ", "-")

        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        # Determina qual role do pedido melhor casa com o título encontrado
        matched_role = next(
            (r for r in roles if r.lower() in title_found.lower()),
            title_found or roles[0],
        )

        found.append(_build_decisor(
            name=name,
            title_found=title_found,
            slug=slug,
            domain=domain,
            source_role=matched_role,
            snippet=f"Encontrado na aba People de {company_name} no LinkedIn.",
            confidence="high",
            verify_emails_smtp=verify_emails_smtp,
        ))

    # Estratégia B: JSON embutido na página (LinkedIn injeta dados em scripts)
    if not found:
        for script in soup.find_all("script", type="application/json"):
            try:
                import json
                data = json.loads(script.string or "")
                _extract_from_json(data, roles, company_name, domain, seen_slugs, found, verify_emails_smtp)
            except Exception:
                continue

    return found


def _extract_from_json(data, roles: List[str], company_name: str,
                       domain: str, seen_slugs: set, found: List[dict],
                       verify_emails_smtp: bool):
    """Extrai recursivamente perfis de estruturas JSON do LinkedIn."""
    if isinstance(data, dict):
        name = data.get("firstName", "") or data.get("name", "")
        title = data.get("headline", "") or data.get("title", "") or data.get("occupation", "")
        if name and title and _is_decision_maker_title(title):
            slug = data.get("publicIdentifier", name.lower().replace(" ", "-"))
            if slug not in seen_slugs:
                seen_slugs.add(slug)
                matched_role = next(
                    (r for r in roles if r.lower() in title.lower()),
                    title or roles[0],
                )
                found.append(_build_decisor(
                    name=name,
                    title_found=title,
                    slug=slug,
                    domain=domain,
                    source_role=matched_role,
                    snippet=f"Encontrado via dados estruturados da página People de {company_name}.",
                    confidence="high",
                    verify_emails_smtp=verify_emails_smtp,
                ))
        for v in data.values():
            if isinstance(v, (dict, list)):
                _extract_from_json(v, roles, company_name, domain, seen_slugs, found, verify_emails_smtp)
    elif isinstance(data, list):
        for item in data:
            _extract_from_json(item, roles, company_name, domain, seen_slugs, found, verify_emails_smtp)


# ---------------------------------------------------------------------------
# Fonte 2 — Fallback: busca em motores de pesquisa
# ---------------------------------------------------------------------------

def _search_one_role(role: str, company_term: str) -> List[dict]:
    """Busca multi-engine (SearXNG → DDG → Bing → Google) com fallback."""
    query = f'site:linkedin.com/in "{role}" "{company_term}"'
    results = search_multi(query, pattern=r"linkedin\.com/in/[a-zA-Z0-9\-._%]+")
    if not results:
        query2 = f'site:linkedin.com/in {role} {company_term}'
        results = search_multi(query2, pattern=r"linkedin\.com/in/[a-zA-Z0-9\-._%]+")
    return results


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

def find_decision_makers(
    domain: str,
    company_name: Optional[str],
    roles: List[str],
    limit: int = 5,
    verify_emails_smtp: bool = False,
    linkedin_url: Optional[str] = None,
) -> List[dict]:
    """
    Busca decisores e devolve lista com:
      name, title_searched, title_found, snippet, linkedin_url,
      probable_emails (lista de {email, status}), match_confidence, phone

    Estratégia:
      1. Aba People do LinkedIn (se linkedin_url disponível)
      2. Fallback: busca por motor de pesquisa por cargo
    Sem dependência de APIs pagas.
    """
    domain = normalize_domain(domain)
    company_term = company_name or domain.split(".")[0]
    found: List[dict] = []
    seen_slugs: set = set()

    # Fonte 1: aba People — acesso direto, confiança alta
    if linkedin_url:
        people_results = _fetch_people_tab_decisors(
            linkedin_url=linkedin_url,
            company_name=company_term,
            roles=roles,
            domain=domain,
            verify_emails_smtp=verify_emails_smtp,
        )
        for r in people_results:
            slug = LINKEDIN_PROFILE_RE.search(r.get("linkedin_url", ""))
            slug_key = slug.group(1).lower() if slug else r.get("name", "").lower()
            if slug_key not in seen_slugs:
                seen_slugs.add(slug_key)
                found.append(r)
            if len(found) >= limit:
                break

    # Fonte 2: fallback por busca — complementa se necessário
    if len(found) < limit:
        for role in roles[:3]:
            if len(found) >= limit:
                break
            results = _search_one_role(role, company_term)
            for r in results:
                url = r.get("url", "")
                m = LINKEDIN_PROFILE_RE.search(url)
                if not m:
                    continue
                slug = m.group(1).lower()
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                title = r.get("title", "")
                snippet = r.get("snippet", "")
                name = _extract_name(title, slug)
                confidence = _match_confidence(snippet, title, role, company_term)

                found.append(_build_decisor(
                    name=name,
                    title_found=title,
                    slug=slug,
                    domain=domain,
                    source_role=role,
                    snippet=snippet,
                    confidence=confidence,
                    verify_emails_smtp=verify_emails_smtp,
                ))
                if len(found) >= limit:
                    break

    # Ordena: primeiro por prioridade de cargo, depois por confiança
    found.sort(key=lambda x: (
        _title_priority(x.get("title_found", "") or x.get("title_searched", "")),
        _CONFIDENCE_ORDER.get(x.get("match_confidence", "low"), 2),
    ))

    logger.info(
        "Decision makers found=%d domain=%s roles=%s",
        len(found[:limit]), domain, roles,
    )
    return found[:limit]
