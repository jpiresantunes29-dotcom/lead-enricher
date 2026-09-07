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
from services.providers import lusha
from middleware.auth import get_current_user, rate_limit_key
from routers.auth import get_or_create_profile
from routers.extension import _lusha_key_utilizavel

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

    outcome = enrichment_service.enrich_for_user(db, profile, body.domain)

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
    minuto existe para a fila rodar várias em paralelo.
    """
    user_id = current_user.get("sub")
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    profile = get_or_create_profile(db, user_id)

    outcome = enrichment_service.enrich_existing_lead(db, profile, lead)

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

    profile = get_or_create_profile(db, user_id)

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

    # TESTE: completa com a Lusha quem ficou sem celular, se o usuário tiver
    # a conta conectada. Só entra aqui quem o caminho gratuito não resolveu.
    chave_lusha = _lusha_key_utilizavel(profile)
    if chave_lusha:
        for dm in saved:
            if dm.phone:
                continue
            achado = lusha.find_contacts(
                full_name=dm.name,
                domain=lead.domain or lead.raw_input_domain,
                company_name=lead.company_name,
                linkedin_url=dm.linkedin_url,
                api_key=chave_lusha,
            )
            if achado and achado.get("phones"):
                dm.phone = achado["phones"][0]["e164"]

    db.commit()
    for dm in saved:
        db.refresh(dm)
    db.refresh(lead)

    return DecisoresResponse(
        success=True,
        message=f"{len(saved)} decisor(es) encontrado(s)." if saved else "Nenhum decisor encontrado para os cargos informados.",
        decisores=[DecisionMakerOut.model_validate(d) for d in saved],
    )


@router.get("/leads/{lead_id}/popular-contacts", response_model=DecisoresResponse)
@limiter.limit("20/minute")
def popular_contacts(
    request: Request,
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Contatos populares da empresa — founders, executives, directors."""
    user_id = current_user.get("sub")
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    profile = get_or_create_profile(db, user_id)

    # Cargos executivos e fundadores — quanto menor a lista, melhor a chance de
    # trazer gente realmente importante da empresa.
    cargos = ["founder", "ceo", "cto", "cfo", "vp", "president", "executive"]

    try:
        results = find_decision_makers(
            domain=lead.domain or lead.raw_input_domain,
            company_name=lead.company_name,
            roles=cargos,
            limit=15,
            linkedin_url=lead.linkedin_url,
            db=db,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar contatos: {e}")

    # FILTRO DE FIDELIDADE: só guarda quem tem LinkedIn verificado
    # (elimina contatos de busca sem validação e ex-funcionários)
    results = [
        r for r in results
        if r.get("linkedin_url") and "linkedin.com/in/" in r.get("linkedin_url", "").lower()
    ]

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

    # Lusha: preencher telefone para quem ficou sem
    chave_lusha = _lusha_key_utilizavel(profile)
    if chave_lusha:
        for dm in saved:
            if dm.phone:
                continue
            achado = lusha.find_contacts(
                full_name=dm.name,
                domain=lead.domain or lead.raw_input_domain,
                company_name=lead.company_name,
                linkedin_url=dm.linkedin_url,
                api_key=chave_lusha,
            )
            if achado and achado.get("phones"):
                dm.phone = achado["phones"][0]["e164"]

    db.commit()
    for dm in saved:
        db.refresh(dm)

    return DecisoresResponse(
        success=True,
        message=f"{len(saved)} contato(s) popular(es) encontrado(s).",
        decisores=[DecisionMakerOut.model_validate(d) for d in saved],
    )
