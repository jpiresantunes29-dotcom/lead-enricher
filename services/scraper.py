import logging
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ._utils import (
    HEADERS, normalize_domain, tld_to_region, safe_get,
    jsonld_organization, linkedin_from_sameas, looks_like_bot_wall,
    is_public_linkedin_slug, LINKEDIN_PAGE_RE, fix_response_encoding,
)
from .phone_normalizer import pick_best_phone, extract_and_normalize_phones
from .providers.cnpj_receita import extract_cnpj

logger = logging.getLogger(__name__)
TIMEOUT = int(os.getenv("SCRAPING_TIMEOUT", "15"))

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)


# Páginas internas (/sobre, /contato) são complemento: não podem custar o mesmo
# tempo da home. Sem esse teto, um site pesado sozinho estourava o limite da
# função serverless (60 s na Vercel).
INNER_PAGE_TIMEOUT = int(os.getenv("SCRAPING_INNER_TIMEOUT", "6"))
INNER_PAGES_BUDGET = int(os.getenv("SCRAPING_INNER_BUDGET", "10"))


# Códigos que significam "o site viu um robô e recusou", não "o site caiu".
# 401 fica de fora: é área logada, e coletar por trás de login nunca foi a
# proposta — não há bloqueio a reportar ali.
_BLOQUEIO_HTTP = {403: "http_403", 429: "http_429", 451: "http_451"}


def _fetch_com_motivo(url: str, timeout: Optional[int] = None) -> tuple:
    """
    Devolve ((soup, html), None) ou (None, motivo).

    O motivo distingue as duas maneiras de não ter conteúdo, que davam na
    mesma ficha vazia: o site não respondeu (motivo None — pode ser DNS, TLS,
    timeout, 5xx) ou o site respondeu recusando o robô (`http_403`,
    `bot_wall`). Só a segunda vira aviso na tela.

    Bloqueia alvos de rede interna (anti-SSRF) — na URL de entrada e em cada
    redirect, via `safe_get`. O domínio de partida vem do usuário, então a
    cadeia inteira é entrada não confiável, não só o primeiro elo.
    """
    try:
        # (connect, read): um servidor lento não pode segurar a busca inteira
        resp = safe_get(url, headers=HEADERS, timeout=(5, timeout or TIMEOUT))
        if resp is None:
            return None, None
        motivo = _BLOQUEIO_HTTP.get(resp.status_code)
        if motivo:
            logger.info("Coleta recusada pelo site url=%s status=%s", url, resp.status_code)
            return None, motivo
        resp.raise_for_status()
        fix_response_encoding(resp)
        if looks_like_bot_wall(resp.text):
            # Site respondeu 200 mas o corpo é um desafio anti-bot (Akamai/
            # Cloudflare), não a página real — tratar como falha de fetch em
            # vez de extrair o título do desafio como se fosse a empresa.
            logger.info("Bot wall detected url=%s", url)
            return None, "bot_wall"
        return (BeautifulSoup(resp.text, "html.parser"), resp.text), None
    except Exception as e:
        logger.debug("Fetch failed url=%s: %s", url, e)
        return None, None


def _fetch(url: str, timeout: Optional[int] = None) -> Optional[tuple]:
    """Devolve (soup, html) ou None. Para quem não precisa saber o motivo."""
    fetched, _ = _fetch_com_motivo(url, timeout)
    return fetched


def _localizacao_plausivel(valor: Optional[str]) -> bool:
    """
    A localização declarada pelo site está em alfabeto latino?

    As metatags `geo.region`/`geo.placename` são das mais copiadas junto com o
    template e das menos revisadas: boticario.com.br declara
    "г.Симферополь, АР Крым" (Simferopol, Crimeia) — sobra do tema que
    originou o site. Não é ambiguidade de fonte, é lixo, e lixo não deve
    entrar na ficha nem como último recurso.

    Acento não atrapalha: NFKD separa o diacrítico da letra, então "São Paulo"
    e "Paraná" continuam contando como latinos.
    """
    if not valor:
        return False
    letras = [c for c in unicodedata.normalize("NFKD", valor) if c.isalpha()]
    if not letras:
        return False
    latinas = sum(1 for c in letras if "a" <= c.lower() <= "z")
    return latinas / len(letras) >= 0.8


def _geo_do_site(soup: BeautifulSoup) -> Optional[str]:
    """
    Localização das metatags geo.*, descartada quando não é plausível.

    A mesma checagem roda de novo na saída de `scrape_website`, porque o
    JSON-LD — que vem antes desta fonte — carrega o mesmo tipo de lixo.
    """
    valor = _meta(soup, "geo.region") or _meta(soup, "geo.placename")
    return valor if _localizacao_plausivel(valor) else None


def _meta(soup: BeautifulSoup, name: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
    if tag:
        return tag.get("content", "").strip() or None
    return None


def _usable_linkedin(href: str) -> Optional[str]:
    """
    Normaliza a URL e devolve None quando ela exige login.

    Empresa que cola no rodapé o link do próprio painel
    (/company/<id>/admin) faria a ficha inteira vir vazia: quem chama
    gravaria essa URL e o enricher pularia a busca pela página pública.
    """
    match = LINKEDIN_PAGE_RE.search(href)
    if not match or not is_public_linkedin_slug(match.group("slug")):
        return None
    return match.group(0).rstrip("/")


def _extract_linkedin(soup: BeautifulSoup, text: str) -> Optional[str]:
    """Extrai link LinkedIn priorizando footer/header (social links da própria empresa)."""
    # 1. Tentar dentro de <footer> ou <header> primeiro
    for container_name in ("footer", "header"):
        container = soup.find(container_name)
        if container:
            for a in container.find_all("a", href=True):
                usable = _usable_linkedin(a["href"])
                if usable:
                    return usable
    # 2. Qualquer âncora no documento
    for a in soup.find_all("a", href=True):
        usable = _usable_linkedin(a["href"])
        if usable:
            return usable
    # 3. Fallback regex no texto
    for match in LINKEDIN_PAGE_RE.finditer(text):
        if is_public_linkedin_slug(match.group("slug")):
            return match.group(0).rstrip("/")
    return None


def _looks_like_slogan(name: str) -> bool:
    """
    'Somos incansáveis pra você não precisar ser' não é nome de empresa.
    Frase longa/com verbo no title é slogan — usar isso como razão social
    polui a ficha inteira do lead.
    """
    if not name:
        return True
    words = name.split()
    if len(words) > 5 or len(name) > 45:
        return True
    lowered = name.lower()
    return any(marker in lowered for marker in (
        " que ", " para ", " pra ", " com ", " sua ", " seu ", " você ", " nós ",
        "somos ", "a melhor", "o melhor", "bem-vindo", "bem vindo", "home",
    ))


def _pick_company_name(soup: BeautifulSoup, org: dict, domain: str) -> Optional[str]:
    """
    Nome da empresa por ordem de confiabilidade. O <title> vem por último
    porque é onde mora o slogan; se ele também não servir, o domínio
    capitalizado é melhor palpite do que uma frase de marketing.
    """
    candidates = [
        org.get("name"),
        _meta(soup, "og:site_name"),
        _meta(soup, "application-name"),
        _meta(soup, "og:title"),
        soup.title.string.strip() if soup.title and soup.title.string else None,
    ]
    for raw in candidates:
        if not raw:
            continue
        # Só corta em separador cercado de espaço: "Acme | Gestão" vira "Acme",
        # mas "Acme-Tech" continua inteiro.
        name = re.sub(r"\s+[\|\-–—:•·]\s*.*$", "", str(raw)).strip(" ,\t")
        # Ponto final de frase sai; ponto de sigla ("TOTVS S.A.") fica.
        if name.endswith(".") and not re.search(r"\b[A-Za-z]\.[A-Za-z]?\.$", name):
            name = name[:-1].strip()
        if name and not _looks_like_slogan(name):
            return name

    root = domain.split(".")[0].replace("-", " ")
    return root.title() if root else None


# Caixas de contato que servem como e-mail público da empresa
_CONTACT_PREFIXES = ("contato", "contact", "comercial", "vendas", "sales", "info", "atendimento")
_NOISE_EMAIL_PARTS = ("noreply", "no-reply", "naoresponda", "example.com", "sentry", "wixpress", "@2x")


def _is_noise_email(email: str) -> bool:
    return any(part in email for part in _NOISE_EMAIL_PARTS)


def _pick_corporate_email(emails, domain: str) -> Optional[str]:
    """E-mail público de contato da empresa: prioriza caixa institucional do domínio."""
    clean = [e for e in sorted(emails) if not _is_noise_email(e)]
    own = [e for e in clean if e.endswith("@" + domain)]
    for prefix in _CONTACT_PREFIXES:
        for e in own:
            if e.split("@", 1)[0].startswith(prefix):
                return e
    return own[0] if own else (clean[0] if clean else None)


def _has_personal_email(emails, domain: str) -> bool:
    """Há algum e-mail nominal (não institucional) capaz de ensinar o padrão?"""
    from .people.email_patterns import is_generic

    return any(
        e.endswith("@" + domain) and not _is_noise_email(e) and not is_generic(e)
        for e in emails
    )


# Palavras que denunciam página institucional ou legal — é nelas que o CNPJ
# aparece quando não está no rodapé da home. São PALAVRAS, e não caminhos,
# porque o que varia entre sites é justamente o caminho: /pt/politica (Madero),
# /aviso-de-privacidade/ (PUCPR) e /a-universidade/sobre-a-pucpr/ não têm nada
# em comum além deste vocabulário.
_PISTAS_INSTITUCIONAIS = (
    "privacidade", "privacy", "termos", "terms", "lgpd", "cookies",
    "institucional", "quem-somos", "quem somos", "sobre", "about",
    "empresa", "contato", "contact", "transparencia", "transparência",
    "politica", "política", "juridico", "jurídico", "regulamento",
)

# Teto de páginas seguidas. Cada uma é uma requisição a mais dentro do
# orçamento da coleta, e passar disso troca latência por um ganho que rareia.
_MAX_LINKS_INSTITUCIONAIS = 8


def _links_institucionais(base_url: str, soup: BeautifulSoup, domain: str) -> list:
    """
    Páginas institucionais/legais que o PRÓPRIO site publica, no mesmo domínio.

    Adivinhar caminhos (/sobre, /contato, /privacidade) só funciona em site que
    segue a convenção, e os que mais interessam não seguem: medido ao vivo,
    nenhum dos caminhos adivinhados existe em pucpr.br, e o CNPJ do Madero mora
    em /pt/politica. Seguir o que o site linka dispensa a convenção.

    Só o mesmo domínio: link para fora leva ao CNPJ de outra empresa — agência,
    parceiro, gateway de pagamento — e gravaria a ficha do lead errado.
    """
    encontrados = []
    base_normal = base_url.rstrip("/")
    for a in soup.find_all("a", href=True):
        texto = a.get_text(" ", strip=True).lower()
        href = a["href"]
        if not any(p in texto or p in href.lower() for p in _PISTAS_INSTITUCIONAIS):
            continue
        try:
            full = urljoin(base_url, href).split("#")[0]
        except ValueError:
            continue
        if not full.startswith(("http://", "https://")):
            continue
        if normalize_domain(urlparse(full).netloc) != domain:
            continue
        if full.rstrip("/") == base_normal or full in encontrados:
            continue
        encontrados.append(full)
        if len(encontrados) >= _MAX_LINKS_INSTITUCIONAIS:
            break
    return encontrados


def _cnpj_seguindo_links_do_site(base_url: str, soup: BeautifulSoup,
                                 domain: str) -> Optional[str]:
    """
    Segunda tentativa de achar o CNPJ, pelas páginas que o site declara.

    O CNPJ é a chave que abre localização, setor e porte OFICIAIS na Receita —
    campos que, sem ele, ficam vazios na ficha mesmo quando o site está no ar.
    Por isso vale uma rodada extra de requisições só por ele.
    """
    urls = _links_institucionais(base_url, soup, domain)
    if not urls:
        return None
    deadline = time.monotonic() + INNER_PAGES_BUDGET
    with ThreadPoolExecutor(max_workers=min(len(urls), 6)) as pool:
        futures = [pool.submit(_fetch, u, INNER_PAGE_TIMEOUT) for u in urls]
        for future in futures:
            restante = deadline - time.monotonic()
            if restante <= 0:
                break
            try:
                fetched = future.result(timeout=restante)
            except Exception:
                continue
            if not fetched:
                continue
            achado = extract_cnpj(fetched[0].get_text(" ", strip=True))
            if achado:
                logger.info("CNPJ achado seguindo link do site domain=%s", domain)
                return achado
    return None


def scrape_website(url: str) -> dict:
    """Scrapes a company homepage and returns extracted fields."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    fetched, bloqueio = _fetch_com_motivo(url)
    if not fetched:
        motivo = bloqueio
        # `blocked` e `failed` levam ao mesmo lugar aqui (nenhum campo
        # coletado), mas só o primeiro tem uma explicação para dar ao vendedor.
        return {
            "status": "blocked" if motivo else "failed",
            "block_reason": motivo,
            "raw_input_url": url,
        }
    soup, html = fetched

    # Detecta região para parsing de telefone
    domain = normalize_domain(url)
    region = tld_to_region(domain, default="BR")

    page_text = soup.get_text(separator=" ", strip=True)
    org = jsonld_organization(soup)

    company_name = _pick_company_name(soup, org, domain)

    description = (
        org.get("description")
        or _meta(soup, "og:description")
        or _meta(soup, "description")
    )
    if description:
        description = description[:500]

    location = None
    addr = org.get("address")
    if isinstance(addr, dict):
        location = addr.get("addressLocality") or addr.get("addressRegion")
    elif isinstance(addr, str):
        location = addr
    if not location:
        loc_obj = org.get("location")
        if isinstance(loc_obj, dict):
            location = loc_obj.get("name")
    if not location:
        location = _geo_do_site(soup)

    # Guarda TODOS os e-mails do domínio: cada um pode ensinar o padrão da
    # empresa (services/people/email_patterns.py), o que zera o custo dos
    # próximos contatos daquele domínio.
    all_emails = {e.lower() for e in EMAIL_REGEX.findall(page_text)}
    corporate_email = _pick_corporate_email(all_emails, domain)

    # CNPJ costuma viver no rodapé — porta de entrada para os dados públicos
    # da Receita (sócios, telefone e e-mail oficiais).
    cnpj = extract_cnpj(page_text)

    # Telefone via libphonenumber (substitui regex frouxo)
    phone = pick_best_phone(page_text, html=html, default_region=region)
    all_phones = extract_and_normalize_phones(page_text, default_region=region)

    # sameAs (JSON-LD) é o sinal mais confiável: a empresa o publica para SEO
    # e ele existe mesmo quando o ícone de rodapé é montado via JS, que o
    # requests não executa. Só cai para a busca no HTML se ele faltar.
    same_as = linkedin_from_sameas(org)
    linkedin_url = (
        (_usable_linkedin(same_as) if same_as else None)
        or _extract_linkedin(soup, page_text)
    )

    sector = org.get("industry") or _meta(soup, "article:section")

    employee_count_raw = None
    emp = org.get("numberOfEmployees")
    if emp:
        employee_count_raw = str(emp.get("value", emp)) if isinstance(emp, dict) else str(emp)

    # Páginas internas (contato/sobre) em paralelo. Vale a pena mesmo com os
    # campos principais preenchidos: é onde moram e-mails nominais e o CNPJ.
    needs_more = (
        not location or not phone or not linkedin_url or not cnpj
        or not _has_personal_email(all_emails, domain)
    )
    if needs_more:
        base = url.rstrip("/")
        # As páginas legais entram porque é nelas que o CNPJ costuma aparecer
        # quando não está no rodapé — e o CNPJ é a porta para localização,
        # setor e porte oficiais da Receita.
        suffixes = ["/contato", "/sobre", "/about", "/contact", "/quem-somos", "/empresa",
                    "/politica-de-privacidade", "/privacidade", "/termos"]
        deadline = time.monotonic() + INNER_PAGES_BUDGET
        with ThreadPoolExecutor(max_workers=len(suffixes)) as pool:
            futures = {
                pool.submit(_fetch_com_motivo, base + s, INNER_PAGE_TIMEOUT): s
                for s in suffixes
            }
            for fut in as_completed(futures, timeout=None):
                if time.monotonic() > deadline:
                    break
                try:
                    result, motivo_pagina = fut.result(
                        timeout=max(0.1, deadline - time.monotonic()))
                except Exception:
                    continue
                if not result:
                    # /sobre e /contato são de onde saem setor, localização e
                    # CNPJ. Home aberta e páginas internas recusando o robô é
                    # o caso comum no varejo grande — e sem registrar isso a
                    # ficha volta vazia sem dizer por quê.
                    bloqueio = bloqueio or motivo_pagina
                    continue
                about_soup, about_html = result
                about_text = about_soup.get_text(separator=" ", strip=True)
                all_emails.update(e.lower() for e in EMAIL_REGEX.findall(about_text))
                if not cnpj:
                    cnpj = extract_cnpj(about_text)
                if not phone:
                    phone = pick_best_phone(about_text, html=about_html, default_region=region)
                if not all_phones:
                    all_phones = extract_and_normalize_phones(about_text, default_region=region)
                if not linkedin_url:
                    linkedin_url = _extract_linkedin(about_soup, about_text)
                if not location:
                    location = _geo_do_site(about_soup)
        corporate_email = corporate_email or _pick_corporate_email(all_emails, domain)

    # Os caminhos adivinhados acima cobrem o site que segue a convenção.
    # Quando não cobrem, ainda resta o que o próprio site linka — e é o
    # CNPJ que decide se a ficha terá localização e setor oficiais.
    if not cnpj:
        cnpj = _cnpj_seguindo_links_do_site(url, soup, domain)

    # Guarda na saída, e não só na metatag: em boticario.com.br o
    # "г.Симферополь, АР Крым" vem do JSON-LD (addressLocality), que é a
    # PRIMEIRA fonte de localização — o template foi copiado inteiro, dados
    # estruturados junto. Onde nasce muda; que é lixo, não.
    if not _localizacao_plausivel(location):
        location = None

    return {
        # A home abriu, então houve coleta — mas se as páginas internas
        # recusaram o robô, o motivo vai junto: é o que explica os campos que
        # ficaram vazios.
        "status": "enriched",
        "block_reason": bloqueio,
        "raw_input_url": url,
        "company_name": company_name,
        "website": url,
        "linkedin_url": linkedin_url,
        "sector": sector,
        "employee_count": employee_count_raw,
        "location": location,
        "description": description,
        "corporate_email": corporate_email,
        "phone": phone,
        "cnpj": cnpj,
        "emails": sorted(e for e in all_emails if e.endswith("@" + domain))[:25],
        "phones": all_phones[:5],
    }
