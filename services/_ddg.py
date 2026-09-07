"""
Parser robusto multi-engine para busca de URLs (especialmente LinkedIn).

Engines, todas disparadas EM PARALELO (não em cascata): SearXNG (instâncias
públicas), Mojeek, DuckDuckGo HTML, Bing, Google. Rodar em paralelo em vez de
esperar cada uma falhar em sequência é o que garante o mesmo orçamento de
tempo cobrindo cinco tentativas em vez de uma — motor bloqueado hoje não
consome o tempo dos outros.

Estratégia anti-block: extração via regex (não depende de seletores CSS frágeis).
Para cada URL, tenta extrair título do contexto próximo no HTML.
"""
import re
import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from urllib.parse import quote_plus, unquote, urlparse
from bs4 import BeautifulSoup
from typing import Dict, List, Optional


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

# Slug do LinkedIn: qualquer caractere até o próximo delimitador de URL/HTML.
# Um allowlist (ex.: [a-zA-Z0-9\-._%]) trunca razões sociais com caractere
# especial — "c&a_brasil" virava só "c" — então aqui é blocklist.
LINKEDIN_SLUG_PATTERN = r"linkedin\.com/(?:in|company)/[^\s\"'<>?#/]+"

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


def _extract_url_results(html: str, target_pattern: str = LINKEDIN_SLUG_PATTERN) -> List[Dict]:
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
                pattern: str = LINKEDIN_SLUG_PATTERN,
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
    """
    Tenta instâncias SearXNG via JSON API.

    O tempo total é o mesmo `timeout` do engine, não `timeout` por instância:
    cinco instâncias fora do ar em sequência custariam um minuto sozinhas.
    """
    deadline = time.monotonic() + timeout
    per_instance = max(3, timeout // 2)
    for instance in SEARXNG_INSTANCES:
        if time.monotonic() >= deadline:
            break
        instance_timeout = min(per_instance, max(3, int(deadline - time.monotonic())))
        try:
            url = f"{instance}/search"
            resp = requests.get(url, params={"q": query, "format": "json"},
                                headers=SEARCH_HEADERS, timeout=instance_timeout)
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


def search_ddg(query: str, pattern: str = LINKEDIN_SLUG_PATTERN, timeout: int = 12) -> List[Dict]:
    """Busca DuckDuckGo HTML com POST (mais resiliente)."""
    return _try_engine(
        "ddg",
        "https://html.duckduckgo.com/html/",
        method="POST",
        data={"q": query, "b": "", "kl": "us-en"},
        pattern=pattern,
        timeout=timeout,
    )


def search_bing(query: str, pattern: str = LINKEDIN_SLUG_PATTERN, timeout: int = 12) -> List[Dict]:
    """Busca Bing."""
    return _try_engine(
        "bing",
        f"https://www.bing.com/search?q={quote_plus(query)}",
        pattern=pattern,
        timeout=timeout,
    )


def search_google(query: str, pattern: str = LINKEDIN_SLUG_PATTERN, timeout: int = 12) -> List[Dict]:
    """Busca Google (frequentemente bloqueada, mas extrai via regex genérico)."""
    return _try_engine(
        "google",
        f"https://www.google.com/search?q={quote_plus(query)}&num=20",
        pattern=pattern,
        timeout=timeout,
    )


def search_mojeek(query: str, pattern: str = LINKEDIN_SLUG_PATTERN, timeout: int = 12) -> List[Dict]:
    """
    Busca no Mojeek — índice próprio (não é proxy de Google/Bing), HTML simples
    sem desafio JS. Motor pequeno, mas é exatamente por isso que ainda não
    entrou na guerra de bloqueio a scraping que pegou DDG/Bing/Google.
    """
    return _try_engine(
        "mojeek",
        f"https://www.mojeek.com/search?q={quote_plus(query)}",
        pattern=pattern,
        timeout=timeout,
    )


def search_multi(query: str, pattern: str = LINKEDIN_SLUG_PATTERN,
                 timeout: int = 12, budget: Optional[float] = None) -> List[Dict]:
    """
    Busca em múltiplas engines EM PARALELO. Devolve resultados deduplicados.
    Engines: SearXNG (instâncias públicas), Mojeek, DDG, Bing, Google.

    Antes disto rodava em cascata (um motor de cada vez, na ordem acima) — um
    motor bloqueado ou lento consumia o orçamento inteiro antes do próximo
    sequer começar. Em paralelo, o custo total é o do motor mais lento que
    responde dentro do orçamento, não a soma de todos — o mesmo tempo agora
    cobre cinco tentativas em vez de uma ou duas.

    `budget` (ou `timeout`, na ausência dele) limita o tempo TOTAL da busca.
    Sem isso, motores lentos somam mais tempo do que a função serverless tem
    para responder. Quem chama sabe quanto tempo ainda resta; aqui só
    respeitamos.
    """
    total_budget = budget if budget else timeout
    deadline = time.monotonic() + total_budget
    engine_timeout = max(3, int(total_budget))

    engines = (
        ("searxng", lambda t: _try_searxng(query, pattern, timeout=t)),
        ("mojeek", lambda t: search_mojeek(query, pattern, timeout=t)),
        ("ddg", lambda t: search_ddg(query, pattern, timeout=t)),
        ("bing", lambda t: search_bing(query, pattern, timeout=t)),
        ("google", lambda t: search_google(query, pattern, timeout=t)),
    )

    seen = set()
    deduped = []
    # Pool solto (sem `with`): queremos parar de ESPERAR assim que tivermos
    # resultado suficiente ou o orçamento acabar, sem bloquear até que todo
    # motor (inclusive um travado) termine — as threads restantes só são
    # dispensadas em segundo plano (mesmo padrão de services.enricher para o
    # lookup de CNPJ em paralelo).
    pool = ThreadPoolExecutor(max_workers=len(engines))
    try:
        futures = {pool.submit(fn, engine_timeout): name for name, fn in engines}
        remaining = max(1.0, deadline - time.monotonic())
        try:
            for future in as_completed(futures, timeout=remaining):
                try:
                    results = future.result()
                except Exception:
                    results = []
                for r in results:
                    u = (r.get("url") or "").lower().split("#")[0]
                    if u and u not in seen:
                        seen.add(u)
                        deduped.append(r)
                if len(deduped) >= 5:
                    break
        except FuturesTimeoutError:
            pass  # orçamento esgotado — devolve o que já chegou
    finally:
        pool.shutdown(wait=False)

    return deduped
