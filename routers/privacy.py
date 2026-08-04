"""
Endpoints públicos de privacidade (LGPD art. 18).

Qualquer pessoa — com ou sem conta — pode pedir que seu contato profissional
seja removido e bloqueado permanentemente. Sem autenticação de propósito:
exigir cadastro para exercer um direito seria criar barreira indevida.

Proteções:
  - rate limit apertado (evita varredura da base)
  - resposta genérica: nunca revela se o dado existia (anti-enumeração)
  - só o hash do valor é guardado
"""
import logging

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from sqlalchemy.orm import Session

from middleware.auth import rate_limit_key
from models.database import get_db
from models.schemas import OptOutRequest
from services.people import optout

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/privacidade", tags=["privacidade"])
limiter = Limiter(key_func=rate_limit_key)

_GENERIC_RESPONSE = {
    "success": True,
    "message": (
        "Pedido registrado. Se houver algum dado associado a este contato em "
        "nossa base, ele foi removido e ficará permanentemente bloqueado."
    ),
}


@router.post("/opt-out")
@limiter.limit("5/minute")
def request_opt_out(request: Request, body: OptOutRequest, db: Session = Depends(get_db)):
    """Remove e bloqueia um e-mail, telefone ou perfil do LinkedIn."""
    kind = (body.kind or "").strip().lower()
    if kind not in optout.KINDS:
        # Resposta genérica também aqui — não damos pistas sobre o formato aceito
        return _GENERIC_RESPONSE

    registered = optout.register(db, kind, body.value, reason=body.reason)
    removed = optout.purge(db, kind, body.value) if registered else 0
    db.commit()

    logger.info("Opt-out publico kind=%s registros_removidos=%d", kind, removed)
    return _GENERIC_RESPONSE
