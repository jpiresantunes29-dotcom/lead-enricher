from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from models.database import get_db, DecisionMaker, Lead, HIDDEN_LEAD_STATUSES
from models.schemas import (
    DecisionMakerOut, DecisionMakerUpdate, LeadOut, LeadListOut, LeadUpdate,
    StageUpdate,
)
from services.activity_rules import PIPELINE_STAGES
from services.phone_normalizer import normalize_input
from middleware.auth import get_current_user

router = APIRouter(prefix="/api", tags=["leads"])


def _get_user_lead(db: Session, lead_id: int, user_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    return lead


def _telefone_editado(bruto: Optional[str]) -> Optional[str]:
    """
    Telefone digitado → formato da casa, ou 422.

    Guardar o que a pessoa digitou seria mais simples e erraria depois: o
    número só serve se der para discar e, no futuro, mandar mensagem. Vazio
    apaga o campo de propósito — é como se corrige um número errado.
    """
    if bruto is None or not bruto.strip():
        return None
    dados = normalize_input(bruto)
    if not dados:
        raise HTTPException(
            status_code=422,
            detail="Telefone inválido. Informe com DDD — ex.: (11) 98888-7777.",
        )
    return dados["formatted"]


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


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: int,
    body: LeadUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Corrige à mão o que a coleta trouxe errado. Hoje: o telefone."""
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    if "phone" not in body.model_fields_set:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")
    lead.phone = _telefone_editado(body.phone)
    db.commit()
    db.refresh(lead)
    return lead


@router.patch("/decisores/{decisor_id}", response_model=DecisionMakerOut)
def update_decisor(
    decisor_id: int,
    body: DecisionMakerUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Celular do decisor, informado por quem prospecta.

    A coleta gratuita não entrega celular pessoal — ela acha o telefone da
    empresa. Este é o campo onde o número certo entra depois de confirmado.
    """
    decisor = (
        db.query(DecisionMaker)
        .join(Lead, DecisionMaker.lead_id == Lead.id)
        .filter(DecisionMaker.id == decisor_id, Lead.user_id == current_user.get("sub"))
        .first()
    )
    if not decisor:
        raise HTTPException(status_code=404, detail="Decisor não encontrado.")
    if "phone" not in body.model_fields_set:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")
    decisor.phone = _telefone_editado(body.phone)
    db.commit()
    db.refresh(decisor)
    return decisor


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
