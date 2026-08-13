"""
Enriquecimento de um domínio para um usuário — cache e persistência.

Vive fora do router porque existem dois caminhos até aqui: a busca avulsa
(`POST /api/enrich`, síncrona) e a fila de lote (`services/jobs.py`). Regra
duplicada em dois lugares é regra que vai divergir; aqui ela é escrita uma vez.

Ordem das decisões (a mesma nos dois caminhos):
  1. ficha recente da versão atual  → serve do cache, sem tocar a rede
  2. tentativa recente do domínio   → reaproveita a ficha (inclui a que morreu
                                      no meio e a de versão antiga)
  3. grava a ficha "pending" e só então coleta
"""
import logging
from dataclasses import dataclass
from datetime import datetime, UTC, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models.database import Lead, Profile, HIDDEN_LEAD_STATUSES
from services._utils import normalize_domain
from services.domain_finder import find_domain
from services.importer import looks_like_domain
from services.enricher import enrich_company, ENRICHMENT_VERSION
from services.people.waterfall import ingest_enrichment

logger = logging.getLogger(__name__)

# Fichas com menos de 7 dias são servidas do cache
CACHE_TTL_DAYS = 7

# Resultados possíveis de uma tentativa de enriquecimento.
RESULT_CACHED = "cached"       # já tínhamos a ficha
RESULT_ENRICHED = "enriched"   # coleta completa
RESULT_PARTIAL = "partial"     # coletou parte dos campos-alvo
RESULT_FAILED = "failed"       # coleta não trouxe nada aproveitável
RESULT_ERROR = "error"         # exceção durante a coleta


@dataclass
class EnrichOutcome:
    """O que aconteceu com uma tentativa — quem chama decide como apresentar."""
    result: str
    lead: Optional[Lead] = None
    message: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.result in (RESULT_CACHED, RESULT_ENRICHED, RESULT_PARTIAL)


def _recent_leads(db: Session, domain: str, user_id: str, include_hidden: bool = False):
    """Fichas do domínio dentro da janela de cache, mais recente primeiro."""
    cutoff = datetime.now(UTC) - timedelta(days=CACHE_TTL_DAYS)
    query = db.query(Lead).filter(
        Lead.user_id == user_id,
        Lead.domain == domain,
        Lead.created_at >= cutoff,
    )
    if not include_hidden:
        query = query.filter(Lead.status.notin_(HIDDEN_LEAD_STATUSES))
    return query.order_by(Lead.created_at.desc())


def find_cached_lead(db: Session, domain: str, user_id: str) -> Optional[Lead]:
    """
    Ficha recente do mesmo domínio para o mesmo usuário, se existir.

    Ficha gerada por uma versão anterior da coleta não conta como cache: a
    correção que a tornou obsoleta precisa chegar ao usuário na próxima busca,
    não daqui a 7 dias.
    """
    return _recent_leads(db, domain, user_id).filter(
        Lead.enrichment_version == ENRICHMENT_VERSION
    ).first()


def find_previous_attempt(db: Session, domain: str, user_id: str) -> Optional[Lead]:
    """
    Tentativa recente deste domínio — inclusive as que não terminaram (status
    "pending", tipicamente uma função serverless morta no meio da coleta).
    Reaproveitá-la é o que impede a mesma empresa virar duas fichas.
    """
    return _recent_leads(db, domain, user_id, include_hidden=True).first()


def enrich_existing_lead(db: Session, profile: Profile, lead: Lead) -> EnrichOutcome:
    """
    Enriquece uma ficha que já existe — na prática, as que vieram de planilha.

    Planilha de prospecção quase sempre traz nome e LinkedIn, não o site.
    Quando falta o domínio, ele é descoberto pelo nome antes da coleta; sem
    isso a linha inteira seria inaproveitável. Depois disso o caminho é o
    mesmo de qualquer busca.
    """
    domain = lead.domain or ""
    if not domain and looks_like_domain(lead.raw_input_domain or ""):
        domain = normalize_domain(lead.raw_input_domain)

    if not domain and lead.company_name:
        found = find_domain(lead.company_name, lead.linkedin_url, lead.location)
        if found.get("domain") and found.get("confidence") in ("high", "medium"):
            domain = found["domain"]
            lead.domain = domain
            lead.website = f"https://{domain}"
            if not lead.raw_input_domain:
                lead.raw_input_domain = domain
            db.commit()
            logger.info(
                "Domínio descoberto lead=%s domain=%s conf=%s",
                lead.id, domain, found.get("confidence"),
            )

    if not domain:
        lead.status = "failed"
        db.commit()
        return EnrichOutcome(
            result=RESULT_ERROR,
            lead=lead,
            message="Não encontrei o site desta empresa. Preencha a coluna Domínio para enriquecer.",
            error="dominio_desconhecido",
        )

    outcome = enrich_for_user(db, profile, domain, existing_lead=lead)
    return outcome


def enrich_for_user(db: Session, profile: Profile, raw_domain: str,
                    existing_lead: Optional[Lead] = None) -> EnrichOutcome:
    """
    Enriquece um domínio para o dono do `profile`. Commita o que precisa
    commitar e nunca levanta exceção de coleta.

    `existing_lead` reaproveita uma ficha já criada (linha de planilha) em vez
    de abrir outra — senão a mesma empresa apareceria duas vezes no histórico,
    uma com as células originais e outra com os dados coletados.
    """
    domain = normalize_domain(raw_domain)
    if not domain:
        return EnrichOutcome(
            result=RESULT_ERROR, message="Domínio inválido.", error="dominio_vazio",
        )

    if existing_lead is not None:
        return _run_enrichment(db, profile, domain, raw_domain, existing_lead)

    cached = find_cached_lead(db, domain, profile.id)
    if cached:
        logger.info("Cache hit for domain=%s user=%s", domain, profile.id)
        return EnrichOutcome(
            result=RESULT_CACHED, lead=cached, message="Dados carregados do cache.",
        )

    # A ficha nasce ANTES da coleta: se o processo morrer no meio, a próxima
    # tentativa continua nesta mesma linha em vez de abrir outra.
    previous = find_previous_attempt(db, domain, profile.id)
    lead = previous if previous is not None else Lead(
        user_id=profile.id, raw_input_domain=raw_domain, domain=domain, status="pending",
    )
    return _run_enrichment(db, profile, domain, raw_domain, lead)


def _run_enrichment(db: Session, profile: Profile, domain: str, raw_domain: str,
                    lead: Lead) -> EnrichOutcome:
    """Reserva a ficha, coleta e grava. Único ponto que fala com a coleta."""
    if lead.id is None:
        db.add(lead)
        db.commit()
        db.refresh(lead)

    try:
        data = enrich_company(raw_domain)
    except Exception as e:
        lead.status = "failed"
        db.commit()
        logger.exception("Enrichment failed for domain=%s: %s", domain, e)
        return EnrichOutcome(
            result=RESULT_ERROR, lead=lead,
            message=f"Erro ao enriquecer: {e}", error=str(e)[:500],
        )

    for key, value in data.items():
        if hasattr(Lead, key):
            setattr(lead, key, value)
    lead.user_id = profile.id
    db.commit()
    db.refresh(lead)

    # Alimenta o banco global de contatos: empresa, padrão de e-mail do domínio
    # e dados públicos do CNPJ. Roda DEPOIS do commit da ficha, em transação
    # própria — uma falha aqui não pode desfazer a coleta que já deu certo.
    try:
        ingest_enrichment(db, data)
        db.commit()
    except Exception:
        logger.exception("ingest_enrichment falhou domain=%s", domain)
        db.rollback()

    status = data.get("status")
    logger.info("Enriched domain=%s status=%s user=%s", domain, status, profile.id)

    if status == "failed":
        return EnrichOutcome(
            result=RESULT_FAILED, lead=lead,
            message="Não conseguimos coletar dados deste domínio.",
        )
    return EnrichOutcome(
        result=RESULT_PARTIAL if status == "partial" else RESULT_ENRICHED,
        lead=lead, message="Enriquecimento concluído.",
    )
