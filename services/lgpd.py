"""
Conformidade LGPD — purga automática de dados pessoais inativos (proposta v3 §7.5).
Após 90 dias sem atividade no lead, decisores e seus dados de contato são anonimizados.
"""
import logging
from datetime import datetime, UTC, timedelta

from sqlalchemy.orm import Session
from models.database import DecisionMaker, Activity, Lead

logger = logging.getLogger(__name__)

# Período de retenção em dias (LGPD/GDPR recomenda 30-90)
RETENTION_DAYS = 90


def anonymize_decision_maker(dm: DecisionMaker):
    """Anonymiza um decisor removendo PII (nome, emails, telefone)."""
    dm.name = "***"
    dm.title_found = None
    dm.title_searched = None
    dm.linkedin_url = None
    dm.probable_emails = []
    dm.phone = None
    dm.email_confidence = None
    dm.created_at = datetime.now(UTC)
    logger.info(f"Anonymized decision maker {dm.id}")


def purge_inactive_decision_makers(db: Session) -> int:
    """
    Purga decisores de leads sem atividade nos últimos RETENTION_DAYS dias.
    Retorna contagem de registros anon imizados.
    """
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)

    # Leads inativos (última atividade anterior ao cutoff)
    inactive_lead_ids = (
        db.query(Lead.id)
        .outerjoin(Activity, Lead.id == Activity.lead_id)
        .filter(
            (Activity.id.is_(None))  # Nenhuma atividade
            | (Activity.created_at < cutoff)  # Última atividade antiga
        )
        .distinct()
        .all()
    )

    if not inactive_lead_ids:
        return 0

    # Anonimiza decisores desses leads
    dms = db.query(DecisionMaker).filter(
        DecisionMaker.lead_id.in_([l[0] for l in inactive_lead_ids])
    ).all()

    for dm in dms:
        anonymize_decision_maker(dm)

    db.commit()
    logger.info(f"LGPD purge: anonymized {len(dms)} decision makers")
    return len(dms)
