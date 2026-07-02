from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from models.database import get_db, Lead
from models.schemas import LeadOut, StageUpdate
from services.lead_scorer import apply_score
from services.activity_rules import PIPELINE_STAGES
from middleware.auth import get_current_user

router = APIRouter(prefix="/api", tags=["leads"])


def _get_user_lead(db: Session, lead_id: int, user_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    return lead


@router.get("/leads", response_model=List[LeadOut])
def list_leads(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    priority: Optional[str] = Query(None, pattern="^(alta|media|baixa)$"),
    stage: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    q = db.query(Lead).filter(Lead.user_id == user_id, Lead.status != "failed")
    if priority:
        q = q.filter(Lead.priority == priority)
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


@router.post("/leads/{lead_id}/rescore", response_model=LeadOut)
def rescore_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Recalcula o score sob demanda (ex.: após mudança de pesos do modelo)."""
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    apply_score(lead, list(lead.decision_makers))
    db.commit()
    db.refresh(lead)
    return lead


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
