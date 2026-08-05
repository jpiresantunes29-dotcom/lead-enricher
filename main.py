import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from models.database import init_db
from middleware.auth import rate_limit_key
from routers import (
    enrichment, leads, auth, billing, export, activities, dashboard,
    integrations, crm_config, imports, sheet,
)

# ── Logging estruturado ───────────────────────────────────────────────────────
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "root": {"level": "INFO", "handlers": ["console"]},
})

logger = logging.getLogger(__name__)

# ── Rate limiter (por usuário JWT; fallback IP) ───────────────────────────────
limiter = Limiter(key_func=rate_limit_key, default_limits=["60/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LeadEnricher starting up.")
    try:
        init_db()
    except Exception:
        # Não deixa uma falha de init_db (conectividade, permissão de DDL no
        # pooler etc.) derrubar o processo inteiro a cada cold start — o
        # runtime Python da Vercel engole exceções de lifespan sem traceback,
        # então logamos explicitamente para conseguir diagnosticar.
        logger.exception("init_db() falhou; app segue no ar sem garantir o schema.")
    yield
    logger.info("LeadEnricher shutting down.")


app = FastAPI(
    title="LeadEnricher",
    description="Inteligência comercial automatizada — descubra empresas em segundos.",
    version="2.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(enrichment.router)
app.include_router(leads.router)
app.include_router(auth.router)
app.include_router(billing.router)
app.include_router(export.router)
app.include_router(activities.router)
app.include_router(dashboard.router)
app.include_router(integrations.router)
app.include_router(crm_config.router)
app.include_router(imports.router)
app.include_router(sheet.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "version": app.version}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # Landing "Gold Signal" (docs/DESIGN_LANDING_V3.md). O produto vive em /app.
    return templates.TemplateResponse(request, "landing.html")


@app.get("/app", response_class=HTMLResponse)
def app_page(request: Request):
    # Rota canônica do produto — redirects de auth e billing apontam para cá.
    return templates.TemplateResponse(request, "index.html")


@app.get("/landing", include_in_schema=False)
def landing_alias():
    # Alias usado durante o desenvolvimento — a landing agora é a home.
    return RedirectResponse("/", status_code=308)


# ── Páginas institucionais (confiança/LGPD) ──────────────────────────────────
@app.get("/termos", response_class=HTMLResponse, include_in_schema=False)
def termos(request: Request):
    return templates.TemplateResponse(request, "termos.html")


@app.get("/privacidade", response_class=HTMLResponse, include_in_schema=False)
def privacidade(request: Request):
    return templates.TemplateResponse(request, "privacidade.html")


@app.get("/seguranca", response_class=HTMLResponse, include_in_schema=False)
def seguranca(request: Request):
    return templates.TemplateResponse(request, "seguranca.html")
