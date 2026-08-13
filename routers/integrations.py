"""
Fases 4–5 da proposta v3: push para CRM (webhook assinado) e insights de IA.
Tudo env-gated — sem as chaves, os endpoints respondem 503 e o produto
segue funcionando.
"""
import os
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from models.database import get_db, Lead, Activity, CRMConnection
from middleware.auth import get_current_user
from services import ai_insights, crypto
from services.crm import webhook as crm_webhook
from services.providers import hunter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["integrations"])


def _get_user_lead(db: Session, lead_id: int, user_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    return lead


def _user_webhook(db: Session, user_id: str) -> CRMConnection | None:
    """Conexão webhook ativa configurada pelo próprio usuário (Configurações)."""
    conn = (
        db.query(CRMConnection)
        .filter(
            CRMConnection.user_id == user_id,
            CRMConnection.provider == "webhook",
            CRMConnection.is_active.is_(True),
        )
        .first()
    )
    return conn if conn and conn.webhook_url else None


@router.get("/integrations/status")
def integrations_status(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Permite à UI mostrar/ocultar ações conforme o que está configurado."""
    user_id = current_user.get("sub")
    return {
        "ai": ai_insights.is_configured(),
        "crm_webhook": crm_webhook.is_configured() or bool(_user_webhook(db, user_id)),
        "hunter": hunter.is_configured(),
    }


@router.post("/leads/{lead_id}/ai-summary")
def ai_summary(
    lead_id: int,
    force: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Resumo executivo pré-ligação. Cacheado no lead; force=true regenera."""
    if not ai_insights.is_configured():
        raise HTTPException(status_code=503, detail="IA não configurada (ANTHROPIC_API_KEY ausente).")

    user_id = current_user.get("sub")
    lead = _get_user_lead(db, lead_id, user_id)
    if lead.ai_summary and not force:
        return {"summary": lead.ai_summary, "cached": True}

    summary = ai_insights.generate_summary(lead, list(lead.decision_makers))
    if not summary:
        raise HTTPException(status_code=502, detail="Falha ao gerar resumo. Tente novamente.")

    lead.ai_summary = summary
    db.commit()
    return {"summary": summary, "cached": False}


@router.post("/leads/{lead_id}/push")
def push_to_crm(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Envia o lead completo (decisores + atividades) ao webhook CRM configurado.

    A conexão do próprio usuário (Configurações) tem precedência; o webhook
    global via env segue funcionando como fallback.
    """
    user_id = current_user.get("sub")
    conn = _user_webhook(db, user_id)
    if not conn and not crm_webhook.is_configured():
        raise HTTPException(
            status_code=503,
            detail="CRM não configurado. Adicione seu webhook em Configurações.",
        )

    # Segredo gravado que não abre com a SECRETS_KEY atual: recusamos em vez de
    # enviar sem assinatura. O receptor que valida o HMAC descartaria em
    # silêncio, e o que não valida passaria a aceitar payload de qualquer
    # origem — nos dois casos a proteção teria sumido sem ninguém notar.
    if conn and conn.webhook_secret == crypto.ILEGIVEL:
        raise HTTPException(
            status_code=409,
            detail="O segredo do webhook não pôde ser lido com a chave atual do "
                   "servidor. Regrave-o em Configurações para voltar a enviar.",
        )

    lead = _get_user_lead(db, lead_id, user_id)
    activities = (
        db.query(Activity)
        .filter(Activity.lead_id == lead.id)
        .order_by(Activity.created_at.asc())
        .all()
    )

    result = crm_webhook.push_lead(
        lead,
        list(lead.decision_makers),
        activities,
        url=conn.webhook_url if conn else None,
        secret=conn.webhook_secret if conn else None,
    )
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=f"Falha no envio ao CRM ({result['error']}).")

    # Trilha de auditoria: registra o envio como nota na timeline
    db.add(Activity(
        lead_id=lead.id, user_id=user_id, type="note",
        notes="Lead enviado ao CRM via webhook.",
    ))
    db.commit()
    return {"success": True, "status_code": result["status_code"]}
