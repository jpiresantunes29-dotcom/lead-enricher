"""Arquivos que os buscadores leem: robots.txt e sitemap.xml."""

from fastapi import APIRouter, Request, Response

from services import seo

router = APIRouter(tags=["meta"], include_in_schema=False)

# Áreas sem conteúdo indexável: produto logado, API, docs do FastAPI.
_DISALLOW = ("/app", "/api/", "/docs", "/redoc", "/openapi.json", "/health")


@router.get("/robots.txt", response_class=Response)
def robots(request: Request) -> Response:
    linhas = ["User-agent: *", "Allow: /"]
    linhas += [f"Disallow: {path}" for path in _DISALLOW]
    linhas += ["", f"Sitemap: {seo.absolute(request, '/sitemap.xml')}", ""]
    return Response("\n".join(linhas), media_type="text/plain; charset=utf-8")


@router.get("/sitemap.xml", response_class=Response)
def sitemap(request: Request) -> Response:
    base = seo.site_url(request)
    urls = "".join(
        "<url>"
        f"<loc>{base}{page.path}</loc>"
        f"<lastmod>{seo.last_modified(page)}</lastmod>"
        f"<changefreq>{page.changefreq}</changefreq>"
        f"<priority>{page.priority}</priority>"
        "</url>"
        for page in seo.indexable_pages()
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}"
        "</urlset>"
    )
    return Response(xml, media_type="application/xml")
