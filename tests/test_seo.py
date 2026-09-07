"""Testes de SEO: robots, sitemap, canonical, Open Graph e JSON-LD."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from main import app
from services import guides, seo

PUBLIC_PAGES = [
    "/", "/guias", "/termos", "/privacidade", "/seguranca", "/remover-meus-dados",
]
GUIDE_PAGES = [guide.path for guide in guides.GUIDES]
ALL_INDEXABLE = PUBLIC_PAGES + GUIDE_PAGES


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _meta(html: str, attr: str, value: str) -> str | None:
    """Extrai o `content` de uma meta tag (`name`/`property`)."""
    match = re.search(
        rf'<meta\s+(?:name|property)="{re.escape(value)}"\s+content="([^"]*)"',
        html,
    )
    return match.group(1) if match else None


def _json_ld(html: str) -> list[dict]:
    return [
        json.loads(block)
        for block in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', html, re.S
        )
    ]


# ── robots.txt ───────────────────────────────────────────────────────────────
def test_robots_txt(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "User-agent: *" in body
    assert "Disallow: /api/" in body
    assert "Disallow: /app" in body
    assert "Sitemap: http://testserver/sitemap.xml" in body


# ── sitemap.xml ──────────────────────────────────────────────────────────────
def test_sitemap_lists_public_pages_only(client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    body = resp.text
    for path in ALL_INDEXABLE:
        assert f"<loc>http://testserver{path}</loc>" in body
    assert "<loc>http://testserver/app</loc>" not in body
    assert body.count("<url>") == len(ALL_INDEXABLE)


def test_sitemap_uses_site_url_env(client, monkeypatch):
    monkeypatch.setenv("SITE_URL", "https://leadenricher.app/")
    body = client.get("/sitemap.xml").text
    assert "<loc>https://leadenricher.app/</loc>" in body
    assert "testserver" not in body


# ── canonical / meta por página ──────────────────────────────────────────────
@pytest.mark.parametrize("path", ALL_INDEXABLE)
def test_public_pages_have_canonical_and_description(client, path):
    html = client.get(path).text
    assert f'<link rel="canonical" href="http://testserver{path}" />' in html
    description = _meta(html, "name", "description")
    assert description and len(description) > 60
    assert "index, follow" in _meta(html, "name", "robots")


def test_page_titles_and_descriptions_are_unique(client):
    titles, descriptions = set(), set()
    for path in ALL_INDEXABLE:
        html = client.get(path).text
        titles.add(re.search(r"<title>(.*?)</title>", html, re.S).group(1))
        descriptions.add(_meta(html, "name", "description"))
    assert len(titles) == len(ALL_INDEXABLE)
    assert len(descriptions) == len(ALL_INDEXABLE)


def test_open_graph_and_twitter_cards(client):
    html = client.get("/").text
    assert _meta(html, "property", "og:url") == "http://testserver/"
    assert _meta(html, "property", "og:image") == "http://testserver/static/og-cover.png?v=1"
    assert _meta(html, "property", "og:locale") == "pt_BR"
    assert _meta(html, "name", "twitter:card") == "summary_large_image"
    # a imagem precisa existir de fato para o crawler não engasgar
    assert client.get("/static/og-cover.png").status_code == 200


# ── área logada não é indexável ──────────────────────────────────────────────
def test_app_page_is_noindex(client):
    resp = client.get("/app")
    assert "noindex" in resp.headers["x-robots-tag"]
    assert _meta(resp.text, "name", "robots") == "noindex, nofollow"
    assert "application/ld+json" not in resp.text


# ── dados estruturados ───────────────────────────────────────────────────────
def test_landing_structured_data(client):
    blocks = {block["@type"]: block for block in _json_ld(client.get("/").text)}
    assert {"Organization", "WebSite", "SoftwareApplication", "FAQPage"} <= blocks.keys()
    assert blocks["Organization"]["url"] == "http://testserver/"
    # Uma oferta, preço zero: o acesso é aberto. Um preço aqui apareceria no
    # resultado de busca prometendo uma cobrança que o produto não faz.
    assert [offer["price"] for offer in blocks["SoftwareApplication"]["offers"]] == ["0"]


def test_faq_html_and_structured_data_stay_in_sync(client):
    html = client.get("/").text
    blocks = {block["@type"]: block for block in _json_ld(html)}
    perguntas = [item["name"] for item in blocks["FAQPage"]["mainEntity"]]
    assert len(perguntas) == len(seo.FAQ)
    for pergunta in perguntas:
        assert f"<summary>{pergunta}</summary>" in html


@pytest.mark.parametrize("path", ["/termos", "/privacidade", "/seguranca"])
def test_legal_pages_have_breadcrumbs(client, path):
    blocks = _json_ld(client.get(path).text)
    assert len(blocks) == 1
    assert blocks[0]["@type"] == "BreadcrumbList"
    assert blocks[0]["itemListElement"][-1]["item"] == f"http://testserver{path}"


# ── conteúdo indexável (/guias) ──────────────────────────────────────────────
def test_guides_index_lists_every_guide(client):
    html = client.get("/guias").text
    for guide in guides.GUIDES:
        assert f'href="{guide.path}"' in html
        assert guide.title in html


@pytest.mark.parametrize("path", GUIDE_PAGES)
def test_guide_page_renders_content(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    html = resp.text
    # corpo real, não só o cabeçalho: heading interno + link de volta ao índice
    assert "<h2>" in html
    assert 'href="/guias"' in html
    # o corpo é HTML confiável e precisa sair renderizado, não escapado
    assert "&lt;h2&gt;" not in html


def test_unknown_guide_returns_404(client):
    assert client.get("/guias/nao-existe").status_code == 404


@pytest.mark.parametrize("path", GUIDE_PAGES)
def test_guide_structured_data(client, path):
    blocks = {block["@type"]: block for block in _json_ld(client.get(path).text)}
    assert {"Article", "BreadcrumbList", "Organization"} <= blocks.keys()
    article = blocks["Article"]
    assert article["url"] == f"http://testserver{path}"
    assert article["datePublished"] and article["dateModified"]
    # trilha completa: Início → Guias → guia
    assert len(blocks["BreadcrumbList"]["itemListElement"]) == 3


def test_guides_index_structured_data(client):
    blocks = {block["@type"]: block for block in _json_ld(client.get("/guias").text)}
    assert blocks["CollectionPage"]["hasPart"]
    assert len(blocks["CollectionPage"]["hasPart"]) == len(guides.GUIDES)


def test_guides_are_linked_from_the_landing(client):
    # sem link interno, o Google demora (ou deixa) de descobrir o conteúdo
    assert 'href="/guias"' in client.get("/").text


# ── fontes self-hosted ───────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ALL_INDEXABLE)
def test_pages_do_not_depend_on_google_fonts(client, path):
    html = client.get(path).text
    assert "fonts.googleapis.com" not in html
    assert "/static/fonts/fonts.css" in html


def test_self_hosted_fonts_are_served(client):
    css = client.get("/static/fonts/fonts.css")
    assert css.status_code == 200
    arquivos = set(re.findall(r"url\('(/static/fonts/[^']+\.woff2)'\)", css.text))
    assert arquivos
    for arquivo in arquivos:
        resp = client.get(arquivo)
        assert resp.status_code == 200, arquivo
        assert resp.content[:4] == b"wOF2", arquivo
