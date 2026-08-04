"""
API da extensão de navegador — o "Lusha brasileiro" do LeadEnricher.

Princípios que valem para todos os endpoints:
  - /resolve NUNCA cobra crédito e NUNCA faz rede pesada (é o que dá a
    sensação de instantâneo ao abrir um perfil no LinkedIn)
  - /reveal cobra no máximo 1 crédito, só quando encontra algo novo
  - opt-out (LGPD) é verificado antes de gravar e antes de entregar
"""
import hashlib
import logging
import secrets
from datetime import datetime, UTC, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from slowapi import Limiter
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, rate_limit_key
from models.database import (
    get_db, Company, DecisionMaker, ExtensionToken, Lead, Person, Profile, Reveal,
)
from models.schemas import (
    CompanyContextRequest, CompanyContextResponse, CompanyPersonOut,
    ExtensionPairRequest, ReportRequest, ResolveRequest, ResolveResponse,
    RevealRequest, RevealResponse, SaveRequest,
)
from routers.auth import get_or_create_profile
from routers.billing import _maybe_reset_quota
from services._utils import normalize_domain
from services.people import company_lookup, optout, repository as repo, waterfall
from services.people import email_patterns as ep
from services.people.identity import extract_title, linkedin_slug as norm_slug, parse_headline
from services.providers import configured_providers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/extension", tags=["extension"])
limiter = Limiter(key_func=rate_limit_key)

_bearer = HTTPBearer()

PAIR_CODE_TTL_MINUTES = 10
REVEAL_GRACE_DAYS = 90        # revelar de novo dentro desse prazo é grátis
REVEAL_BUDGET_SECONDS = 12.0  # teto de tempo do /reveal (Vercel corta em 60 s)


# ── Autenticação ─────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_ext_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
    db: Session = Depends(get_db),
) -> dict:
    """
    Aceita token da extensão (ext_...), JWT do Supabase ou sessão demo —
    assim a mesma extensão funciona logada ou em modo de teste.
    """
    token = credentials.credentials or ""
    if token.startswith("ext_"):
        row = (
            db.query(ExtensionToken)
            .filter(
                ExtensionToken.token_hash == _hash_token(token),
                ExtensionToken.revoked_at.is_(None),
            )
            .first()
        )
        if not row:
            raise HTTPException(status_code=401, detail="Extensão não pareada ou revogada.")
        row.last_used_at = datetime.now(UTC)
        db.commit()
        return {"sub": row.user_id, "_ext": True}
    return get_current_user(credentials)


# ── Pareamento ───────────────────────────────────────────────────────────────

@router.post("/pair-code")
@limiter.limit("10/minute")
def create_pair_code(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Gera o código curto que o usuário digita na extensão (chamado pelo /app)."""
    user_id = current_user.get("sub")
    get_or_create_profile(db, user_id)

    code = "-".join(secrets.token_hex(2).upper() for _ in range(2))  # ABCD-EF12
    entry = ExtensionToken(
        user_id=user_id,
        code=code,
        code_expires_at=datetime.now(UTC) + timedelta(minutes=PAIR_CODE_TTL_MINUTES),
    )
    db.add(entry)
    db.commit()
    return {"code": code, "expires_in_minutes": PAIR_CODE_TTL_MINUTES}


@router.post("/pair")
@limiter.limit("10/minute")
def pair(request: Request, body: ExtensionPairRequest, db: Session = Depends(get_db)):
    """Troca o código de pareamento por um token permanente do dispositivo."""
    code = (body.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="Informe o código de pareamento.")

    entry = (
        db.query(ExtensionToken)
        .filter(ExtensionToken.code == code, ExtensionToken.token_hash.is_(None))
        .order_by(ExtensionToken.id.desc())
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Código inválido.")
    expires = entry.code_expires_at
    if expires and (expires if expires.tzinfo else expires.replace(tzinfo=UTC)) < datetime.now(UTC):
        raise HTTPException(status_code=410, detail="Código expirado. Gere outro no LeadEnricher.")

    token = "ext_" + secrets.token_urlsafe(32)
    entry.token_hash = _hash_token(token)
    entry.code = None
    entry.code_expires_at = None
    entry.device_label = (body.device_label or "Chrome")[:120]
    entry.last_used_at = datetime.now(UTC)
    db.commit()

    return {"token": token, "device_label": entry.device_label}


# ── Conta e créditos ─────────────────────────────────────────────────────────

def _credits_left(profile: Profile) -> Optional[int]:
    if profile.reveals_limit is None or profile.reveals_limit < 0:
        return None  # ilimitado
    return max(0, (profile.reveals_limit or 0) - (profile.reveals_used or 0))


@router.get("/me")
def me(db: Session = Depends(get_db), current_user: dict = Depends(get_ext_user)):
    profile = get_or_create_profile(db, current_user.get("sub"))
    if _maybe_reset_quota(profile):
        db.commit()
    return {
        "plan": profile.plan,
        "credits_left": _credits_left(profile),
        "credits_limit": profile.reveals_limit,
        "searches_left": max(0, (profile.searches_limit or 0) - (profile.searches_used or 0)),
        "quota_reset_at": profile.quota_reset_at.isoformat() if profile.quota_reset_at else None,
        "premium_providers": configured_providers(),
    }


# ── Resolve (rápido, sem cobrar) ─────────────────────────────────────────────

def _resolve_domain(db: Session, body, allow_network: bool) -> tuple:
    """Descobre o domínio da empresa. Devolve (domain, confidence)."""
    if body.company_domain:
        return normalize_domain(body.company_domain), 100
    found = company_lookup.resolve(
        db,
        company_name=body.company_name,
        linkedin_slug=body.company_linkedin_slug,
        location=getattr(body, "location", None),
        allow_network=allow_network,
    )
    if found:
        return found["domain"], found["confidence"]
    return None, 0


@router.post("/resolve", response_model=ResolveResponse)
@limiter.limit("120/minute")
def resolve(
    request: Request,
    body: ResolveRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_ext_user),
):
    """
    Identifica a pessoa da página aberta e mostra, mascarado, o que já sabemos.
    Não consome crédito. Alvo: responder em menos de meio segundo.
    """
    user_id = current_user.get("sub")
    profile = get_or_create_profile(db, user_id)

    slug = body.linkedin_slug or norm_slug(body.linkedin_url or "")
    if slug and optout.is_blocked(db, "linkedin", slug):
        return ResolveResponse(blocked=True, credits_cost=0, credits_left=_credits_left(profile))

    title = body.title or extract_title(body.headline or "")
    company_name = body.company_name
    if not company_name and body.headline:
        _, parsed_company = parse_headline(body.headline)
        company_name = parsed_company

    domain, domain_confidence = _resolve_domain(db, body, allow_network=body.deep)

    company = None
    if domain:
        company = repo.upsert_company(
            db, domain,
            name=company_name,
            linkedin_slug=body.company_linkedin_slug,
        )

    person = repo.upsert_person(
        db,
        full_name=body.full_name,
        linkedin_url=body.linkedin_url,
        slug=slug,
        title=title,
        headline=body.headline,
        company_domain=domain,
        company_name=company_name,
        company=company,
        location=body.location,
        photo_url=body.photo_url,
        source="linkedin_dom",
    )
    if not person:
        db.commit()
        return ResolveResponse(
            company_name=company_name,
            company_domain=domain,
            needs_domain=not domain,
            credits_left=_credits_left(profile),
        )

    prev = waterfall.preview(db, person)
    already = _recent_reveal(db, user_id, person.id) is not None
    db.commit()

    return ResolveResponse(
        person_id=person.id,
        full_name=person.full_name,
        title=person.title,
        seniority=person.seniority,
        department=person.department,
        company_name=person.company_name_raw or (company.name if company else None),
        company_domain=domain,
        domain_confidence=domain_confidence,
        linkedin_url=f"https://www.linkedin.com/in/{slug}" if slug else None,
        email=prev["email"],
        phone=prev["phone"],
        known_pattern=prev["known_pattern"],
        likely_findable=prev["likely_findable"],
        already_revealed=already,
        credits_cost=0 if already else 1,
        credits_left=_credits_left(profile),
        blocked=prev["blocked"],
        needs_domain=not domain,
    )


# ── Reveal (cobra, com todas as travas) ──────────────────────────────────────

def _recent_reveal(db: Session, user_id: str, person_id: int) -> Optional[Reveal]:
    cutoff = datetime.now(UTC) - timedelta(days=REVEAL_GRACE_DAYS)
    return (
        db.query(Reveal)
        .filter(Reveal.user_id == user_id, Reveal.person_id == person_id, Reveal.created_at >= cutoff)
        .order_by(Reveal.created_at.desc())
        .first()
    )


@router.post("/reveal", response_model=RevealResponse)
@limiter.limit("40/minute")
def reveal(
    request: Request,
    body: RevealRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_ext_user),
):
    user_id = current_user.get("sub")
    profile = get_or_create_profile(db, user_id)
    if _maybe_reset_quota(profile):
        db.commit()

    person = None
    if body.person_id:
        person = db.query(Person).filter(Person.id == body.person_id).first()
    elif body.linkedin_slug:
        person = repo.find_person(db, slug=norm_slug(body.linkedin_slug))
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada. Recarregue a página.")

    if body.company_domain and not person.company_domain:
        person.company_domain = normalize_domain(body.company_domain)

    previous = _recent_reveal(db, user_id, person.id)
    credits_left = _credits_left(profile)
    if previous is None and credits_left is not None and credits_left <= 0:
        raise HTTPException(
            status_code=402,
            detail="Créditos de revelação esgotados neste ciclo. Faça upgrade para continuar.",
        )

    company = repo.get_company(db, person.company_domain) if person.company_domain else None
    result = waterfall.reveal(
        db, person, company=company, kind=body.kind, budget_seconds=REVEAL_BUDGET_SECONDS,
    )

    if result["blocked"]:
        db.commit()
        return RevealResponse(
            success=False,
            message="Este contato pediu remoção da nossa base (LGPD) e não pode ser exibido.",
            person_id=person.id,
            credits_left=credits_left,
        )

    found = result["found_email"] or result["found_phone"]
    charged = 0
    # Não cobra: (a) nada encontrado  (b) já revelado nos últimos 90 dias
    if found and previous is None:
        charged = 1
        profile.reveals_used = (profile.reveals_used or 0) + 1

    db.add(Reveal(
        user_id=user_id,
        person_id=person.id,
        kind=body.kind,
        credits_charged=charged,
        found_email=result["found_email"],
        found_phone=result["found_phone"],
        provider_chain=result["chain"],
        cost_usd=0.0,
    ))
    db.commit()
    db.refresh(person)

    company = company or (repo.get_company(db, person.company_domain) if person.company_domain else None)
    company_phone = None
    if company and company.phones:
        company_phone = (company.phones[0] or {}).get("formatted")

    if found:
        message = "Contato revelado."
    elif person.company_domain:
        message = "Não encontramos contato confiável para esta pessoa — nada foi cobrado."
    else:
        message = "Informe o site da empresa para conseguirmos montar o e-mail — nada foi cobrado."

    return RevealResponse(
        success=found,
        message=message,
        person_id=person.id,
        full_name=person.full_name,
        title=person.title,
        company_name=person.company_name_raw,
        company_domain=person.company_domain,
        emails=result["emails"],
        phones=result["phones"],
        company_phone=company_phone,
        credits_charged=charged,
        credits_left=_credits_left(profile),
        from_cache=result["from_cache"],
        chain=result["chain"],
    )


# ── Contexto da empresa (página /company do LinkedIn) ────────────────────────

@router.post("/company", response_model=CompanyContextResponse)
@limiter.limit("60/minute")
def company_context(
    request: Request,
    body: CompanyContextRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_ext_user),
):
    user_id = current_user.get("sub")
    get_or_create_profile(db, user_id)

    domain = normalize_domain(body.domain or "")
    if not domain:
        found = company_lookup.resolve(
            db, company_name=body.company_name, linkedin_slug=body.linkedin_slug,
            allow_network=body.deep,
        )
        domain = found["domain"] if found else None
    if not domain:
        return CompanyContextResponse(name=body.company_name)

    company = waterfall.ensure_company_context(
        db, domain,
        company_name=body.company_name,
        linkedin_slug=body.linkedin_slug,
        budget_seconds=9.0 if body.deep else 6.0,
        allow_network=True,
    )
    if not company:
        return CompanyContextResponse(domain=domain, name=body.company_name)

    people = []
    for person in waterfall.find_company_decision_makers(db, company, limit=12):
        best = repo.best_email(person)
        people.append(CompanyPersonOut(
            person_id=person.id,
            full_name=person.full_name,
            title=person.title,
            seniority=person.seniority,
            linkedin_url=(f"https://www.linkedin.com/in/{person.linkedin_slug}"
                          if person.linkedin_slug else None),
            source=person.source,
            email_preview=waterfall.mask_email(best.email) if best else None,
            has_email=bool(best),
            has_phone=bool(repo.best_phone(person)),
        ))

    pattern_row = ep.get_pattern(db, domain)
    lead = (
        db.query(Lead)
        .filter(Lead.user_id == user_id, Lead.domain == domain)
        .order_by(Lead.created_at.desc())
        .first()
    )
    cnpj_data = company.cnpj_data or {}
    db.commit()

    return CompanyContextResponse(
        domain=company.domain,
        name=company.name or body.company_name,
        sector=company.sector,
        location=company.location,
        cnpj=company.cnpj,
        razao_social=cnpj_data.get("razao_social"),
        situacao=cnpj_data.get("situacao"),
        porte=cnpj_data.get("porte"),
        phones=company.phones or [],
        main_email=company.main_email,
        email_pattern=pattern_row.pattern if pattern_row else None,
        pattern_confidence=(pattern_row.confidence or 0) if pattern_row else 0,
        employee_count=company.employee_count,
        people=people,
        lead_id=lead.id if lead else None,
    )


# ── Salvar na pipeline ───────────────────────────────────────────────────────

@router.post("/save")
@limiter.limit("60/minute")
def save_to_pipeline(
    request: Request,
    body: SaveRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_ext_user),
):
    """Joga a pessoa revelada dentro da pipeline do usuário (Lead + DecisionMaker)."""
    user_id = current_user.get("sub")
    get_or_create_profile(db, user_id)

    person = db.query(Person).filter(Person.id == body.person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")

    domain = person.company_domain
    lead = None
    if body.lead_id:
        lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.user_id == user_id).first()
    if not lead and domain:
        lead = (
            db.query(Lead)
            .filter(Lead.user_id == user_id, Lead.domain == domain)
            .order_by(Lead.created_at.desc())
            .first()
        )
    if not lead:
        company = repo.get_company(db, domain) if domain else None
        lead = Lead(
            user_id=user_id,
            raw_input_domain=domain or (person.company_name_raw or person.full_name or "extensão"),
            domain=domain,
            company_name=(company.name if company else None) or person.company_name_raw,
            website=f"https://{domain}" if domain else None,
            sector=company.sector if company else None,
            location=company.location if company else None,
            phone=company.phone if company else None,
            corporate_email=company.main_email if company else None,
            employee_count=company.employee_count if company else None,
            status="enriched",
        )
        db.add(lead)
        db.flush()

    best_email = repo.best_email(person)
    best_phone = repo.best_phone(person)
    existing = (
        db.query(DecisionMaker)
        .filter(DecisionMaker.lead_id == lead.id, DecisionMaker.name == person.full_name)
        .first()
    )
    dm = existing or DecisionMaker(lead_id=lead.id, name=person.full_name)
    dm.title_found = person.title or person.headline
    dm.title_searched = person.title
    dm.linkedin_url = (f"https://www.linkedin.com/in/{person.linkedin_slug}"
                       if person.linkedin_slug else None)
    dm.snippet = (body.note or f"Capturado pela extensão em {person.company_name_raw or person.company_domain or 'LinkedIn'}.")[:500]
    dm.probable_emails = [
        {"email": e.email, "status": e.status, "confidence": e.confidence}
        for e in repo.sorted_emails(person)[:4]
    ]
    dm.match_confidence = (
        "high" if best_email and (best_email.confidence or 0) >= 85
        else "medium" if best_email else "low"
    )
    dm.phone = best_phone.formatted if best_phone else None
    if not existing:
        db.add(dm)

    db.commit()

    return {
        "success": True,
        "message": "Contato salvo na sua pipeline.",
        "lead_id": lead.id,
        "decision_maker_id": dm.id,
    }


# ── Correção / remoção (LGPD) ────────────────────────────────────────────────

@router.post("/report")
@limiter.limit("20/minute")
def report(
    request: Request,
    body: ReportRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_ext_user),
):
    """Marca um dado como errado ou registra pedido de remoção."""
    if body.kind not in optout.KINDS:
        raise HTTPException(status_code=422, detail="Tipo inválido.")

    value = body.value
    if not value and body.person_id:
        person = db.query(Person).filter(Person.id == body.person_id).first()
        if person and body.kind == "linkedin":
            value = person.linkedin_slug
    if not value:
        raise HTTPException(status_code=422, detail="Informe o dado a ser removido.")

    optout.register(db, body.kind, value, reason=body.reason or "reportado pela extensão")
    optout.purge(db, body.kind, value)
    db.commit()
    logger.info("Opt-out registrado kind=%s via=extensao", body.kind)
    return {"success": True, "message": "Registrado. O dado não será mais exibido."}
