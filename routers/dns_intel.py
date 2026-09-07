"""
Relatório DNS completo de um lead, sob demanda.

Fica fora do enriquecimento de propósito: a varredura inteira (subdomínios em
logs de certificado, ASN de cada IP, banner HTTP, RDAP) leva ~15-25 s, tempo
que ninguém quer esperar para ver a ficha comercial. O painel só chama esta
rota quando o usuário abre a seção — e o resultado fica guardado no lead, para
a segunda abertura ser instantânea.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, rate_limit_key
from models.database import get_db, Lead
from models.schemas import DnsReportResponse
from services.dns_intel import FULL_REPORT_VERSION, get_full_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dns"])
limiter = Limiter(key_func=rate_limit_key)


@router.get("/leads/{lead_id}/dns", response_model=DnsReportResponse)
@limiter.limit("12/minute")
def lead_dns_report(
    request: Request,
    lead_id: int,
    refresh: bool = Query(False, description="Ignora o relatório guardado e coleta de novo."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    lead = (
        db.query(Lead)
        .filter(Lead.id == lead_id, Lead.user_id == current_user.get("sub"))
        .first()
    )
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    domain = lead.domain or lead.raw_input_domain
    if not domain:
        raise HTTPException(status_code=422, detail="Lead sem domínio.")

    stored = (lead.dns_report or {}).get("full")
    # Relatório de versão anterior não vale como cache: a correção que mudou a
    # coleta precisa aparecer na próxima abertura do painel.
    if stored and not refresh and stored.get("version") == FULL_REPORT_VERSION:
        return DnsReportResponse(success=True, cached=True, report=stored)

    try:
        report = get_full_report(domain)
    except Exception as e:
        logger.exception("Relatório DNS falhou domain=%s: %s", domain, e)
        raise HTTPException(status_code=500, detail=f"Erro ao coletar DNS: {e}")

    # JSON de coluna é imutável para o SQLAlchemy: sem dict novo, a alteração
    # não é detectada e o commit não grava nada.
    lead.dns_report = {**(lead.dns_report or {}), "full": report}
    try:
        db.commit()
    except Exception:
        logger.exception("Falha ao guardar relatório DNS lead=%s", lead_id)
        db.rollback()

    return DnsReportResponse(success=True, cached=False, report=report)
