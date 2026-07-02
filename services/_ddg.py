"""
Parser robusto multi-engine para busca de URLs (especialmente LinkedIn).

Engines em cascata:
  1. DuckDuckGo HTML (POST + form) — preferida
  2. Bing
  3. SearXNG instâncias públicas (JSON API)
  4. Google (regex genérico no HTML)

Estratégia anti-block: extração via regex (não depende de seletores CSS frágeis).
Para cada URL, tenta extrair título do contexto próximo no HTML.
"""
import re
import json
import requests
from urllib.parse import quote_plus, unquote, urlparse
from bs4 import BeautifulSoup
from typing import List, Dict


# Headers mais "humanos" (Firefox 121, completos)
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# DDG embeda URL real em ?uddg=...
DDG_REDIRECT_RE = re.compile(r"/l/\?(?:.*?&)?uddg=([^&\"]+)")

# Instâncias públicas SearXNG conhecidas (JSON API)
SEARXNG_INSTANCES = [
    "https://search.disroot.org",
    "https://paulgo.io",
    "https://baresearch.org",
    "https://search.inetol.net",
    "https://priv.au",
]


def _is_blocked(html: str) -> bool:
    """Heurística simples: páginas muito curtas ou só JS são bloqueio."""
    if len(html) < 5000:
        return True
    # Páginas de captcha tendem a ter pouco conteúdo de texto
    text_ratio = len(BeautifulSoup(html, "html.parser").get_text()) / max(len(html), 1)
    return text_ratio < 0.04


def _extract_url_results(html: str, target_pattern: str = r"linkedin\.com/(?:in|company)/[a-zA-Z0-9\-._%]+") -> List[Dict]:
    """
    Extrai URLs que combinam com target_pattern do HTML, junto com texto próximo
    como título/snippet (dentro do mesmo elemento âncora).
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    pat = re.compile(rf"https?://(?:[a-z]{{2,3}}\.)?{target_pattern}", re.IGNORECASE)

    # 1) Âncoras com href válido
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Decodificar redirects DDG
        m = DDG_REDIRECT_RE.search(href)
        if m:
            href = unquote(m.group(1))
        # Decodificar redirects Google /url?q=
        if href.startswith("/url?"):
            qm = re.search(r"[?&]q=([^&]+)", href)
            if qm:
                href = unquote(qm.group(1))
        # Decodificar redirects Bing /click
        if "bing.com/ck/a" in href.lower():
            qm = re.search(r"[?&]u=a1([^&]+)", href)
            if qm:
                try:
                    import base64
                    href = base64.b64decode(qm.group(1) + "==").decode("utf-8", errors="ignore")
                except Exception:
                    pass

        if not pat.search(href):
            continue
        # Normalizar: pega só a parte que bate com o pattern
        full_match = pat.search(href)
        if not full_match:
            continue
        url = full_match.group(0)
        if url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True) or ""
        # Snippet: contexto do parent
        parent = a.find_parent(["div", "li", "section"]) or a
        snippet = parent.get_text(" ", strip=True)[:300] if parent else ""
        results.append({"url": url, "title": title, "snippet": snippet})

    # 2) Fallback: regex em texto bruto (captcha dribble)
    for m in pat.finditer(html):
        url = m.group(0)
        if url not in seen:
            seen.add(url)
            results.append({"url": url, "title": "", "snippet": ""})

    return results


def _try_engine(name: str, url: str, method: str = "GET", data=None,
                pattern: str = r"linkedin\.com/(?:in|company)/[a-zA-Z0-9\-._%]+",
                timeout: int = 12) -> List[Dict]:
    """Roda um engine HTTP e extrai resultados via regex."""
    try:
        if method == "POST":
            resp = requests.post(url, headers=SEARCH_HEADERS, data=data, timeout=timeout)
        else:
            resp = requests.get(url, headers=SEARCH_HEADERS, timeout=timeout)
        if resp.status_code not in (200, 202):
            return []
        if _is_blocked(resp.text):
            return []
        return _extract_url_results(resp.text, target_pattern=pattern)
    except Exception:
        return []


def _try_searxng(query: str, pattern: str, timeout: int = 12) -> List[Dict]:
    """Tenta instâncias SearXNG via JSON API."""
    for instance in SEARXNG_INSTANCES:
        try:
            url = f"{instance}/search"
            resp = requests.get(url, params={"q": query, "format": "json"},
                                headers=SEARCH_HEADERS, timeout=timeout)
            if resp.status_code != 200:
                continue
            ct = resp.headers.get("content-type", "")
            if "application/json" not in ct:
                continue
            data = resp.json()
            results = []
            pat = re.compile(rf"https?://(?:[a-z]{{2,3}}\.)?{pattern}", re.IGNORECASE)
            for item in data.get("results", [])[:30]:
                u = item.get("url", "")
                if pat.search(u):
                    results.append({
                        "url": pat.search(u).group(0),
                        "title": item.get("title", ""),
                        "snippet": item.get("content", ""),
                    })
            if results:
                return results
        except Exception:
            continue
    return []


def search_ddg(query: str, timeout: int = 12) -> List[Dict]:
    """Busca DuckDuckGo HTML com POST (mais resiliente)."""
    return _try_engine(
        "ddg",
        "https://html.duckduckgo.com/html/",
        method="POST",
        data={"q": query, "b": "", "kl": "us-en"},
        timeout=timeout,
    )


def search_bing(query: str, timeout: int = 12) -> List[Dict]:
    """Busca Bing."""
    return _try_engine(
        "bing",
        f"https://www.bing.com/search?q={quote_plus(query)}",
        timeout=timeout,
    )


def search_google(query: str, timeout: int = 12) -> List[Dict]:
    """Busca Google (frequentemente bloqueada, mas extrai via regex genérico)."""
    return _try_engine(
        "google",
        f"https://www.google.com/search?q={quote_plus(query)}&num=20",
        timeout=timeout,
    )


def search_multi(query: str, pattern: str = r"linkedin\.com/(?:in|company)/[a-zA-Z0-9\-._%]+",
                 timeout: int = 12) -> List[Dict]:
    """
    Busca em múltiplas engines em sequência. Devolve resultados deduplicados.
    Engines tentadas: SearXNG (mais estável) → DDG → Bing → Google.
    """
    seen = set()
    deduped = []

    for engine_func in (
        lambda: _try_searxng(query, pattern, timeout=timeout),
        lambda: search_ddg(query, timeout=timeout),
        lambda: search_bing(query, timeout=timeout),
        lambda: search_google(query, timeout=timeout),
    ):
        try:
            results = engine_func()
        except Exception:
            results = []
        for r in results:
            u = (r.get("url") or "").lower().split("#")[0]
            if u and u not in seen:
                seen.add(u)
                deduped.append(r)
        if len(deduped) >= 5:
            break

    return deduped
