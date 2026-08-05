from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from models.database import get_db, Lead, HIDDEN_LEAD_STATUSES
from models.schemas import LeadOut, LeadListOut, StageUpdate
from services.activity_rules import PIPELINE_STAGES
from middleware.auth import get_current_user

router = APIRouter(prefix="/api", tags=["leads"])


def _get_user_lead(db: Session, lead_id: int, user_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    return lead


@router.get("/leads", response_model=List[LeadListOut])
def list_leads(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    stage: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    q = db.query(Lead).filter(
        Lead.user_id == user_id, Lead.status.notin_(HIDDEN_LEAD_STATUSES)
    )
    if stage:
        if stage not in PIPELINE_STAGES:
            raise HTTPException(status_code=422, detail="Estágio inválido.")
        q = q.filter(Lead.stage == stage)
    offset = (page - 1) * per_page
    return q.order_by(Lead.created_at.desc()).offset(offset).limit(per_page).all()


@router.get("/leads/{lead_id}", response_model=LeadOut)
def get_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return _get_user_lead(db, lead_id, current_user.get("sub"))


@router.delete("/leads/{lead_id}", status_code=204)
def delete_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    db.delete(lead)
    db.commit()


@router.patch("/leads/{lead_id}/stage", response_model=LeadOut)
def update_stage(
    lead_id: int,
    body: StageUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Move o lead no pipeline (kanban)."""
    if body.stage not in PIPELINE_STAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Estágio inválido. Use: {', '.join(PIPELINE_STAGES)}.",
        )
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    lead.stage = body.stage
    db.commit()
    db.refresh(lead)
    return lead
