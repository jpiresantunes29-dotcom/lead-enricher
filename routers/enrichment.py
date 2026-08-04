import logging
from datetime import datetime, UTC, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from sqlalchemy.orm import Session
from models.database import get_db, Lead, DecisionMaker, Profile
from models.schemas import (
    EnrichRequest, EnrichResponse, LeadOut,
    DecisoresRequest, DecisoresResponse, DecisionMakerOut,
)
from services.enricher import enrich_company, ENRICHMENT_VERSION
from services._utils import normalize_domain
from services.decision_finder import find_decision_makers
from services.people.waterfall import ingest_enrichment
from middleware.auth import get_current_user, rate_limit_key
from routers.auth import get_or_create_profile
from routers.billing import _maybe_reset_quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["enrichment"])
limiter = Limiter(key_func=rate_limit_key)

# Leads com menos de 7 dias são servidos do cache
_CACHE_TTL_DAYS = 7


def _check_quota(profile: Profile, db: Session):
    if _maybe_reset_quota(profile):
        # Persiste o reset mesmo em caminhos que não commitam depois (cache hit)
        db.commit()
    if profile.searches_limit > 0 and profile.searches_used >= profile.searches_limit:
        raise HTTPException(
            status_code=402,
            detail="Cota esgotada. Faça upgrade para continuar.",
        )


def _recent_leads(db: Session, domain: str, user_id: str):
    """Fichas do domínio dentro da janela de cache, mais recente primeiro."""
    cutoff = datetime.now(UTC) - timedelta(days=_CACHE_TTL_DAYS)
    return (
        db.query(Lead)
        .filter(
            Lead.user_id == user_id,
            Lead.domain == domain,
            Lead.status != "failed",
            Lead.created_at >= cutoff,
        )
        .order_by(Lead.created_at.desc())
    )


def _find_cached_lead(db: Session, domain: str, user_id: str) -> Lead | None:
    """
    Retorna lead recente do mesmo domínio para o mesmo usuário, se existir.

    Ficha gerada por uma versão anterior da coleta não conta como cache: a
    correção que a tornou obsoleta precisa chegar ao usuário na próxima busca,
    não daqui a 7 dias.
    """
    return _recent_leads(db, domain, user_id).filter(
        Lead.enrichment_version == ENRICHMENT_VERSION
    ).first()


def _find_stale_lead(db: Session, domain: str, user_id: str) -> Lead | None:
    """
    Ficha defasada que o usuário teria recebido de graça se ainda valesse.

    Só faz sentido consultar depois de _find_cached_lead() não achar nada —
    aí qualquer ficha na janela é, por definição, de outra versão.
    """
    return _recent_leads(db, domain, user_id).first()


@router.post("/enrich", response_model=EnrichResponse)
@limiter.limit("10/minute")
def enrich(
    request: Request,
    body: EnrichRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not body.domain or not body.domain.strip():
        raise HTTPException(status_code=422, detail="Domínio não pode estar vazio.")

    user_id = current_user.get("sub")
    profile = get_or_create_profile(db, user_id)

    _check_quota(profile, db)

    domain = normalize_domain(body.domain)

    # Serve do cache se existir lead recente (não consome cota)
    cached = _find_cached_lead(db, domain, user_id)
    if cached:
        logger.info("Cache hit for domain=%s user=%s", domain, user_id)
        return EnrichResponse(
            success=True,
            message="Dados carregados do cache.",
            data=LeadOut.model_validate(cached),
        )

    # Recoleta por ficha desatualizada é correção nossa, não busca nova: o
    # usuário já pagou por este domínio e não pode pagar de novo.
    stale = _find_stale_lead(db, domain, user_id)
    charged = stale is None
    if charged:
        # Incrementa cota antes da operação lenta para evitar race condition
        profile.searches_used += 1
        db.commit()

    try:
        data = enrich_company(body.domain)
    except Exception as e:
        if charged:
            profile.searches_used -= 1
            db.commit()
        logger.exception("Enrichment failed for domain=%s: %s", domain, e)
        raise HTTPException(status_code=500, detail=f"Erro ao enriquecer: {e}")

    lead_kwargs = {k: v for k, v in data.items() if hasattr(Lead, k)}
    lead_kwargs["user_id"] = user_id
    lead = Lead(**lead_kwargs)
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # Alimenta o banco global de contatos: empresa, padrão de e-mail do domínio
    # e dados públicos do CNPJ. Roda DEPOIS do commit do lead, em transação
    # própria — uma falha aqui não pode custar a busca que o usuário já pagou.
    try:
        ingest_enrichment(db, data)
        db.commit()
    except Exception:
        logger.exception("ingest_enrichment falhou domain=%s", domain)
        db.rollback()

    logger.info("Enriched domain=%s status=%s user=%s", domain, data["status"], user_id)

    return EnrichResponse(
        success=data["status"] != "failed",
        message="Enriquecimento concluído." if data["status"] != "failed" else "Não conseguimos coletar dados deste domínio.",
        data=LeadOut.model_validate(lead),
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
