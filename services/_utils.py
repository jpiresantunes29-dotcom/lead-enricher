"""
Helpers compartilhados entre os services do LeadEnricher.

Centraliza:
  - HEADERS HTTP padrão (User-Agent, Accept-Language) para evitar duplicação
  - normalize_domain() para limpar input do usuário
  - tld_to_region() para inferir região default do número de telefone
  - LINKEDIN_COMPANY_RE para extrair slug de URLs do LinkedIn
  - is_public_host() / is_public_url() — guard anti-SSRF para URLs derivadas
    de input do usuário
  - safe_get() — o mesmo guard aplicado a CADA salto de um redirect, que é
    onde a checagem só da URL inicial deixa passar
  - jsonld_organization() / linkedin_from_sameas() — dado estruturado que a
    própria empresa publica (schema.org), fonte de LinkedIn mais confiável
    que um link solto no body e que não depende de busca externa
  - looks_like_bot_wall() — detecta página de desafio anti-bot (Akamai/
    Cloudflare/PerimeterX) para não confundi-la com o conteúdo real do site
"""
import ipaddress
import json
import logging
import re
import socket
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


LINKEDIN_COMPANY_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/([^\s\"'<>?#/]+)",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Caminhos do LinkedIn que só o dono da página acessa. Empresa que cola o
# link do próprio painel no rodapé do site (acontece bastante) faria a ficha
# inteira falhar: /company/<id>/admin redireciona para /uas/login e não
# devolve setor, sede nem funcionários.
_PRIVATE_LINKEDIN_SLUGS = {"admin", "login", "setup", "unavailable"}


def is_public_linkedin_slug(slug: str) -> bool:
    """
    O slug leva a uma página pública de empresa, ou a uma que exige login?

    Slug só de dígitos é o ID interno do LinkedIn — /company/74031250 cai na
    tela de login mesmo sem /admin, enquanto /company/farmatex-do-brasil
    devolve a ficha completa. Distinguir os dois é o que separa uma ficha
    cheia de uma vazia.
    """
    slug = (slug or "").strip().strip("/").lower()
    if not slug:
        return False
    if slug in _PRIVATE_LINKEDIN_SLUGS:
        return False
    return not slug.isdigit()


def normalize_domain(value: str) -> str:
    """Remove protocolo, path, query string e prefixo www."""
    value = (value or "").strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/")[0].split("?")[0]
    if value.startswith("www."):
        value = value[4:]
    return value


# Mapa TLD → região default ISO-3166 alpha-2 (para libphonenumber)
TLD_REGION_MAP = {
    "br": "BR",
    "pt": "PT",
    "ar": "AR",
    "cl": "CL",
    "co": "CO",
    "mx": "MX",
    "uy": "UY",
    "pe": "PE",
    "us": "US",
    "uk": "GB",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "nl": "NL",
    "ca": "CA",
    "au": "AU",
    "jp": "JP",
}


def tld_to_region(domain: str, default: str = "BR") -> str:
    """Infere a região do telefone a partir do TLD do domínio."""
    domain = normalize_domain(domain)
    parts = domain.split(".")
    if len(parts) >= 2:
        tld = parts[-1]
        return TLD_REGION_MAP.get(tld, default)
    return default


def is_public_host(host: str) -> bool:
    """
    Guard anti-SSRF: resolve o host e garante que NENHUM dos IPs cai em
    faixa privada, loopback, link-local ou reservada.

    Retorna False para hosts que não resolvem (não há o que buscar) ou que
    apontam para rede interna (ex.: localhost, 10.x, 169.254.x — metadata).
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    ips = {info[4][0] for info in infos}
    if not ips:
        return False
    for raw in ips:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def is_public_url(url: str) -> bool:
    """Aplica is_public_host() ao hostname de uma URL http(s)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return is_public_host(parsed.hostname)


#: Redirects seguidos antes de desistir. O mesmo teto que o `requests` usa por
#: padrão — cadeia mais longa que isso é laço ou armadilha, não navegação.
MAX_REDIRECTS = 30


def safe_get(url: str, *, headers: Optional[dict] = None, timeout=None,
             stream: bool = False, session: Optional[object] = None):
    """
    GET que aplica `is_public_url()` na URL inicial **e em cada redirect**.

    `requests.get(..., allow_redirects=True)` valida o que você mandou e depois
    segue para onde o servidor apontar, sem perguntar de novo. Isso reabre por
    dentro o buraco que a checagem inicial fecha: um domínio público responde
    302 para `http://169.254.169.254/` (metadata da nuvem) ou para `10.0.0.5`,
    e o fetch vai — porque o alvo interno nunca passou por validação nenhuma.
    O host de partida é do usuário, então a cadeia inteira é atacável.

    Por isso os redirects são seguidos à mão: cada `Location` vira uma URL
    absoluta, passa pelo mesmo guard e só então é buscada.

    Devolve a `Response` final, ou `None` se qualquer salto for barrado, a
    cadeia estourar o teto ou a rede falhar. `None` é o mesmo sinal que quem
    chama já tratava como "não deu" — nenhum chamador precisa saber por quê.
    """
    getter = getattr(session, "get", None) or requests.get
    atual = url
    for _ in range(MAX_REDIRECTS):
        if not is_public_url(atual):
            logger.warning("Blocked non-public fetch target url=%s", atual)
            return None
        try:
            resp = getter(atual, headers=headers, timeout=timeout,
                          allow_redirects=False, stream=stream)
        except Exception as e:
            logger.debug("Fetch failed url=%s: %s", atual, e)
            return None

        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp

        destino = resp.headers.get("location")
        if not destino:
            # Redirect sem destino: nada a seguir, devolve como veio para quem
            # chamou decidir (raise_for_status tratará o status estranho).
            return resp
        # Fecha o corpo do salto intermediário antes de abrir o próximo —
        # com stream=True a conexão ficaria pendurada até o GC.
        try:
            resp.close()
        except Exception:
            pass
        atual = urljoin(atual, destino)

    logger.warning("Redirect chain too long url=%s", url)
    return None


def jsonld_organization(soup: BeautifulSoup) -> dict:
    """
    Primeiro bloco JSON-LD Organization/Corporation/LocalBusiness da página.

    Dado que a própria empresa publica para SEO — cobre sameAs (redes
    sociais), endereço e nº de funcionários mesmo quando não aparecem
    visíveis no HTML (ex.: ícone de rodapé montado via JS, que o requests
    não executa).
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            t = data.get("@type", "")
            if any(x in t for x in ["Organization", "Corporation", "LocalBusiness"]):
                return data
        except Exception:
            continue
    return {}


def linkedin_from_sameas(org: dict) -> Optional[str]:
    """Extrai a URL do LinkedIn do campo sameAs (schema.org) de um Organization."""
    same_as = (org or {}).get("sameAs")
    if not same_as:
        return None
    candidates = same_as if isinstance(same_as, list) else [same_as]
    for url in candidates:
        if isinstance(url, str) and "linkedin.com/company/" in url:
            return url.split("?")[0].rstrip("/")
    return None


# Trechos característicos de páginas de desafio anti-bot (Akamai Bot Manager,
# Cloudflare, PerimeterX...). Sem esta checagem, o scraper trata a página de
# bloqueio como conteúdo real e salva o título do desafio — ex. "Pardon Our
# Interruption" — como se fosse o nome da empresa.
_BOT_WALL_MARKERS = (
    "pardon our interruption",
    "just a moment",
    "attention required! | cloudflare",
    "checking if the site connection is secure",
    "verify you are a human",
    "verifying you are human",
    "enable javascript and cookies to continue",
    "px-captcha",
    "making sure you're not a bot",
    "making sure you&#39;re not a bot",
)


def looks_like_bot_wall(html: str) -> bool:
    """Heurística: a resposta é um desafio anti-bot, não o conteúdo real da página."""
    if not html:
        return False
    snippet = html[:3000].lower()
    return any(marker in snippet for marker in _BOT_WALL_MARKERS)


def fix_response_encoding(resp) -> None:
    """
    Corrige o charset quando o servidor não declara nenhum.

    Sem `charset` no Content-Type, o requests assume ISO-8859-1 (é o que o
    HTTP manda), mas site brasileiro quase sempre é UTF-8 — e aí todo acento
    é decodificado errado, calado: "ONDUNORTE – Um novo tempo!" vira lixo, e
    com ele o nome da empresa, a descrição, o setor e a cidade. Não levanta
    exceção porque ISO-8859-1 aceita qualquer byte; o erro só aparece na
    ficha do vendedor.
    """
    declared = (resp.headers.get("content-type") or "").lower()
    if "charset=" in declared:
        return
    apparent = resp.apparent_encoding
    if apparent and (resp.encoding or "").lower() != apparent.lower():
        resp.encoding = apparent


def looks_like_search_block(html: str) -> bool:
    """
    A resposta de um buscador é bloqueio/captcha em vez de resultados?

    Mesmo critério de services._ddg._is_blocked() — página curta demais ou
    com pouco texto visível para o tamanho do HTML. Existe aqui como função
    pública para que services.employee_count (módulo irmão) possa usá-la sem
    importar o módulo de busca.
    """
    if not html or len(html) < 5000:
        return True
    text_ratio = len(BeautifulSoup(html, "html.parser").get_text()) / max(len(html), 1)
    return text_ratio < 0.04
