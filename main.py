import logging
import logging.config
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session

from models.database import get_db, init_db, schema_status
from middleware.auth import (
    jwt_configured,
    rate_limit_key,
    secret_confere_com_projeto,
    verificacao_por_jwks,
)
from routers import enrichment, leads, auth, export, activities, dashboard, integrations, crm_config
from routers import batch, extension, internal, privacy
from routers import imports, sheet
from routers import wa as wa_router
from routers import wa_sandbox as wa_sandbox_router
from routers import dns_intel as dns_intel_router
from routers import seo as seo_router
from services import guides, seo
from services.people import optout

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

# Ambiente: "production" fecha a documentação interativa e faz o app gritar
# quando o schema não sobe. Em dev tudo fica aberto para inspeção.
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# ── Rate limiter (por usuário JWT; fallback IP) ───────────────────────────────
limiter = Limiter(key_func=rate_limit_key, default_limits=["60/minute"])


def _check_configuration() -> None:
    """
    Confere no boot o que, faltando, quebra o app de forma silenciosa e cara.
    Em produção é falha dura: subir sem poder autenticar ninguém é pior do que
    não subir.
    """
    # Chave pública publicada pelo projeto já basta: o segredo legado só entra
    # quando não há JWKS nenhum.
    if not verificacao_por_jwks():
        if not jwt_configured():
            message = (
                "Sem chave para verificar login: o projeto não publica JWKS e "
                "SUPABASE_JWT_SECRET está ausente ou curto demais (as rotas "
                "autenticadas respondem 503)."
            )
            if IS_PRODUCTION:
                raise RuntimeError(message)
            logger.warning(message)
        elif secret_confere_com_projeto() is False:
            # Segredo presente e do projeto errado é o pior dos mundos: o boot
            # passa, o login termina bem e o app volta deslogado sem erro nenhum.
            logger.error(
                "SUPABASE_JWT_SECRET não confere com o projeto Supabase do frontend: "
                "todo login será recusado com 401. Copie o JWT Secret do mesmo "
                "projeto da anon key em static/js/app.js."
            )
        else:
            logger.warning(
                "O projeto Supabase não publica chave pública (JWKS): o login só "
                "funciona enquanto ele assinar com o JWT Secret legado. Se a chave "
                "de assinatura atual for um segredo compartilhado, todo login será "
                "recusado — troque-a por uma assimétrica (ECC P-256)."
            )

    _check_whatsapp_configuration()


def _check_whatsapp_configuration() -> None:
    """
    Meia configuração do WhatsApp é pior do que nenhuma.

    Com envio ligado e `WHATSAPP_APP_SECRET` ausente, o produto manda o convite
    (que a Meta cobra) e depois recusa toda entrega do webhook por falta de
    assinatura — recusa correta, mas o efeito prático é o lead responder e
    ninguém ficar sabendo. Convite pago, resposta perdida, e nada disso aparece
    como erro em lugar nenhum.

    Em produção é falha dura: quem configurou metade quase certamente esqueceu
    o resto, e descobrir isso pelo silêncio custa leads reais.
    """
    from services.wa import client as wa_client, webhook as wa_webhook

    if not wa_client.is_configured():
        return
    if wa_webhook.is_configured():
        return

    message = (
        "WhatsApp configurado para ENVIAR mas sem WHATSAPP_APP_SECRET: o "
        "convite sai e é cobrado, e a resposta do lead é recusada no webhook "
        "por falta de assinatura."
    )
    if IS_PRODUCTION:
        raise RuntimeError(message)
    logger.warning("%s (em produção isto impediria o boot)", message)


def _log_prontidao() -> None:
    """
    Escreve no log do boot o que está pendente, com a consequência de cada item.

    Roda **depois** do `init_db()` porque a conferência de schema precisa do
    banco de pé. É informativo: o que impede o boot já falhou antes daqui; o
    resto fica registrado para quem for ler o log do deploy.
    """
    try:
        from services import preflight
        relatorio = preflight.verificar(producao=IS_PRODUCTION)
        if relatorio.achados:
            logger.info("Conferência de prontidão:\n%s",
                        preflight.resumo_para_log(relatorio))
    except Exception:
        # Conferência é diagnóstico: se ela quebrar, o app continua de pé.
        logger.warning("Não foi possível rodar a conferência de prontidão.",
                       exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LeadEnricher starting up (env=%s).", APP_ENV)
    _check_configuration()
    try:
        init_db()
    except Exception:
        # Em produção, schema quebrado significa app inútil: melhor falhar no
        # boot (a Vercel mostra o erro no deploy) do que responder 500 em cada
        # clique do usuário. Em dev, seguimos com o aviso para não travar quem
        # está sem banco configurado.
        logger.exception("init_db() falhou.")
        if IS_PRODUCTION:
            raise
    _log_prontidao()
    yield
    logger.info("LeadEnricher shutting down.")


app = FastAPI(
    title="LeadEnricher",
    description="Inteligência comercial automatizada — descubra empresas em segundos.",
    version="2.1.0",
    lifespan=lifespan,
    # Documentação interativa é mapa da API para quem quiser sondá-la; em
    # produção ela não agrega nada ao usuário final.
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Cabeçalhos de segurança ───────────────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    """
    Defesas de navegador que valem para toda resposta. Sem elas, um único
    upload/HTML refletido vira execução de script no domínio da aplicação.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"
    )
    if request.url.scheme == "https" or IS_PRODUCTION:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


# CORS restrito à extensão de navegador. O site é servido pela mesma origem e
# não precisa disso; liberar geral aqui exporia a API a qualquer página web.
#
# O ID de uma extensão Chrome é sempre 32 letras de 'a' a 'p'. Com
# EXTENSION_IDS definido (lista separada por vírgula), só as nossas passam —
# é o que se deve usar depois de publicar na Web Store.
_extension_ids = [i.strip() for i in os.getenv("EXTENSION_IDS", "").split(",") if i.strip()]
if _extension_ids:
    _origin_regex = "^chrome-extension://(" + "|".join(_extension_ids) + ")$"
else:
    _origin_regex = r"^chrome-extension://[a-p]{32}$"

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_origin_regex,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(enrichment.router)
app.include_router(leads.router)
app.include_router(dns_intel_router.router)
app.include_router(auth.router)
app.include_router(export.router)
app.include_router(activities.router)
app.include_router(dashboard.router)
app.include_router(integrations.router)
app.include_router(crm_config.router)
app.include_router(batch.router)
app.include_router(extension.router)
app.include_router(internal.router)
app.include_router(privacy.router)
app.include_router(seo_router.router)
# Importação de planilha e a visão de grade sobre os leads importados.
app.include_router(imports.router)
app.include_router(sheet.router)
app.include_router(wa_router.router)
app.include_router(wa_sandbox_router.router)


@app.get("/health", tags=["meta"])
def health():
    """
    Sinal de vida com verificação de schema: banco incompleto vira "degraded"
    aqui em vez de 500 no clique do usuário. O detalhe do que falta só sai
    fora de produção — em produção seria mapa da estrutura interna.
    """
    schema = schema_status()
    payload = {
        "status": "ok" if schema["ok"] else "degraded",
        "version": app.version,
        "schema_ok": schema["ok"],
    }
    if not IS_PRODUCTION:
        payload["schema"] = schema
    return payload


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # Landing "Gold Signal" (docs/DESIGN_LANDING_V3.md). O produto vive em /app.
    return templates.TemplateResponse(
        request, "landing.html", {**seo.context(request, "/"), "faq": seo.FAQ}
    )


@app.get("/app", response_class=HTMLResponse)
def app_page(request: Request):
    # Rota canônica do produto — o redirect do login aponta para cá.
    # Área logada não tem o que indexar: noindex no header e na meta tag.
    return templates.TemplateResponse(
        request,
        "index.html",
        seo.context(request, "/app"),
        headers={"X-Robots-Tag": "noindex, nofollow"},
    )


# ── Conteúdo indexável (/guias) ──────────────────────────────────────────────
@app.get("/guias", response_class=HTMLResponse, include_in_schema=False)
def guias_index(request: Request):
    return templates.TemplateResponse(
        request,
        "guias.html",
        {
            **seo.context(request, "/guias"),
            "guides": guides.GUIDES,
            "index_title": guides.INDEX_TITLE,
        },
    )


@app.get("/guias/{slug}", response_class=HTMLResponse, include_in_schema=False)
def guia(request: Request, slug: str):
    guide = guides.get(slug)
    if guide is None:
        raise HTTPException(status_code=404, detail="Guia não encontrado")
    return templates.TemplateResponse(
        request,
        "guia.html",
        {
            **seo.context(request, guide.path),
            "guide": guide,
            "related": guides.related_of(guide),
        },
    )


# ── Páginas institucionais (confiança/LGPD) ──────────────────────────────────
@app.get("/termos", response_class=HTMLResponse, include_in_schema=False)
def termos(request: Request):
    return templates.TemplateResponse(request, "termos.html", seo.context(request, "/termos"))


@app.get("/privacidade", response_class=HTMLResponse, include_in_schema=False)
def privacidade(request: Request):
    return templates.TemplateResponse(
        request, "privacidade.html", seo.context(request, "/privacidade")
    )


@app.get("/seguranca", response_class=HTMLResponse, include_in_schema=False)
def seguranca(request: Request):
    return templates.TemplateResponse(
        request, "seguranca.html", seo.context(request, "/seguranca")
    )


@app.get("/remover-meus-dados", response_class=HTMLResponse, include_in_schema=False)
def remover_meus_dados(request: Request):
    # Direito de oposição/eliminação (LGPD art. 18). Público e sem cadastro:
    # exigir conta para exercer um direito seria barreira indevida.
    return templates.TemplateResponse(
        request, "remover-dados.html", seo.context(request, "/remover-meus-dados")
    )


@app.get("/privacidade/confirmar", response_class=HTMLResponse, include_in_schema=False)
def confirmar_remocao(request: Request, token: str = "", db: Session = Depends(get_db)):
    """
    Confirma o pedido de remoção pelo link enviado por e-mail e executa a
    exclusão. É aqui — e só aqui — que o pedido público passa a valer.
    """
    result = optout.confirm_request(db, token)
    db.commit()

    if result:
        kind, removed = result
        logger.info("Opt-out confirmado kind=%s registros_removidos=%d", kind, removed)

    return templates.TemplateResponse(
        request,
        "remover-dados-confirmado.html",
        {**seo.context(request, "/privacidade/confirmar"), "confirmado": bool(result)},
        # Página de uso único atrás de token: fora do índice, sempre.
        headers={"X-Robots-Tag": "noindex, nofollow"},
    )
