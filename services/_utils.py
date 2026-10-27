"""
Helpers compartilhados entre os services do LeadEnricher.

Centraliza:
  - HEADERS HTTP padrão (User-Agent, Accept-Language) para evitar duplicação
  - normalize_domain() para limpar input do usuário
  - tld_to_region() para inferir região default do número de telefone
  - LINKEDIN_PAGE_RE / linkedin_ref() para extrair tipo e slug de URLs de
    organização do LinkedIn (/company/ e /school/)
  - domain_mentioned() — o domínio aparece no texto como domínio inteiro, e não
    como pedaço de um subdomínio de outra entidade
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
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# Páginas de organização no LinkedIn.
#
# Universidade, faculdade e escola vivem em /school/, não em /company/ — e é
# essa a página institucional delas. Enxergar só /company/ não deixava o campo
# vazio: fazia a coleta DESCARTAR o perfil correto (que costuma estar no rodapé
# da home) e aceitar em seguida um /company/ de sub-marca achado numa página
# interna. Caso real: pucpr.br publica
# /school/pontificia-universidade-catolica-do-parana na home, e a ficha vinha
# com /company/hotmilk-pucpr — o hub de inovação, não a universidade.
#
# O prefixo aceita `www.`, o de país (`br.`, `pt.`) e o móvel (`m.`): é o
# mesmo perfil em qualquer um deles, e quem grava a URL a remonta na forma
# canônica (`linkedin_page_url`).
LINKEDIN_PAGE_RE = re.compile(
    r"https?://(?:[a-z]{1,3}\.)?linkedin\.com/(?P<kind>company|school)/(?P<slug>[^\s\"'<>?#/]+)",
    re.IGNORECASE,
)


def linkedin_ref(url: str) -> Optional[tuple]:
    """
    `(kind, slug)` de uma URL de organização no LinkedIn, ou None.

    Devolver o tipo junto com o slug é o que permite remontar a URL no caminho
    certo: `/school/<slug>` e `/company/<slug>` são páginas diferentes, e trocar
    um pelo outro leva a 404.
    """
    match = LINKEDIN_PAGE_RE.search(url or "")
    if not match:
        return None
    return match.group("kind").lower(), match.group("slug").lower()


def linkedin_page_url(kind: str, slug: str) -> str:
    """URL canônica da página de organização (`company` ou `school`)."""
    return f"https://www.linkedin.com/{kind}/{slug}"


# Rodapé de site feito por agência traz o crédito dela — "Desenvolvido por
# Agência X" com o ícone do LinkedIn DA AGÊNCIA. Pelo lugar onde está (o
# rodapé, a origem de link mais confiável), esse link ganhava do da própria
# empresa e ia para a ficha.
_CREDITO_RE = re.compile(
    r"desenvolvid[oa] por|developed by|criad[oa] por|created by|made by|"
    r"feito por|powered by|site por|site by|design por|designed by|"
    r"produzid[oa] por|ag[eê]ncia",
    re.IGNORECASE,
)


def is_credit_anchor(anchor) -> bool:
    """
    A âncora é o crédito de quem fez o site, e não um link da empresa?

    Olha o texto da âncora e do bloco imediatamente em volta, com teto de
    tamanho: um rodapé inteiro sempre contém "agência" em algum lugar, e aí
    todo link dele seria descartado.
    """
    for node in (anchor, getattr(anchor, "parent", None)):
        if node is None or not hasattr(node, "get_text"):
            continue
        texto = node.get_text(" ", strip=True)
        if texto and len(texto) <= 160 and _CREDITO_RE.search(texto):
            return True
        for attr in ("title", "aria-label"):
            valor = node.get(attr) if hasattr(node, "get") else None
            if isinstance(valor, str) and _CREDITO_RE.search(valor):
                return True
    return False

# Conjunto completo de headers de um Chrome real (Windows), não só o
# User-Agent. WAFs mais simples (mod_security, Cloudflare em modo básico,
# Sucuri) não olham fingerprint de TLS — barram pela ausência de headers que
# todo navegador manda e nenhum script manda por padrão (Sec-Fetch-*,
# Accept completo, Client Hints). Isso não passa por Akamai/Cloudflare em
# modo agressivo com desafio JS (ex.: magazineluiza.com.br, que barra com
# HTTP 403 antes mesmo do HTML — ver looks_like_bot_wall para quando o
# desafio vem com 200), mas reduz o falso-positivo nos WAFs mais comuns.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
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
    """
    Remove protocolo, path, query string, porta, credencial, ponto final e
    prefixo www.

    Porta e ponto final parecem detalhe, mas chegam de verdade da planilha e
    da extensão ("empresa.com.br:443", "empresa.com.br." copiado de um dig):
    sem limpá-los, a consulta de MX ia para um nome que não existe e a ficha
    dizia "sem e-mail" para uma empresa com Google Workspace.
    """
    value = (value or "").strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/")[0].split("?")[0].split("#")[0]
    value = value.rsplit("@", 1)[-1]
    value = value.split(":")[0].rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    return value


# Sufixos públicos de dois níveis. Sem eles, "mx.skymail.net.br" virava o
# domínio "net.br" — o sufixo, não o provedor —, e "vendas.empresa.com.br" não
# chegava a "empresa.com.br". A lista do .br cobre as categorias que aparecem
# em base de prospecção; não é a Public Suffix List inteira de propósito (ela
# pesa e muda), só o que muda o resultado aqui.
TWO_LEVEL_SUFFIXES = frozenset({
    "com.br", "net.br", "org.br", "adv.br", "eng.br", "ind.br", "srv.br",
    "psc.br", "med.br", "esp.br", "tur.br", "agr.br", "art.br", "inf.br",
    "gov.br", "edu.br", "mil.br", "jus.br", "leg.br", "mp.br", "coop.br",
    "emp.br", "app.br", "dev.br", "log.br", "blog.br", "ong.br", "eco.br",
    "imb.br", "rec.br", "tv.br", "vet.br", "odo.br", "adm.br", "arq.br",
    "cnt.br", "eti.br", "far.br", "fot.br", "jor.br", "pro.br", "psi.br",
    "not.br", "tmp.br", "seg.br", "bio.br", "etc.br", "radio.br",
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk",
    "com.mx", "com.ar", "com.co", "com.pe", "com.uy", "com.py", "com.bo",
    "com.ec", "com.ve", "com.au", "net.au", "edu.au", "co.nz", "co.za",
    "co.jp", "co.in", "com.pt", "com.es", "com.tr", "com.cn", "com.hk",
    "com.sg",
})


def registrable_domain(host: str) -> str:
    """
    Domínio registrável (o "organizacional") de um host:
    'vendas.empresa.com.br' → 'empresa.com.br', 'mx.a.locaweb.com.br' →
    'locaweb.com.br'.

    É a régua para duas perguntas da coleta: se um redirecionamento ficou
    dentro da empresa (site oficial) e de quem é o MX quando o usuário digitou
    um subdomínio.
    """
    host = normalize_domain(host)
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    keep = 3 if ".".join(parts[-2:]) in TWO_LEVEL_SUFFIXES else 2
    return ".".join(parts[-keep:])


def host_matches(host: Optional[str], suffix: str) -> bool:
    """
    `host` é `suffix` ou um subdomínio dele — nunca um nome que só o contém.

    `"uol.com.br" in host` casava com "bauol.com.br"; `"google.com" in host`,
    com "google.com.evil.net". Para dizer quem opera um servidor, o que vale é
    o sufixo inteiro, rótulo por rótulo.
    """
    host = (host or "").strip().rstrip(".").lower()
    suffix = (suffix or "").strip().strip(".").lower()
    if not host or not suffix:
        return False
    return host == suffix or host.endswith("." + suffix)


# Hosts para onde um domínio redireciona sem que aquilo seja o site da
# empresa: estacionamento de domínio, construtor de página genérico, rede
# social. Domínio que só leva ao Instagram não tem site — e gravar
# "instagram.com" como site oficial seria pior do que deixar o do domínio.
_NOT_A_COMPANY_SITE = (
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "wa.me", "whatsapp.com", "linktr.ee",
    "sedoparking.com", "sedo.com", "hugedomains.com", "dan.com", "afternic.com",
    "godaddy.com", "parkingcrew.net", "bodis.com", "registro.br", "hostgator.com.br",
    "locaweb.com.br", "wix.com", "google.com",
)

# Parâmetros que só servem a quem mediu o clique. Não fazem parte do endereço
# da empresa e não devem viajar para a ficha nem para o CRM.
_TRACKING_PARAMS = re.compile(
    r"^(utm_[a-z]+|gclid|fbclid|msclkid|mc_[a-z]+|_ga|_gl|ref|trk|igshid|yclid|dclid)$",
    re.IGNORECASE,
)


def strip_tracking(url: str) -> str:
    """URL sem parâmetros de rastreio (utm_*, gclid, fbclid...) e sem âncora."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not _TRACKING_PARAMS.match(k)]
    return parsed._replace(query=urlencode(query), fragment="").geturl()


def canonical_site_url(final_url: Optional[str], domain: str) -> Optional[str]:
    """
    Endereço oficial do site a partir de onde a home REALMENTE terminou.

    `https://<domínio>` é o que o usuário digitou, não o que a empresa
    publica: o site pode morar em `www.`, só existir em http, ou o domínio
    pode redirecionar para a marca nova do grupo. O destino final do
    redirecionamento é a declaração da própria empresa sobre onde fica o site.

    Devolve só a origem (esquema + host): o caminho da home costuma ser idioma
    ou campanha ("/pt-br/", "/?utm_source=..."), não endereço. Devolve None
    quando o destino não é um site de empresa (estacionamento, rede social) —
    quem chama mantém o endereço do domínio.
    """
    if not final_url:
        return None
    try:
        parsed = urlparse(final_url)
    except ValueError:
        return None
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme not in ("http", "https") or not host:
        return None
    if registrable_domain(host) != registrable_domain(domain):
        if any(host_matches(host, bloqueado) for bloqueado in _NOT_A_COMPANY_SITE):
            return None
    return f"{parsed.scheme}://{host}"


def domain_mentioned(text: str, domain: str) -> bool:
    """
    O domínio aparece em `text` como domínio inteiro?

    `"pucpr.br" in html` parece uma checagem inocente e não é: casa com
    `hotmilk.pucpr.br`, com `naopucpr.br` e com qualquer outra entidade que
    apenas contenha o domínio buscado. Era assim que a página do hub de
    inovação da PUCPR passava como sendo a da universidade — e, num HTML de
    330 KB, quase tudo passava.

    As fronteiras recusam um rótulo colado antes (subdomínio) ou depois
    (domínio maior). O ponto que separa `hotmilk` de `pucpr.br` é justamente o
    que o lookbehind barra.

    À direita são dois lookaheads porque um ponto seguinte é ambíguo: em
    `pucpr.br.uk` ele continua o domínio (e aí não é o nosso), enquanto em
    "Acesse pucpr.br." ele só encerra a frase. O que decide é vir letra depois.
    """
    if not text or not domain:
        return False
    padrao = (r"(?<![a-z0-9.\-])" + re.escape(domain.lower())
              + r"(?![a-z0-9\-])(?!\.[a-z0-9])")
    return re.search(padrao, text.lower()) is not None


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
        if isinstance(url, str) and linkedin_ref(url):
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
