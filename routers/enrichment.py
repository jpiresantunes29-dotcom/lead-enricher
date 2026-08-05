import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from sqlalchemy.orm import Session
from models.database import get_db, DecisionMaker, Lead
from models.schemas import (
    EnrichRequest, EnrichResponse, LeadOut,
    DecisoresRequest, DecisoresResponse, DecisionMakerOut,
)
from services import enrichment_service
from services.decision_finder import find_decision_makers
from middleware.auth import get_current_user, rate_limit_key
from routers.auth import get_or_create_profile
from routers.billing import _maybe_reset_quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["enrichment"])
limiter = Limiter(key_func=rate_limit_key)


@router.post("/enrich", response_model=EnrichResponse)
@limiter.limit("10/minute")
def enrich(
    request: Request,
    body: EnrichRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Busca avulsa: continua síncrona porque um domínio cabe no orçamento da
    requisição (~15-30 s) e o usuário está olhando para a tela esperando a
    ficha. Volume vai pela fila — ver routers/batch.py.
    """
    if not body.domain or not body.domain.strip():
        raise HTTPException(status_code=422, detail="Domínio não pode estar vazio.")

    user_id = current_user.get("sub")
    profile = get_or_create_profile(db, user_id)
    if _maybe_reset_quota(profile):
        # Persiste o reset mesmo em caminhos que não commitam depois (cache hit)
        db.commit()

    outcome = enrichment_service.enrich_for_user(db, profile, body.domain)

    if outcome.result == enrichment_service.RESULT_QUOTA:
        raise HTTPException(status_code=402, detail=outcome.message)
    if outcome.result == enrichment_service.RESULT_ERROR:
        raise HTTPException(status_code=500, detail=outcome.message)

    return EnrichResponse(
        success=outcome.result != enrichment_service.RESULT_FAILED,
        message=outcome.message,
        data=LeadOut.model_validate(outcome.lead),
    )


@router.post("/leads/{lead_id}/enrich", response_model=EnrichResponse)
@limiter.limit("120/minute")
def enrich_existing_lead(
    request: Request,
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Enriquece uma ficha que já existe — as que vieram de planilha.

    É o que a fila da planilha chama, um lead por requisição: a coleta leva
    10–30 s e o maxDuration da função na Vercel é 60 s. O limite alto por
    minuto existe para a fila rodar várias em paralelo; o freio real continua
    sendo a cota do plano (uma busca por lead).
    """
    user_id = current_user.get("sub")
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    profile = get_or_create_profile(db, user_id)
    if _maybe_reset_quota(profile):
        db.commit()

    outcome = enrichment_service.enrich_existing_lead(db, profile, lead)

    if outcome.result == enrichment_service.RESULT_QUOTA:
        raise HTTPException(status_code=402, detail=outcome.message)
    if outcome.error == "dominio_desconhecido":
        raise HTTPException(status_code=422, detail=outcome.message)
    if outcome.result == enrichment_service.RESULT_ERROR:
        raise HTTPException(status_code=500, detail=outcome.message)

    return EnrichResponse(
        success=outcome.result != enrichment_service.RESULT_FAILED,
        message=outcome.message,
        data=LeadOut.model_validate(outcome.lead),
    )


@router.post("/decisores", response_model=DecisoresResponse)
@limiter.limit("20/minute")
def buscar_decisores(
    request: Request,
    body: DecisoresRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not body.roles:
        raise HTTPException(status_code=422, detail="Informe pelo menos um cargo.")

    user_id = current_user.get("sub")
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    try:
        results = find_decision_makers(
            domain=lead.domain or lead.raw_input_domain,
            company_name=lead.company_name,
            roles=body.roles,
            limit=8,
            linkedin_url=lead.linkedin_url,
            # Com a sessão, os e-mails saem do padrão aprendido do domínio e os
            # decisores encontrados entram no banco global de pessoas.
            db=db,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar decisores: {e}")

    saved = []
    for r in results:
        dm = DecisionMaker(
            lead_id=lead.id,
            name=r.get("name"),
            title_searched=r.get("title_searched"),
            title_found=r.get("title_found"),
            snippet=r.get("snippet"),
            linkedin_url=r.get("linkedin_url"),
            probable_emails=r.get("probable_emails"),
            match_confidence=r.get("match_confidence"),
            phone=r.get("phone"),
        )
        db.add(dm)
        saved.append(dm)

    db.commit()
    for dm in saved:
        db.refresh(dm)
    db.refresh(lead)

    return DecisoresResponse(
        success=True,
        message=f"{len(saved)} decisor(es) encontrado(s)." if saved else "Nenhum decisor encontrado para os cargos informados.",
        decisores=[DecisionMakerOut.model_validate(d) for d in saved],
    )
