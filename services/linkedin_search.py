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
import unicodedata
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List
from difflib import SequenceMatcher
from urllib.parse import unquote

from ._utils import (
    HEADERS, normalize_domain, LINKEDIN_COMPANY_RE,
    jsonld_organization, linkedin_from_sameas, is_public_linkedin_slug,
    fix_response_encoding,
)
from ._ddg import search_multi

# (connect, read) da página pública da empresa. O read é generoso de propósito:
# a página passa de 350 KB e leva ~11 s, e é dela que sai a contagem exata de
# funcionários — o dado mais pedido da ficha.
PAGE_TIMEOUT = (5, 14)


def _normalize(slug: str) -> str:
    """
    Normaliza URL do LinkedIn em formato canônico.

    unquote() decodifica slugs percent-encoded (ex.: "c%26a_brasil" -> "c&a_brasil")
    para que a URL exibida bata com a que a própria empresa usa.
    """
    slug = slug.lower().rstrip("/").split("?")[0].split("#")[0]
    slug = unquote(slug)
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
        # Slug numérico (ID interno) ou de painel exige login — seguir esse
        # link devolve a tela de /uas/login, não a ficha. Descartar aqui deixa
        # o fluxo cair na adivinhação de slug, que acha a página pública.
        if not is_public_linkedin_slug(slug):
            return
        seen.add(slug)
        candidates.append({"slug": slug, "url": _normalize(slug), "source": source, "weight": weight})

    # sameAs (JSON-LD Organization) — a empresa publica isso para SEO; existe
    # mesmo quando o ícone social é montado via JS e não aparece no footer/
    # header estático que o requests baixa. Prioridade máxima: é dado
    # estruturado declarado pela própria empresa, não um link solto na página.
    same_as_url = linkedin_from_sameas(jsonld_organization(soup))
    if same_as_url:
        add(same_as_url, "jsonld_sameas", 1.2)

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
            fix_response_encoding(resp)
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
    """
    Tenta achar o link do LinkedIn no próprio site (home + páginas comuns).

    A home é consultada primeiro (é onde o link social quase sempre está);
    só se ela não trouxer nada é que as páginas internas são buscadas — todas
    de uma vez. Antes, isto podia virar 36 requisições em fila.
    """
    all_candidates = []

    for base in (f"https://{domain}", f"https://www.{domain}"):
        fetched = _fetch_html(base, timeout=8)
        if fetched:
            html, soup = fetched
            all_candidates.extend(_extract_candidates_from_html(html, soup))
            if all_candidates:
                return _pick_best_candidate(all_candidates, domain)
            break  # o site respondeu, só não tinha link — não tenta o outro host

    paths = ["/about", "/sobre", "/contato", "/contact", "/quem-somos", "/empresa"]
    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        futures = [pool.submit(_fetch_html, f"https://{domain}{p}", 6) for p in paths]
        for future in futures:
            try:
                fetched = future.result(timeout=8)
            except Exception:
                continue
            if fetched:
                html, soup = fetched
                all_candidates.extend(_extract_candidates_from_html(html, soup))

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


def fetch_company_page(linkedin_url: str) -> Optional[str]:
    """
    Baixa a página pública da empresa no LinkedIn.

    Pesa ~350 KB e leva ~11 s, e é a fonte canônica de DOIS campos: a validação
    do vínculo com o domínio e a contagem exata de funcionários. Buscar uma vez
    e compartilhar o HTML evita pagar esse download duas vezes dentro do
    orçamento da requisição — era o que fazia a contagem exata sempre estourar
    o timeout e a ficha cair na faixa declarada (ou em nada).
    """
    if not linkedin_url:
        return None
    try:
        resp = requests.get(
            linkedin_url, headers=HEADERS, timeout=PAGE_TIMEOUT, allow_redirects=True,
        )
        if resp.status_code == 200 and len(resp.text) > 500:
            fix_response_encoding(resp)
            return resp.text
    except Exception:
        pass
    return None


# Rótulos do bloco "Informações" da página da empresa (PT e EN).
_PAGE_FIELDS = {
    "website": ("site", "website"),
    "sector": ("setor", "industry"),
    "size": ("tamanho da empresa", "company size"),
    "location": ("sede", "headquarters"),
}


def parse_company_page(html: Optional[str]) -> dict:
    """
    Lê o bloco "Informações" da página pública da empresa.

    É o mesmo HTML já baixado para validar o vínculo, e entrega de graça três
    campos que o site institucional quase nunca expõe em dados estruturados:
    setor, sede e faixa de funcionários. Devolve {website, sector, size,
    location} com None no que não aparecer.
    """
    out = {key: None for key in _PAGE_FIELDS}
    out["name"] = None
    if not html:
        return out

    soup = BeautifulSoup(html, "html.parser")

    # Nome da empresa como o LinkedIn a chama ("Grupo Madero", "Cia. Hering").
    # Serve para confirmar um slug adivinhado quando o site declarado na
    # página é o do grupo controlador, e não o domínio que buscamos.
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        out["name"] = re.sub(r"\s*\|\s*LinkedIn\s*$", "", og_title["content"]).strip() or None

    for dt in soup.find_all("dt"):
        label = " ".join(dt.get_text(" ", strip=True).split()).lower().rstrip(":")
        key = next((k for k, labels in _PAGE_FIELDS.items() if label in labels), None)
        if not key or out[key]:
            continue
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue
        value = " ".join(dd.get_text(" ", strip=True).split())
        out[key] = value or None

    # O <dd> do site carrega junto o texto acessível do link
    # ("http://acme.com.br Link externo para Acme") — fica só a URL.
    if out["website"]:
        match = re.search(r"https?://\S+", out["website"])
        if match:
            out["website"] = match.group(0)

    return out


def _confidence(html: Optional[str], domain: str, declared_website: Optional[str]) -> str:
    """
    Quanto a página confirma que este LinkedIn é o da empresa do domínio.

    O vínculo forte é o site que a própria empresa declara no bloco
    "Informações" — se ele bate com o domínio buscado, não há dúvida.
    """
    if not html:
        # A página não respondeu — não é sinal contra a empresa, só ausência
        # de prova.
        return "probable"
    domain_lower = (domain or "").lower()
    if not domain_lower:
        return "unverified"

    if declared_website and normalize_domain(declared_website) == domain_lower:
        return "verified"

    text = html.lower()
    if re.search(r'"website"\s*:\s*"https?://(?:www\.)?' + re.escape(domain_lower), text):
        return "verified"
    if domain_lower in text:
        return "probable"
    return "unverified"


def inspect_company_page(linkedin_url: str, domain: str,
                         page_html: Optional[str] = None) -> dict:
    """
    Tudo que a página pública da empresa ensina, com UM download e UM parse:
      {confidence, sector, location, size, html}

    Reunir isto em uma chamada só é o que evita baixar 350 KB duas vezes (uma
    para validar, outra para contar funcionários) dentro do orçamento da
    requisição.
    """
    html = page_html if page_html is not None else fetch_company_page(linkedin_url)
    info = parse_company_page(html)
    return {
        "confidence": _confidence(html, domain, info["website"]) if linkedin_url else "unverified",
        "sector": info["sector"],
        "location": info["location"],
        "size": info["size"],
        "name": info["name"],
        "html": html,
    }


def validate_linkedin(linkedin_url: str, domain: str, page_html: Optional[str] = None) -> str:
    """
    Verifica se o LinkedIn pertence ao domínio.
    Retorna: "verified" | "probable" | "unverified"
    """
    if not linkedin_url:
        return "unverified"
    return inspect_company_page(linkedin_url, domain, page_html=page_html)["confidence"]


# Sufixos e prefixos que a empresa usa no nome mas costuma dropar (ou
# acrescentar) no slug do LinkedIn: "Farmatex Do Brasil" -> farmatex-do-brasil,
# mas "Hering" -> cia-hering e "O Boticário" -> grupo-boticario.
_SLUG_SUFFIX_RE = re.compile(r"-(?:do|da|de)-brasil$|-ltda$|-s-?a$|-group$|-brasil$")
_SLUG_PREFIXES = ("cia-", "grupo-", "o-")

# Teto de candidatos testados. Cada página do LinkedIn leva ~12 s; testamos
# em paralelo, então o custo é ~1 página — mas cada tentativa é uma requisição
# ao LinkedIn, e disparar dezenas por busca é o caminho para tomar bloqueio.
_MAX_SLUG_CANDIDATES = 5


def _slugify(value: str) -> str:
    """'Farmatex Do Brasil' -> 'farmatex-do-brasil' (sem acento, sem símbolo)."""
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()


def _guess_slug_candidates(company_name: Optional[str], domain: str) -> List[str]:
    """
    Palpites de slug do LinkedIn a partir do nome da empresa e do domínio.

    O LinkedIn usa endereços previsíveis, então dá para chegar na página
    pública sem passar por buscador nenhum — o que importa porque os
    buscadores estão todos bloqueando robô. Medido em 12 empresas reais:
    10 acertos, incluindo as de Curitiba (Positivo, Copel, Sanepar, Madero).
    """
    root = domain.split(".")[0]
    base = _slugify(company_name) if company_name else ""
    candidates = [base, root]

    stripped = _SLUG_SUFFIX_RE.sub("", base)
    if stripped and stripped != base:
        candidates.append(stripped)

    candidates.append(base.replace("-", ""))
    candidates += [prefix + (stripped or base) for prefix in _SLUG_PREFIXES]

    seen = set()
    unique = []
    for c in candidates:
        if c and c not in seen and is_public_linkedin_slug(c):
            seen.add(c)
            unique.append(c)
    return unique[:_MAX_SLUG_CANDIDATES]


def _names_match(page_name: Optional[str], company_name: Optional[str], domain: str) -> bool:
    """
    O nome na página do LinkedIn corresponde à empresa que buscamos?

    Segundo critério de aceite de um palpite, para os casos em que o site
    declarado é o do grupo controlador e não o domínio buscado — reais e
    corretos: /company/madero diz "Grupo Madero" (buscamos
    restaurantemadero.com.br) e /company/cia-hering diz "Cia. Hering" mas
    declara o site da Azzas 2154, que a incorporou.
    """
    if not page_name:
        return False
    page_key = re.sub(r"[^a-z0-9]", "", _slugify(page_name))
    if not page_key:
        return False

    for raw in (company_name, domain.split(".")[0]):
        key = re.sub(r"[^a-z0-9]", "", _slugify(raw or ""))
        # Mínimo de 4 caracteres: "sa"/"cia" dentro de outro nome não é prova
        # de nada e aceitaria a empresa errada.
        if len(key) < 4:
            continue
        if key in page_key or page_key in key:
            return True
        if _similarity(key, page_key) >= 0.85:
            return True
    return False


def _guess_from_slugs(domain: str, company_name: Optional[str]) -> Optional[dict]:
    """
    Testa os palpites em paralelo e devolve {url, page} do primeiro
    confirmado.

    Exigir confirmação é o que separa isto de um chute: um slug parecido
    pode ser de outra empresa. Aceitamos por dois caminhos — o site
    declarado no LinkedIn batendo com o domínio ("verified"), ou o nome da
    página batendo com o da empresa. Palpite sem nenhuma das duas provas é
    descartado.
    """
    candidates = _guess_slug_candidates(company_name, domain)
    if not candidates:
        return None

    def _try(slug: str) -> Optional[dict]:
        url = _normalize(slug)
        page = inspect_company_page(url, domain)
        if page["confidence"] == "verified":
            return {"url": url, "page": page}
        if _names_match(page.get("name"), company_name, domain):
            # Sem o site batendo, o vínculo é forte mas não é prova — a ficha
            # mostra "provável" para o vendedor saber o que está olhando.
            page["confidence"] = "probable"
            return {"url": url, "page": page}
        return None

    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        futures = [pool.submit(_try, slug) for slug in candidates]
        # Ordem dos candidatos importa: o primeiro palpite é o mais provável,
        # então percorremos na ordem de submissão, não na de conclusão.
        for future in futures:
            try:
                result = future.result(timeout=PAGE_TIMEOUT[1] + 4)
            except Exception:
                continue
            if result:
                return result
    return None


def find_company_linkedin(domain: str, company_name: Optional[str] = None) -> dict:
    """
    Busca o LinkedIn da empresa com múltiplas estratégias e devolve dict
    com {url, confidence, source, page}.

    `page` é o resultado de inspect_company_page() — quem chama reaproveita o
    HTML e os campos já extraídos em vez de baixar a página de novo.
    """
    domain = normalize_domain(domain)

    # Estratégia 1: extração do site (a empresa declarando o próprio perfil)
    found = _extract_from_site(domain)
    source = "site"
    page = None

    # Estratégia 2: adivinhar o slug. Vem antes da busca em engines porque
    # não depende de infraestrutura de terceiros — e os buscadores estão
    # todos barrando robô hoje (captcha/anti-bot).
    if not found:
        guessed = _guess_from_slugs(domain, company_name)
        if guessed:
            found, page, source = guessed["url"], guessed["page"], "slug_guess"

    # Estratégia 3: busca em engines — último recurso, hoje dormente.
    if not found:
        found = _search_engines(domain, company_name)
        source = "search_engine"

    if not found:
        return {"url": None, "confidence": "none", "source": None, "page": None}

    if page is None:
        page = inspect_company_page(found, domain)
    return {"url": found, "confidence": page["confidence"], "source": source, "page": page}
