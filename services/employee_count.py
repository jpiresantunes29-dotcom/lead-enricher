"""
Conta funcionários por cascata multi-fonte:
  1. Página pública da empresa no LinkedIn — contagem EXATA de membros
     associados ("Visualizar todos os N funcionários", link da aba Pessoas).
     A aba /people/ em si exige login, mas o número exato aparece na página
     principal (face-pile) — é essa a fonte canônica.
  2. LinkedIn aba People direta (raramente acessível sem login)
  3. Faixa da página principal do LinkedIn ("5.001-10.000 funcionários")
  4. Google Cache / Bing snapshot
  5. Página /about ou /sobre do site da empresa

Normaliza para {raw, min, max, band, exact, source}.
source == "linkedin_people" ⇒ contagem exata de membros associados.
"""
import re
import requests
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

from ._utils import HEADERS, LINKEDIN_COMPANY_RE, looks_like_search_block

# Padrões comuns no og:description / página do LinkedIn
COUNT_PATTERNS = [
    # "1,001-5,000 employees" / "501-1.000 funcionários" / "10,000+ employees"
    re.compile(r"([\d,.]+)\s*[\-–]\s*([\d,.]+)\s*(?:employees|funcion[áa]rios|empleados|colaboradores)", re.IGNORECASE),
    re.compile(r"([\d,.]+)\+\s*(?:employees|funcion[áa]rios|empleados|colaboradores)", re.IGNORECASE),
    re.compile(r"([\d,.]+)\s*(?:employees|funcion[áa]rios|empleados|colaboradores)", re.IGNORECASE),
    # JSON-LD / API LinkedIn
    re.compile(r'"employeeCount"\s*:\s*"?([\d]+)"?'),
    re.compile(r'"staffCount"\s*:\s*([\d]+)'),
    re.compile(r'"staffCountRange"\s*:\s*"\(([\d,.]+),([\d,.]+)\)"'),
]

# Padrões da contagem EXATA de membros associados, como aparece na página
# pública da empresa (link "Visualizar todos os N funcionários" → aba Pessoas)
EXACT_MEMBER_PATTERNS = [
    # pt: "Visualizar todos os 12.701 funcionários" / en: "View all 15,924 employees"
    re.compile(
        r"(?:Visualizar|Ver|View|See)\s+(?:todos\s+os|todas\s+as|all)\s+([\d,.]+)\s+"
        r"(?:funcion[áa]rios?|employees?|empleados?|colaboradores?)",
        re.IGNORECASE,
    ),
    # "12.701 associated members" / "12.701 membros associados"
    re.compile(r"([\d,.]+)\s+(?:associated\s+members?|membros?\s+associados?)", re.IGNORECASE),
    # JSON embutido
    re.compile(r'"staffCount"\s*:\s*(\d+)'),
]

# Padrões específicos da aba People — retornam contagem exata
PEOPLE_TAB_PATTERNS = [
    # "4,732 employees on LinkedIn" / "4.732 funcionários no LinkedIn"
    re.compile(r"([\d,.]+)\s*(?:employees?|funcionários?|colaboradores?)\s+(?:on|no|na|em)\s+LinkedIn", re.IGNORECASE),
    # "See Company's 4,732 employees on LinkedIn"
    re.compile(r"(?:See|Veja|Ver)\s+[\w\s'']+[''s]*\s+([\d,.]+)\s+(?:employees?|funcionários?)", re.IGNORECASE),
    # JSON embedded in page: "staffCount":4732
    re.compile(r'"staffCount"\s*:\s*([\d]+)'),
    re.compile(r'"employeeCount"\s*:\s*"?([\d]+)"?'),
]

# Bandas padrão LinkedIn
BANDS = [
    (1, 10, "1-10"),
    (11, 50, "11-50"),
    (51, 200, "51-200"),
    (201, 500, "201-500"),
    (501, 1000, "501-1000"),
    (1001, 5000, "1001-5000"),
    (5001, 10000, "5001-10000"),
    (10001, 99999999, "10000+"),
]

# Teto de sanidade: a maior empregadora privada do mundo tem ~2,1 milhões de
# pessoas. Qualquer número acima disso é lixo capturado do HTML (ID de página,
# CEP, código de produto) — e um dado absurdo destrói a confiança na ferramenta.
MAX_PLAUSIBLE_EMPLOYEES = 2_500_000


def _to_int(s: str) -> Optional[int]:
    try:
        return int(re.sub(r"[,.\s]", "", s))
    except Exception:
        return None


def _plausible(value: Optional[int]) -> bool:
    return bool(value) and 1 <= value <= MAX_PLAUSIBLE_EMPLOYEES


def _band_for(value: int) -> str:
    for lo, hi, label in BANDS:
        if lo <= value <= hi:
            return label
    return "unknown"


def normalize_employee_count(raw: str) -> Optional[dict]:
    """
    Converte texto cru em {raw, min, max, band, exact}.
    """
    if not raw:
        return None
    raw = raw.strip()

    # Faixa explícita: "1,001-5,000"
    m = re.search(r"([\d,.]+)\s*[\-–]\s*([\d,.]+)", raw)
    if m:
        lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        if _plausible(lo) and _plausible(hi) and lo <= hi:
            return {"raw": raw, "min": lo, "max": hi, "band": _band_for(lo), "exact": None}

    # "10,000+"
    m = re.search(r"([\d,.]+)\+", raw)
    if m:
        lo = _to_int(m.group(1))
        if _plausible(lo):
            return {"raw": raw, "min": lo, "max": None, "band": _band_for(lo), "exact": None}

    # número exato
    m = re.search(r"([\d,.]+)", raw)
    if m:
        n = _to_int(m.group(1))
        if _plausible(n):
            return {"raw": raw, "min": n, "max": n, "band": _band_for(n), "exact": n}

    return None


def _find_in_text(text: str) -> Optional[dict]:
    """Roda os patterns em ordem; devolve o primeiro match normalizado."""
    if not text:
        return None
    for pat in COUNT_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        # range pattern (2 grupos)
        if m.lastindex and m.lastindex >= 2:
            lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
            if _plausible(lo) and _plausible(hi) and lo <= hi:
                return {"raw": m.group(0), "min": lo, "max": hi, "band": _band_for(lo), "exact": None}
        else:
            n = normalize_employee_count(m.group(0))
            if n:
                return n
    return None


def _try_url(url: str, timeout: int = 8) -> Optional[str]:
    try:
        # (connect, read) separados: um servidor que responde devagar em
        # pedaços não pode segurar a requisição inteira do usuário.
        resp = requests.get(url, headers=HEADERS, timeout=(4, timeout), allow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except Exception:
        pass
    return None


def _exact_from_company_page(html: str) -> Optional[dict]:
    """
    Extrai a contagem exata de membros associados do HTML da página pública
    da empresa (o "Visualizar todos os N funcionários" do face-pile).
    Retorna {raw, min, max, band, exact, source: 'linkedin_people'} ou None.
    """
    if not html:
        return None

    def _build(n: int, raw: str) -> dict:
        return {
            "raw": raw,
            "min": n,
            "max": n,
            "band": _band_for(n),
            "exact": n,
            "source": "linkedin_people",
        }

    # 1) Elemento do face-pile (mais preciso — é o link da aba Pessoas)
    try:
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one("p.face-pile__text")
        if el:
            txt = " ".join(el.get_text(" ", strip=True).split())
            m = re.search(r"([\d,.]+)", txt)
            if m:
                n = _to_int(m.group(1))
                if n:
                    return _build(n, txt)
    except Exception:
        pass

    # 2) Padrões textuais/JSON no HTML completo
    for pat in EXACT_MEMBER_PATTERNS:
        m = pat.search(html)
        if m:
            n = _to_int(m.group(1))
            if n:
                return _build(n, " ".join(m.group(0).split()))

    return None


def _fetch_people_tab_count(linkedin_url: str) -> Optional[dict]:
    """
    Busca contagem exata de funcionários na aba People/Pessoas do LinkedIn.
    URL: linkedin.com/company/{slug}/people/
    Retorna {raw, min, max, band, exact, source: 'linkedin_people'} ou None.
    """
    slug_match = LINKEDIN_COMPANY_RE.search(linkedin_url)
    if not slug_match:
        return None

    slug = slug_match.group(1).rstrip("/")
    people_url = f"https://www.linkedin.com/company/{slug}/people/"
    text = _try_url(people_url)
    if not text or len(text) < 2000:
        return None

    soup = BeautifulSoup(text, "html.parser")

    # Tenta og:description primeiro (mais confiável — LinkedIn coloca o número ali)
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        desc_content = og_desc.get("content", "")
        for pat in PEOPLE_TAB_PATTERNS:
            m = pat.search(desc_content)
            if m:
                n = _to_int(m.group(1))
                if n:
                    result = normalize_employee_count(str(n))
                    if result:
                        result["source"] = "linkedin_people"
                        return result

    # Tenta texto visível da página
    page_text = soup.get_text(" ", strip=True)
    for pat in PEOPLE_TAB_PATTERNS:
        m = pat.search(page_text)
        if m:
            n = _to_int(m.group(1))
            if n:
                result = normalize_employee_count(str(n))
                if result:
                    result["source"] = "linkedin_people"
                    return result

    # Tenta JSON embutido na página
    for pat in PEOPLE_TAB_PATTERNS[-2:]:  # os dois padrões JSON
        m = pat.search(text)
        if m:
            n = _to_int(m.group(1))
            if n:
                result = normalize_employee_count(str(n))
                if result:
                    result["source"] = "linkedin_people"
                    return result

    return None


def _bing_count(linkedin_url: str) -> Optional[dict]:
    """Snapshot do Bing. Só vale para slug textual — ver comentário abaixo."""
    slug_match = LINKEDIN_COMPANY_RE.search(linkedin_url)
    # Slug numérico é o ID interno do LinkedIn (ex.: /company/2629565). A página
    # de busca repete o termo pesquisado, e o regex leria esse ID como
    # "2.629.565 funcionários" — dado absurdo. Nesse caso, não busca.
    if not slug_match or slug_match.group(1).isdigit():
        return None
    slug = slug_match.group(1)
    bing_url = f"https://www.bing.com/search?q={quote_plus(f'linkedin.com/company/{slug} employees')}"
    text = _try_url(bing_url)
    # O Bing está bloqueando robô (verificado em ago/2026): devolve página de
    # desafio com 200. Sem esta guarda, o regex leria números do bloqueio como
    # se fossem funcionários. Fica como rede de segurança dormente.
    if not text or looks_like_search_block(text):
        return None
    # Remove o eco do próprio termo pesquisado antes de extrair números
    text = re.sub(rf"linkedin\.com/company/{re.escape(slug)}", " ", text, flags=re.IGNORECASE)
    result = _find_in_text(text)
    if result:
        result["source"] = "bing_search"
    return result


def _website_count(website_url: str) -> Optional[dict]:
    """Procura a contagem nas páginas institucionais do site, em paralelo."""
    base = website_url.rstrip("/")
    suffixes = ["/about", "/sobre", "/quem-somos", "/about-us", "/empresa"]
    with ThreadPoolExecutor(max_workers=len(suffixes)) as pool:
        futures = [pool.submit(_try_url, base + s, 6) for s in suffixes]
        for future in futures:
            try:
                text = future.result(timeout=8)
            except Exception:
                continue
            if not text:
                continue
            result = _find_in_text(text)
            if result:
                result["source"] = "company_website"
                return result
    return None


def fetch_employee_count(linkedin_url: Optional[str], website_url: Optional[str] = None,
                         page_html: Optional[str] = None,
                         allow_network: bool = True) -> Optional[dict]:
    """
    Cascata de fontes, executada em paralelo e resolvida por prioridade.

    Sequencial, isto custava ~40 s por empresa (cada fonte esperando a
    anterior falhar). Em paralelo custa o tempo da fonte mais lenta, e a
    escolha continua sendo pela mais confiável — não pela mais rápida.

    `page_html` evita rebaixar a página da empresa quando quem chama já a
    baixou (ver services.linkedin_search.fetch_company_page).

    `allow_network=False` limita a busca ao que dá para ler do `page_html` já
    em mãos — é o que quem chama usa quando não há mais orçamento de tempo
    para gastar em rede.
    """
    # A contagem exata na página da empresa é a fonte canônica. Quando o HTML
    # já veio pronto, ela sai sem custo — não há por que gastar Bing e aba
    # People (dezenas de segundos) para escolher a mesma resposta no fim.
    if page_html:
        exact = _exact_from_company_page(page_html)
        if exact:
            return exact
        if not allow_network:
            band = _find_in_text(page_html)
            if band:
                band["source"] = "linkedin_direct"
            return band
    if not allow_network:
        return None

    jobs = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        if linkedin_url:
            if page_html is None:
                jobs["page"] = pool.submit(_try_url, linkedin_url, 14)
            jobs["people"] = pool.submit(_fetch_people_tab_count, linkedin_url)
            jobs["bing"] = pool.submit(_bing_count, linkedin_url)

        results = {}
        for key, future in jobs.items():
            try:
                results[key] = future.result(timeout=16)
            except Exception:
                results[key] = None

    page_text = page_html if page_html is not None else results.get("page")

    # 1. Contagem exata na página pública da empresa (fonte canônica)
    if page_text:
        exact = _exact_from_company_page(page_text)
        if exact:
            return exact

    # 2. Aba People
    if results.get("people"):
        return results["people"]

    # 3. Faixa declarada na página principal ("5.001-10.000 funcionários")
    if page_text:
        result = _find_in_text(page_text)
        if result:
            result["source"] = "linkedin_direct"
            return result

    # 4. Snapshot do Bing
    if results.get("bing"):
        return results["bing"]

    # 5. Site da empresa — só se o LinkedIn não respondeu nada
    if website_url:
        return _website_count(website_url)

    return None
