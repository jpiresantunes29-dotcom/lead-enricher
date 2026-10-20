"""
Fluxo comercial automatizado: cadência por lead, agenda do dia, agendamento
em lote e base de clientes do time. Ver docs/FLUXO_COMERCIAL_API.md.
"""
from datetime import datetime, timedelta, UTC
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from models.database import (
    get_db, Activity, CustomerDomain, DecisionMaker, Lead, HIDDEN_LEAD_STATUSES,
)
from models.schemas import (
    ActivityOut, CadenciaRequest, ClientesRequest, LeadOut, LoteRequest,
)
from services import cadence as cad
from services.domain_list import parse as parse_domains
from services.tier_classifier import apply_tier, team_of

router = APIRouter(prefix="/api", tags=["cadencia"])


def _user(current_user: dict) -> str:
    return current_user.get("sub")


def _get_user_lead(db: Session, lead_id: int, user_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    return lead


def _cadencia_out(db: Session, lead: Lead) -> Dict:
    return {
        "cadence": lead.cadence,
        "cadence_attempt": lead.cadence_attempt or 0,
        "tier": lead.tier,
        "proximas": [ActivityOut.model_validate(a).model_dump(mode="json")
                     for a in cad.abertas(db, lead)],
        "acoes": cad.acoes(lead),
    }


# ── Cadência por lead ────────────────────────────────────────────────────────

@router.get("/leads/{lead_id}/cadencia")
def get_cadencia(
    lead_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Onde o lead está e quais botões fazem sentido agora."""
    lead = _get_user_lead(db, lead_id, _user(current_user))
    return _cadencia_out(db, lead)


@router.post("/leads/{lead_id}/cadencia")
def post_cadencia(
    lead_id: int,
    body: CadenciaRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """O vendedor diz o que aconteceu; a próxima tarefa nasce sozinha."""
    user_id = _user(current_user)
    lead = _get_user_lead(db, lead_id, user_id)
    try:
        r = cad.registrar(db, lead, user_id, body.resultado,
                          notas=body.notas, reuniao_em=body.reuniao_em)
    except cad.CadenceError as e:
        raise HTTPException(status_code=422, detail=str(e))
    db.commit()
    db.refresh(lead)
    for a in r["criadas"]:
        db.refresh(a)
    return {
        "success": True,
        "message": r["message"],
        "lead": LeadOut.model_validate(lead).model_dump(mode="json"),
        "criadas": [ActivityOut.model_validate(a).model_dump(mode="json") for a in r["criadas"]],
        "cadencia": _cadencia_out(db, lead),
    }


# ── Agenda do dia ────────────────────────────────────────────────────────────

def _dia_ou_hoje(dia: Optional[str]):
    if not dia:
        return datetime.now(UTC).astimezone(cad.TZ).date()
    try:
        return datetime.strptime(dia, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Data inválida. Use AAAA-MM-DD.")


@router.get("/agenda")
def agenda(
    dia: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    O que fazer no dia, com os links de contato prontos. Atrasadas primeiro:
    tarefa vencida é a que mais custa — o lead esfria um pouco a cada dia.
    """
    user_id = _user(current_user)
    alvo = _dia_ou_hoje(dia)
    inicio = cad._local(alvo, datetime.min.time())
    fim = inicio + timedelta(days=1)

    rows = (
        db.query(Activity, Lead)
        .join(Lead, Activity.lead_id == Lead.id)
        .filter(
            Lead.user_id == user_id,
            Activity.completed_at.is_(None),
            Activity.due_at.isnot(None),
            Activity.due_at < fim,
            Activity.type != "note",
        )
        .order_by(Lead.tier.asc().nullslast(), Activity.due_at.asc())
        .all()
    )
    dm_ids = {a.decision_maker_id for a, _ in rows if a.decision_maker_id}
    dms = {d.id: d for d in db.query(DecisionMaker).filter(DecisionMaker.id.in_(dm_ids))} \
        if dm_ids else {}
    # Nome de quem assina a mensagem: o do cadastro (login Google traz), nunca
    # adivinhado do e-mail — "jpires.antunes29" viraria "Jpires Antunes29".
    meta = current_user.get("user_metadata") or {}
    nome = (meta.get("full_name") or meta.get("name") or "").strip()
    remetente = nome.split(" ")[0] if nome else ""

    atrasadas, hoje = [], []
    por_tier: Dict[str, int] = {}
    for act, lead in rows:
        dm = dms.get(act.decision_maker_id) or cad.contato_principal(lead)
        item = {
            "activity": ActivityOut.model_validate(act).model_dump(mode="json"),
            "lead": {
                "id": lead.id, "domain": lead.domain, "company_name": lead.company_name,
                "tier": lead.tier, "cadence": lead.cadence,
                "cadence_attempt": lead.cadence_attempt or 0, "phone": lead.phone,
            },
            "contato": {
                "id": dm.id, "name": dm.name, "title": dm.title_found,
                "phone": dm.phone, "email": cad._email_de(dm), "linkedin_url": dm.linkedin_url,
            } if dm else None,
            "acao": cad.acao_de(act, lead, dm, remetente),
        }
        due = act.due_at if act.due_at.tzinfo else act.due_at.replace(tzinfo=UTC)
        (atrasadas if due < inicio else hoje).append(item)
        chave = str(lead.tier) if lead.tier else "sem_tier"
        por_tier[chave] = por_tier.get(chave, 0) + 1

    return {
        "dia": alvo.isoformat(),
        "resumo": {"atrasadas": len(atrasadas), "hoje": len(hoje), "por_tier": por_tier},
        "atrasadas": atrasadas,
        "hoje": hoje,
    }


# ── Agendamento em lote (40/5/5) ─────────────────────────────────────────────

@router.post("/agenda/lote")
def agendar_lote(
    body: LoteRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Agenda os próximos N leads não executados numa data. `preview` mostra o
    lote sem gravar — o vendedor confere antes de reservar o dia.
    """
    user_id = _user(current_user)
    hoje = datetime.now(UTC).astimezone(cad.TZ).date()
    if body.dia < hoje:
        raise HTTPException(status_code=422, detail="Escolha hoje ou uma data futura.")
    if body.dia.weekday() >= 5:
        raise HTTPException(status_code=422, detail="Escolha um dia útil.")

    mix = dict(cad.LOTE_PADRAO)
    if body.mix:
        try:
            mix = {int(k): max(0, int(v)) for k, v in body.mix.items() if int(k) in (1, 2, 3)}
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Mix inválido.")

    candidatos = cad.candidatos_do_lote(db, user_id)
    lote = cad.montar_lote(candidatos, body.total, mix)
    por_tier = {str(t): sum(1 for lead in lote if lead.tier == t) for t in (1, 2, 3)}
    disponiveis = {str(t): len(candidatos.get(t, [])) for t in (1, 2, 3)}
    selecionados = [
        {"lead_id": lead.id, "domain": lead.domain, "company_name": lead.company_name,
         "tier": lead.tier}
        for lead in lote
    ]

    if not lote:
        return {"success": False, "dia": body.dia.isoformat(), "por_tier": por_tier,
                "disponiveis": disponiveis, "selecionados": [], "criadas": 0,
                "message": "Nenhum lead Tier 1–3 aguardando agendamento."}

    criadas = 0
    data_br = body.dia.strftime("%d/%m")
    if body.preview:
        msg = f"{len(lote)} leads prontos para {data_br}."
    else:
        criadas = len(cad.agendar_lote(db, user_id, lote, body.dia))
        db.commit()
        msg = f"{criadas} primeiros contatos agendados para {data_br}."
    if len(lote) < body.total:
        msg += f" A base tinha só {len(lote)} de {body.total}."

    return {"success": True, "message": msg, "dia": body.dia.isoformat(),
            "por_tier": por_tier, "disponiveis": disponiveis,
            "selecionados": selecionados, "criadas": criadas}


# ── Resumo de tiers ──────────────────────────────────────────────────────────

@router.get("/tiers/resumo")
def tiers_resumo(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _user(current_user)
    base = db.query(Lead.tier, Lead.cadence, func.count(Lead.id)).filter(
        Lead.user_id == user_id, Lead.status.notin_(HIDDEN_LEAD_STATUSES),
    ).group_by(Lead.tier, Lead.cadence)
    por_tier = {"1": 0, "2": 0, "3": 0, "4": 0, "sem_tier": 0}
    nao_exec = {"1": 0, "2": 0, "3": 0}
    for tier, cadence, n in base:
        chave = str(tier) if tier else "sem_tier"
        por_tier[chave] = por_tier.get(chave, 0) + n
        if cadence is None and chave in nao_exec:
            nao_exec[chave] += n
    # Tier suposto por porte desconhecido conta como "a revisar". JSON não
    # filtra igual em SQLite e Postgres, então a conferência é em Python —
    # só a coluna pequena, não a ficha inteira.
    detalhes = db.query(Lead.tier_details).filter(
        Lead.user_id == user_id, Lead.status.notin_(HIDDEN_LEAD_STATUSES),
        Lead.tier.isnot(None), Lead.tier_manual.is_(None),
    )
    supostos = sum(1 for (d,) in detalhes if isinstance(d, dict) and d.get("needs_review"))
    return {"por_tier": por_tier, "nao_executados": nao_exec,
            "revisar": por_tier["sem_tier"] + supostos}


@router.post("/tiers/reclassificar")
def reclassificar_todos(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Classifica de uma vez todas as fichas do usuário. Só relê o banco.

    Existe para a base anterior a esta funcionalidade: sem isso, fichas
    antigas nunca ganhariam tier e nunca entrariam num lote de agendamento.
    """
    user_id = _user(current_user)
    leads = db.query(Lead).filter(Lead.user_id == user_id,
                                  Lead.status.notin_(HIDDEN_LEAD_STATUSES)).all()
    for lead in leads:
        apply_tier(db, lead, user_id, current_user.get("email"))
    db.commit()
    return {"success": True, "reclassificados": len(leads),
            "message": f"{len(leads)} ficha(s) classificada(s)."}


# ── Base de clientes do time ─────────────────────────────────────────────────

_KINDS = ("skymail", "revenda")


@router.get("/clientes")
def list_clientes(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    team = team_of(current_user.get("email"), _user(current_user))
    itens = (db.query(CustomerDomain).filter(CustomerDomain.team == team)
             .order_by(CustomerDomain.domain.asc()).all())
    return {
        "team": team,
        "total": len(itens),
        "itens": [{"domain": c.domain, "kind": c.kind,
                   "created_at": c.created_at.isoformat() if c.created_at else None}
                  for c in itens],
    }


def _reclassificar_dominios(db: Session, current_user: dict, dominios: List[str]) -> int:
    """Reaplica o tier nas fichas do usuário com esses domínios."""
    if not dominios:
        return 0
    leads = db.query(Lead).filter(Lead.user_id == _user(current_user),
                                  Lead.domain.in_(dominios)).all()
    for lead in leads:
        apply_tier(db, lead, _user(current_user), current_user.get("email"))
    return len(leads)


@router.post("/clientes")
def add_clientes(
    body: ClientesRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Cola uma lista de domínios (um por linha, vírgula ou CSV)."""
    if body.kind not in _KINDS:
        raise HTTPException(status_code=422, detail="Tipo inválido. Use: skymail ou revenda.")
    team = team_of(current_user.get("email"), _user(current_user))
    dominios = [d.lower() for d in parse_domains(body.texto, limit=20_000)]
    if not dominios:
        raise HTTPException(status_code=422, detail="Nenhum domínio válido encontrado no texto.")
    existentes = {d for (d,) in db.query(CustomerDomain.domain).filter(
        CustomerDomain.team == team, CustomerDomain.domain.in_(dominios))}
    novos = [d for d in dict.fromkeys(dominios) if d not in existentes]
    for d in novos:
        db.add(CustomerDomain(team=team, domain=d, kind=body.kind, created_by=_user(current_user)))
    db.flush()
    reclass = _reclassificar_dominios(db, current_user, novos)
    db.commit()
    return {
        "success": True,
        "message": f"{len(novos)} domínio(s) adicionado(s) à base de clientes"
                   + (f"; {reclass} ficha(s) reclassificada(s) como Tier 4." if reclass else "."),
        "adicionados": len(novos),
        "ignorados": len(dominios) - len(novos),
        "reclassificados": reclass,
    }


@router.delete("/clientes/{domain}", status_code=204)
def delete_cliente(
    domain: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    team = team_of(current_user.get("email"), _user(current_user))
    row = db.query(CustomerDomain).filter(CustomerDomain.team == team,
                                          CustomerDomain.domain == domain.lower()).first()
    if not row:
        raise HTTPException(status_code=404, detail="Domínio não está na base de clientes.")
    db.delete(row)
    db.flush()
    _reclassificar_dominios(db, current_user, [domain.lower()])
    db.commit()
    return Response(status_code=204)
