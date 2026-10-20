from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from models.database import (
    get_db, DecisionMaker, Lead, HIDDEN_LEAD_STATUSES,
    RELATIONSHIP_LEAD, RELATIONSHIP_CUSTOMER, RELATIONSHIP_DO_NOT_CONTACT,
)
from models.schemas import (
    DecisionMakerCreate, DecisionMakerOut, DecisionMakerUpdate, LeadOut,
    LeadListOut, LeadUpdate, StageUpdate, TierUpdate,
)
from services.activity_rules import PIPELINE_STAGES
from services.cadence import CADENCES
from services.lead_scorer import apply_score, PRIORITIES
from services.tier_classifier import apply_tier
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
    priority: Optional[str] = Query(None),
    tier: Optional[int] = Query(None, ge=1, le=4),
    cadence: Optional[str] = Query(None),
    sort: str = Query("recent", pattern="^(recent|score|tier)$"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Histórico de fichas do usuário.

    `sort=score` é o que dá utilidade à nota: sem uma ordem por prioridade, a
    pontuação seria um enfeite no card e a lista continuaria em ordem de
    chegada, que é justamente a ordem que o scoring existe para substituir.
    """
    user_id = current_user.get("sub")
    q = db.query(Lead).filter(
        Lead.user_id == user_id, Lead.status.notin_(HIDDEN_LEAD_STATUSES)
    )
    if stage:
        if stage not in PIPELINE_STAGES:
            raise HTTPException(status_code=422, detail="Estágio inválido.")
        q = q.filter(Lead.stage == stage)
    if priority:
        if priority not in PRIORITIES:
            raise HTTPException(
                status_code=422,
                detail=f"Prioridade inválida. Use: {', '.join(PRIORITIES)}.",
            )
        q = q.filter(Lead.priority == priority)
    if tier:
        q = q.filter(Lead.tier == tier)
    if cadence:
        if cadence == "nao_executado":
            q = q.filter(Lead.cadence.is_(None))
        elif cadence in CADENCES:
            q = q.filter(Lead.cadence == cadence)
        else:
            raise HTTPException(status_code=422, detail="Cadência inválida.")

    if sort == "tier":
        # Tier 1 primeiro; dentro do tier, quem tem mais chance de atender.
        q = q.order_by(Lead.tier.asc().nullslast(), Lead.score.desc().nullslast(),
                       Lead.created_at.desc())
    elif sort == "score":
        # `nullslast`: ficha ainda não pontuada vai para o fim em vez de
        # encabeçar a lista, que é o que um NULL faz por padrão em Postgres
        # na ordem decrescente. Empate cai no mais recente.
        q = q.order_by(Lead.score.desc().nullslast(), Lead.created_at.desc())
    else:
        q = q.order_by(Lead.created_at.desc())

    offset = (page - 1) * per_page
    return q.offset(offset).limit(per_page).all()


@router.post("/leads/{lead_id}/rescore", response_model=LeadOut)
def rescore_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Recalcula a nota da ficha sob demanda.

    Existe porque a nota é gravada, não calculada na leitura — e dado gravado
    envelhece. Três situações deixam a nota atrás da realidade e nenhuma delas
    passa pelos dois pontos que repontuam sozinhos (coleta e busca de
    decisores): telefone corrigido à mão, contato revelado na Lusha, e ficha
    pontuada por uma régua anterior à vigente.

    É idempotente e não vai à rede: só relê o que já está no banco.
    """
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    apply_score(lead, decision_makers=list(lead.decision_makers))
    _retier(db, lead, current_user)
    db.commit()
    db.refresh(lead)
    return lead


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


def _retier(db: Session, lead: Lead, current_user: dict) -> None:
    """Reclassifica com os fatos do momento. Não commita."""
    apply_tier(db, lead, current_user.get("sub"), current_user.get("email"))


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: int,
    body: LeadUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Corrige à mão o que a coleta trouxe errado: telefone, porte e "já é
    cliente". Os dois últimos mudam o tier, que é recalculado na hora.
    """
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    campos = body.model_fields_set & {"phone", "employees", "relationship"}
    if not campos:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")
    if "phone" in campos:
        lead.phone = _telefone_editado(body.phone)
    if "employees" in campos and body.employees:
        n = body.employees
        lead.employee_count = {"raw": str(n), "min": n, "max": n, "band": None,
                               "exact": n, "source": "manual"}
        # A contagem do LinkedIn venceria a informada; quem corrigiu à mão sabe.
        lead.employee_count_linkedin = None
    if "relationship" in campos:
        permitidos = (RELATIONSHIP_LEAD, RELATIONSHIP_CUSTOMER, RELATIONSHIP_DO_NOT_CONTACT)
        if body.relationship not in permitidos:
            raise HTTPException(status_code=422,
                                detail=f"Relação inválida. Use: {', '.join(permitidos)}.")
        lead.relationship = body.relationship
    if campos & {"employees", "relationship"}:
        apply_score(lead, decision_makers=list(lead.decision_makers))
        _retier(db, lead, current_user)
    db.commit()
    db.refresh(lead)
    return lead


@router.patch("/leads/{lead_id}/tier", response_model=LeadOut)
def set_tier(
    lead_id: int,
    body: TierUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Tier escolhido pelo vendedor; `null` devolve a decisão à régua."""
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    lead.tier_manual = body.tier
    _retier(db, lead, current_user)
    db.commit()
    db.refresh(lead)
    return lead


@router.post("/leads/{lead_id}/reclassificar", response_model=LeadOut)
def reclassificar(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Refaz a classificação sem ir à rede. Serve para quando o mundo mudou fora
    da ficha: um colega largou o domínio, a base de clientes foi atualizada.
    """
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    _retier(db, lead, current_user)
    db.commit()
    db.refresh(lead)
    return lead


# ── Contatos do decisor ──────────────────────────────────────────────────────

def _get_user_decisor(db: Session, decisor_id: int, user_id: str) -> DecisionMaker:
    decisor = (
        db.query(DecisionMaker)
        .join(Lead, DecisionMaker.lead_id == Lead.id)
        .filter(DecisionMaker.id == decisor_id, Lead.user_id == user_id)
        .first()
    )
    if not decisor:
        raise HTTPException(status_code=404, detail="Decisor não encontrado.")
    return decisor


def _email_editado(bruto: Optional[str]) -> Optional[str]:
    if bruto is None or not bruto.strip():
        return None
    email = bruto.strip().lower()
    local, _, dominio = email.partition("@")
    if not local or "." not in dominio or " " in email:
        raise HTTPException(status_code=422, detail="E-mail inválido.")
    return email


def _linkedin_editado(bruto: Optional[str]) -> Optional[str]:
    if bruto is None or not bruto.strip():
        return None
    url = bruto.strip()
    if "linkedin.com/" not in url.lower():
        raise HTTPException(status_code=422, detail="Informe a URL do perfil no LinkedIn.")
    return url if url.startswith("http") else "https://" + url.lstrip("/")


def _aplicar_contato(db: Session, decisor: DecisionMaker, body: DecisionMakerUpdate) -> None:
    """Grava só os campos enviados. String vazia apaga."""
    enviados = body.model_fields_set
    if "name" in enviados:
        nome = (body.name or "").strip()
        if not nome:
            raise HTTPException(status_code=422, detail="O nome não pode ficar vazio.")
        decisor.name = nome
    if "title" in enviados:
        decisor.title_found = (body.title or "").strip() or None
    if "phone" in enviados:
        decisor.phone = _telefone_editado(body.phone)
    if "email" in enviados:
        email = _email_editado(body.email)
        outros = [e for e in (decisor.probable_emails or [])
                  if isinstance(e, dict) and e.get("status") != "manual"]
        decisor.probable_emails = ([{"email": email, "status": "manual"}] if email else []) + outros
    if "linkedin_url" in enviados:
        decisor.linkedin_url = _linkedin_editado(body.linkedin_url)
    if "is_primary" in enviados and body.is_primary is not None:
        if body.is_primary:
            # Um principal por ficha: é com ele que a cadência fala.
            db.query(DecisionMaker).filter(
                DecisionMaker.lead_id == decisor.lead_id, DecisionMaker.id != decisor.id,
            ).update({DecisionMaker.is_primary: False}, synchronize_session="fetch")
        decisor.is_primary = body.is_primary


@router.get("/leads/{lead_id}/decisores", response_model=List[DecisionMakerOut])
def list_decisores(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Contatos salvos na ficha — cadastrados à mão e revelados. O principal
    primeiro, depois os manuais (quem o vendedor confirmou), depois o resto.
    Palpite da busca gratuita sem telefone nem e-mail confirmado fica de fora:
    esta lista é "com quem eu falo", não "quem pode trabalhar lá".
    """
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    salvos = [
        dm for dm in lead.decision_makers
        if dm.source == "manual" or dm.phone or dm.revealed or dm.is_primary
        or any(isinstance(e, dict) and e.get("status") in ("valid", "manual")
               for e in (dm.probable_emails or []))
    ]
    return sorted(salvos, key=lambda d: (not d.is_primary, d.source != "manual", d.id))


@router.post("/leads/{lead_id}/decisores", response_model=DecisionMakerOut, status_code=201)
def create_decisor(
    lead_id: int,
    body: DecisionMakerCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Contato do decisor cadastrado à mão (telefone, e-mail, LinkedIn)."""
    lead = _get_user_lead(db, lead_id, current_user.get("sub"))
    ja_tem_principal = any(dm.is_primary for dm in lead.decision_makers)
    decisor = DecisionMaker(lead_id=lead.id, source="manual", match_confidence="high",
                            probable_emails=[], is_primary=False)
    db.add(decisor)
    db.flush()
    _aplicar_contato(db, decisor, body)
    # O primeiro contato da ficha vira principal sem ninguém precisar marcar.
    if not ja_tem_principal and "is_primary" not in body.model_fields_set:
        decisor.is_primary = True
    db.refresh(lead)
    apply_score(lead, decision_makers=list(lead.decision_makers))
    db.commit()
    db.refresh(decisor)
    return decisor


@router.patch("/decisores/{decisor_id}", response_model=DecisionMakerOut)
def update_decisor(
    decisor_id: int,
    body: DecisionMakerUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Corrige o contato do decisor. A coleta gratuita não entrega celular
    pessoal nem confirma e-mail — é aqui que o dado certo entra.
    """
    decisor = _get_user_decisor(db, decisor_id, current_user.get("sub"))
    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")
    _aplicar_contato(db, decisor, body)
    apply_score(decisor.lead, decision_makers=list(decisor.lead.decision_makers))
    db.commit()
    db.refresh(decisor)
    return decisor


@router.delete("/decisores/{decisor_id}", status_code=204)
def delete_decisor(
    decisor_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    decisor = _get_user_decisor(db, decisor_id, current_user.get("sub"))
    lead = decisor.lead
    db.delete(decisor)
    db.flush()
    db.refresh(lead)
    apply_score(lead, decision_makers=list(lead.decision_makers))
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
