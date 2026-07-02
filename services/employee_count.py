"""
Conta funcionários por cascata multi-fonte:
  1. LinkedIn aba People (contagem exata "X employees on LinkedIn")
  2. LinkedIn público direto (página principal)
  3. Google Cache da página do LinkedIn
  4. Bing Cache (snapshot via search)
  5. Página /about ou /sobre do site da empresa

Normaliza para {raw, min, max, band, exact, source}.
O campo employee_count_linkedin armazena o valor exato obtido da aba People.
"""
import re
import requests
import json
from typing import Optional
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

from ._utils import HEADERS

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


def _to_int(s: str) -> Optional[int]:
    try:
        return int(re.sub(r"[,.\s]", "", s))
    except Exception:
        return None


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
        if lo and hi:
            return {"raw": raw, "min": lo, "max": hi, "band": _band_for(lo), "exact": None}

    # "10,000+"
    m = re.search(r"([\d,.]+)\+", raw)
    if m:
        lo = _to_int(m.group(1))
        if lo:
            return {"raw": raw, "min": lo, "max": None, "band": _band_for(lo), "exact": None}

    # número exato
    m = re.search(r"([\d,.]+)", raw)
    if m:
        n = _to_int(m.group(1))
        if n:
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
            if lo and hi:
                return {"raw": m.group(0), "min": lo, "max": hi, "band": _band_for(lo), "exact": None}
        else:
            n = normalize_employee_count(m.group(0))
            if n:
                return n
    return None


def _try_url(url: str, timeout: int = 10) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except Exception:
        pass
    return None


def _fetch_people_tab_count(linkedin_url: str) -> Optional[dict]:
    """
    Busca contagem exata de funcionários na aba People/Pessoas do LinkedIn.
    URL: linkedin.com/company/{slug}/people/
    Retorna {raw, min, max, band, exact, source: 'linkedin_people'} ou None.
    """
    slug_match = re.search(r"linkedin\.com/company/([a-zA-Z0-9\-._%]+)", linkedin_url)
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


def fetch_employee_count(linkedin_url: Optional[str], website_url: Optional[str] = None) -> Optional[dict]:
    """
    Cascata de fontes. Devolve {raw, min, max, band, exact, source} ou None.
    """
    if linkedin_url:
        # Fonte 1: aba People — contagem exata "X employees on LinkedIn"
        result = _fetch_people_tab_count(linkedin_url)
        if result:
            return result

        # Fonte 2: LinkedIn página principal
        text = _try_url(linkedin_url)
        if text:
            result = _find_in_text(text)
            if result:
                result["source"] = "linkedin_direct"
                return result

        # Fonte 3: Google Cache
        cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{linkedin_url}"
        text = _try_url(cache_url)
        if text:
            result = _find_in_text(text)
            if result:
                result["source"] = "google_cache"
                return result

        # Fonte 4: Bing snapshot via search
        slug_match = re.search(r"linkedin\.com/company/([a-zA-Z0-9\-._%]+)", linkedin_url)
        if slug_match:
            slug = slug_match.group(1)
            bing_url = f"https://www.bing.com/search?q={quote_plus(f'linkedin.com/company/{slug} employees')}"
            text = _try_url(bing_url)
            if text:
                result = _find_in_text(text)
                if result:
                    result["source"] = "bing_search"
                    return result

    # Fonte 5: /about do site
    if website_url:
        base = website_url.rstrip("/")
        for suffix in ["/about", "/sobre", "/quem-somos", "/about-us", "/empresa"]:
            text = _try_url(base + suffix)
            if text:
                result = _find_in_text(text)
                if result:
                    result["source"] = "company_website"
                    return result

    return None
