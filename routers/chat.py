"""
Chat de IA sobre o pipeline (Fase 5a). Só leitura: a rota não grava nada.

Env-gated como o resumo da ficha: sem GROQ_API_KEY responde 503 e o resto do
produto segue funcionando.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, rate_limit_key
from models.database import get_db
from models.schemas import ChatRequest
from services import crm_chat

router = APIRouter(prefix="/api", tags=["chat"])
limiter = Limiter(key_func=rate_limit_key)


@router.post("/chat")
@limiter.limit("20/minute")
def chat(
    request: Request,
    body: ChatRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not crm_chat.is_configured():
        raise HTTPException(status_code=503, detail="IA não configurada (GROQ_API_KEY ausente).")
    resultado = crm_chat.responder(
        db,
        current_user.get("sub"),
        body.pergunta.strip(),
        [t.model_dump() for t in body.historico],
        body.mencionados,
    )
    if resultado is None:
        raise HTTPException(status_code=502, detail="A IA não respondeu. Tente de novo em instantes.")
    return resultado
