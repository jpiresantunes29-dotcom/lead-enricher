"""
Métricas comerciais agregadas (proposta v3 §6) — o "Power BI interno".
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from models.database import get_db, utcnow, Lead, Activity, HIDDEN_LEAD_STATUSES
from middleware.auth import get_current_user

router = APIRouter(prefix="/api", tags=["dashboard"])

_CONTACT_OUTCOMES = ("talked", "meeting_scheduled")


@router.get("/dashboard/metrics")
def dashboard_metrics(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    # Tudo em UTC com fuso explícito: as colunas de data são timezone-aware
    # (models.database.utcnow) e misturar naive com aware aqui trocava o
    # "atrasado" pelo "em dia" conforme o fuso do servidor.
    now = utcnow()
    cutoff = now - timedelta(days=days)

    leads_q = db.query(Lead).filter(Lead.user_id == user_id, Lead.created_at >= cutoff)
    leads_pesquisados = leads_q.count()

    calls_q = db.query(Activity).filter(
        Activity.user_id == user_id,
        Activity.type == "call",
        Activity.created_at >= cutoff,
    )
    ligacoes = calls_q.count()
    contatos = calls_q.filter(Activity.outcome.in_(_CONTACT_OUTCOMES)).count()
    reunioes = calls_q.filter(Activity.outcome == "meeting_scheduled").count()

    funil = dict(
        db.query(Lead.stage, func.count(Lead.id))
        .filter(Lead.user_id == user_id, Lead.status.notin_(HIDDEN_LEAD_STATUSES))
        .group_by(Lead.stage)
        .all()
    )

    pendentes_q = db.query(Activity).filter(
        Activity.user_id == user_id,
        Activity.due_at.isnot(None),
        Activity.completed_at.is_(None),
    )
    followups_pendentes = pendentes_q.count()
    followups_atrasados = pendentes_q.filter(Activity.due_at < now).count()

    oportunidades = sum(funil.get(s, 0) for s in ("oportunidade", "ganho"))

    return {
        "period_days": days,
        "leads_pesquisados": leads_pesquisados,
        "ligacoes_realizadas": ligacoes,
        "taxa_contato": round(contatos / ligacoes, 3) if ligacoes else 0.0,
        "taxa_reuniao": round(reunioes / ligacoes, 3) if ligacoes else 0.0,
        "conversao_oportunidade": round(oportunidades / leads_pesquisados, 3) if leads_pesquisados else 0.0,
        "funil_por_estagio": funil,
        "followups_pendentes": followups_pendentes,
        "followups_atrasados": followups_atrasados,
    }
