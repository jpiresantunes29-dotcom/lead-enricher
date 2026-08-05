"""
Limpeza das sessões de demonstração.

Cada visitante que abre o app sem cadastro vira um perfil `demo-<sufixo>` com
leads, atividades e créditos próprios. Isso é intencional — é o que deixa o
produto explorável sem cadastro —, mas nada aqui tem dono: o navegador some, o
perfil fica. Sem uma limpeza periódica, a tabela cresce para sempre com dados
que ninguém vai olhar de novo.

O que é apagado: perfis demo sem atividade há mais de DEMO_TTL_DAYS, com
tudo o que pendura neles. O banco global de contatos (Company/Person/
EmailPattern) NÃO é tocado: aquele conhecimento é do produto, não da sessão —
e foi pago em tempo de coleta.
"""
import logging
import os
from datetime import timedelta
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from middleware.auth import DEMO_USER_PREFIX
from models.database import (
    Activity, DecisionMaker, ExtensionToken, Job, Lead, Profile, Reveal, utcnow,
)

logger = logging.getLogger(__name__)

DEMO_TTL_DAYS = int(os.getenv("DEMO_TTL_DAYS", "7"))


def _perfis_expirados(db: Session, cutoff, limit: int):
    """
    Perfis demo cuja última atividade é anterior ao corte.

    "Atividade" é o mais recente entre a criação do perfil, sua última
    atualização e a última busca feita nele — quem voltou ontem para ver os
    leads que pesquisou na semana passada não pode perdê-los no meio da visita.
    """
    ultimo_lead = (
        db.query(Lead.user_id, func.max(Lead.created_at).label("ultimo"))
        .group_by(Lead.user_id)
        .subquery()
    )
    return (
        db.query(Profile)
        .outerjoin(ultimo_lead, ultimo_lead.c.user_id == Profile.id)
        .filter(
            Profile.id.startswith(DEMO_USER_PREFIX),
            or_(ultimo_lead.c.ultimo.is_(None), ultimo_lead.c.ultimo < cutoff),
            or_(Profile.updated_at.is_(None), Profile.updated_at < cutoff),
            or_(Profile.created_at.is_(None), Profile.created_at < cutoff),
        )
        .limit(limit)
        .all()
    )


def purge_demo_profiles(db: Session, ttl_days: Optional[int] = None,
                        limit: int = 200) -> dict:
    """
    Apaga perfis demo expirados e os dados presos a eles. Commita.

    `limit` existe para a rodada caber no tempo de uma função serverless: o que
    sobrar sai na próxima execução do cron.
    """
    ttl = DEMO_TTL_DAYS if ttl_days is None else ttl_days
    cutoff = utcnow() - timedelta(days=ttl)

    perfis = _perfis_expirados(db, cutoff, limit)
    if not perfis:
        return {"perfis": 0, "leads": 0, "atividades": 0, "jobs": 0, "reveals": 0,
                "tokens": 0, "ttl_dias": ttl}

    ids = [p.id for p in perfis]
    leads = db.query(Lead.id).filter(Lead.user_id.in_(ids)).all()
    lead_ids = [row[0] for row in leads]

    contagem = {"perfis": len(ids), "leads": len(lead_ids), "ttl_dias": ttl}

    if lead_ids:
        contagem["decisores"] = db.query(DecisionMaker).filter(
            DecisionMaker.lead_id.in_(lead_ids)
        ).delete(synchronize_session=False)
    contagem["atividades"] = db.query(Activity).filter(
        Activity.user_id.in_(ids)
    ).delete(synchronize_session=False)
    contagem["jobs"] = db.query(Job).filter(
        Job.user_id.in_(ids)
    ).delete(synchronize_session=False)
    contagem["reveals"] = db.query(Reveal).filter(
        Reveal.user_id.in_(ids)
    ).delete(synchronize_session=False)
    contagem["tokens"] = db.query(ExtensionToken).filter(
        ExtensionToken.user_id.in_(ids)
    ).delete(synchronize_session=False)
    db.query(Lead).filter(Lead.user_id.in_(ids)).delete(synchronize_session=False)
    db.query(Profile).filter(Profile.id.in_(ids)).delete(synchronize_session=False)

    db.commit()
    logger.info(
        "Limpeza demo: %d perfis, %d leads, %d atividades (ttl=%dd)",
        contagem["perfis"], contagem["leads"], contagem.get("atividades", 0), ttl,
    )
    return contagem
