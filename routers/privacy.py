"""
Endpoints públicos de privacidade (LGPD art. 18).

Qualquer pessoa — com ou sem conta — pode pedir que seu contato profissional
seja removido e bloqueado permanentemente. Sem autenticação de propósito:
exigir cadastro para exercer um direito seria criar barreira indevida.

O pedido, porém, é confirmado por e-mail antes de valer. Sem essa etapa, o
formulário viraria a ferramenta mais barata de sabotagem contra a base: um
concorrente apagaria contatos de terceiros em lote, e o dado apagado não
volta. Confirmar prova que quem pediu tem acesso ao canal informado.

Proteções:
  - rate limit apertado (evita varredura da base)
  - resposta genérica: nunca revela se o dado existia (anti-enumeração)
  - só o hash do valor é guardado depois da confirmação
"""
import logging
import os

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from sqlalchemy.orm import Session

from middleware.auth import rate_limit_key
from models.database import get_db
from models.schemas import OptOutRequest
from services import mailer
from services.people import optout

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/privacidade", tags=["privacidade"])
limiter = Limiter(key_func=rate_limit_key)

_GENERIC_RESPONSE = {
    "success": True,
    "message": (
        "Pedido registrado. Enviamos um e-mail com um link de confirmação — "
        "assim que você abrir o link, qualquer dado associado a este contato "
        "será removido e ficará permanentemente bloqueado."
    ),
}

_CONFIRMATION_SUBJECT = "Confirme a remoção dos seus dados — LeadEnricher"

_CONFIRMATION_BODY = """Olá,

Recebemos um pedido para remover e bloquear este contato da base do
LeadEnricher ({alvo}).

Para confirmar, abra o link abaixo (válido por {horas} horas):

{link}

Se não foi você, ignore esta mensagem: sem a confirmação, nada é removido e
nenhum dado seu é alterado.

LeadEnricher — {site}
"""


def _site_url(request: Request) -> str:
    """Base pública do site, para montar o link de confirmação."""
    configured = (os.getenv("SITE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _destination(kind: str, value: str, contact_email: str) -> str:
    """
    Para onde vai o link de confirmação.

    Remoção de e-mail confirma no próprio e-mail — é o que prova titularidade.
    Telefone e LinkedIn não têm canal verificável, então o pedido exige um
    e-mail de contato: não prova a posse do dado, mas dá rastro e custo a quem
    tentar usar o formulário em massa.
    """
    if kind == "email":
        return (value or "").strip()
    return (contact_email or "").strip()


@router.post("/opt-out")
@limiter.limit("5/minute")
def request_opt_out(request: Request, body: OptOutRequest, db: Session = Depends(get_db)):
    """Abre um pedido de remoção. Só passa a valer depois da confirmação."""
    kind = (body.kind or "").strip().lower()
    if kind not in optout.KINDS:
        # Resposta genérica também aqui — não damos pistas sobre o formato aceito
        return _GENERIC_RESPONSE

    destination = _destination(kind, body.value, body.contact_email or "")
    if "@" not in destination:
        return _GENERIC_RESPONSE

    token = optout.create_request(
        db, kind, body.value, contact_email=destination, reason=body.reason,
    )
    db.commit()

    if token:
        link = f"{_site_url(request)}/privacidade/confirmar?token={token}"
        mailer.send(
            destination,
            _CONFIRMATION_SUBJECT,
            _CONFIRMATION_BODY.format(
                alvo={"email": "e-mail", "phone": "telefone", "linkedin": "perfil do LinkedIn"}[kind],
                horas=optout.TOKEN_TTL_HOURS,
                link=link,
                site=_site_url(request),
            ),
        )
        logger.info("Opt-out pendente kind=%s origem=form_publico", kind)
    else:
        # Já estava bloqueado: nada a confirmar. A resposta é a mesma para não
        # revelar que o contato existe (ou existia) na base.
        logger.info("Opt-out ignorado kind=%s motivo=ja_bloqueado", kind)

    return _GENERIC_RESPONSE
