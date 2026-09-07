"""
Camada de execução comercial (proposta v3 §5):
registro de ligações com regras automáticas, follow-ups e convites .ics.
"""
from datetime import datetime, UTC, date
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from models.database import get_db, Lead, Activity
from models.schemas import ActivityCreate, ActivityUpdate, ActivityOut, ActivityResponse
from services.activity_rules import (
    apply_rules, build_ics, ACTIVITY_TYPES, CALL_OUTCOMES,
)
from middleware.auth import get_current_user

router = APIRouter(prefix="/api", tags=["activities"])


def _get_user_lead(db: Session, lead_id: int, user_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    return lead


def _get_user_activity(db: Session, activity_id: int, user_id: str) -> Activity:
    act = db.query(Activity).filter(
        Activity.id == activity_id, Activity.user_id == user_id
    ).first()
    if not act:
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    return act


@router.post("/leads/{lead_id}/activities", response_model=ActivityResponse)
def create_activity(
    lead_id: int,
    body: ActivityCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    lead = _get_user_lead(db, lead_id, user_id)

    if body.type not in ACTIVITY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo inválido. Use: {', '.join(ACTIVITY_TYPES)}.",
        )
    if body.type == "call" and body.outcome and body.outcome not in CALL_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"Resultado inválido. Use: {', '.join(CALL_OUTCOMES)}.",
        )

    activity = Activity(
        lead_id=lead.id,
        user_id=user_id,
        type=body.type,
        outcome=body.outcome if body.type == "call" else None,
        notes=body.notes,
        due_at=body.due_at,
    )
    db.add(activity)

    derived = apply_rules(lead, activity, meeting_at=body.meeting_at)
    for d in derived:
        db.add(d)

    db.commit()
    db.refresh(activity)
    for d in derived:
        db.refresh(d)

    msgs = {
        "meeting_scheduled": "Reunião registrada — lead movido para 'reunião agendada'.",
        "no_answer": "Registrado. Follow-up criado para daqui a 2 dias úteis.",
        "voicemail": "Registrado. Follow-up criado para daqui a 2 dias úteis.",
        "busy": "Registrado. Follow-up criado para amanhã (dia útil).",
        "talked": "Conversa registrada — lead marcado como contatado.",
    }
    return ActivityResponse(
        success=True,
        message=msgs.get(body.outcome or "", "Atividade registrada."),
        activity=ActivityOut.model_validate(activity),
        derived=[ActivityOut.model_validate(d) for d in derived],
        lead_stage=lead.stage,
    )


@router.get("/leads/{lead_id}/activities", response_model=List[ActivityOut])
def list_lead_activities(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Timeline do lead, mais recente primeiro."""
    _get_user_lead(db, lead_id, current_user.get("sub"))
    return (
        db.query(Activity)
        .filter(Activity.lead_id == lead_id)
        .order_by(Activity.created_at.desc())
        .all()
    )


@router.get("/activities/pending", response_model=List[ActivityOut])
def pending_activities(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Fila do dia: follow-ups e reuniões em aberto, mais urgente primeiro."""
    user_id = current_user.get("sub")
    return (
        db.query(Activity)
        .filter(
            Activity.user_id == user_id,
            Activity.due_at.isnot(None),
            Activity.completed_at.is_(None),
        )
        .order_by(Activity.due_at.asc())
        .all()
    )


@router.patch("/activities/{activity_id}", response_model=ActivityOut)
def update_activity(
    activity_id: int,
    body: ActivityUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Concluir, reagendar ou anotar uma atividade."""
    act = _get_user_activity(db, activity_id, current_user.get("sub"))
    if body.completed is not None:
        act.completed_at = datetime.now(UTC) if body.completed else None
    if body.due_at is not None:
        act.due_at = body.due_at
    if body.notes is not None:
        act.notes = body.notes
    db.commit()
    db.refresh(act)
    return act


@router.get("/activities/{activity_id}/ics")
def download_ics(
    activity_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Convite .ics universal (Outlook, Google e Apple Calendar)."""
    act = _get_user_activity(db, activity_id, current_user.get("sub"))
    if not act.due_at:
        raise HTTPException(status_code=422, detail="Atividade sem data agendada.")
    lead = db.query(Lead).filter(Lead.id == act.lead_id).first()
    content = build_ics(act, lead)
    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="leadenricher_{act.id}.ics"'},
    )


@router.get("/followups/today")
def today_followups(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Fila do dia — follow-ups agendados para hoje (para navbar)."""
    user_id = current_user.get("sub")
    today_date = datetime.now(UTC).date()
    acts = (
        db.query(Activity, Lead.domain, Lead.company_name)
        .join(Lead, Activity.lead_id == Lead.id)
        .filter(
            Lead.user_id == user_id,
            # Follow-ups derivados pelas regras nascem como type=task;
            # reuniões como type=meeting. Não existe type="followup".
            Activity.type.in_(("task", "meeting")),
            Activity.completed_at.is_(None),
            func.date(Activity.due_at) == today_date,
        )
        .order_by(Activity.due_at.asc())
        .all()
    )
    return [
        {
            "id": a[0].id,
            "lead_id": a[0].lead_id,
            "domain": a[1] or "?",
            "company_name": a[2] or "?",
            "due_at": a[0].due_at.isoformat() if a[0].due_at else None,
            "notes": a[0].notes,
        }
        for a in acts
    ]
