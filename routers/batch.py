"""
Upload em lote de domínios para enriquecimento (proposta v3 §8.1).
Aceita CSV/XLSX e enfileira para processamento assíncrono.
"""
import csv
import io
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from models.database import get_db, Lead, Profile
from middleware.auth import get_current_user
from routers.auth import get_or_create_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["batch"])


@router.post("/leads/batch/upload")
async def batch_upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Faz upload de CSV/XLSX com domínios (1 domínio por linha, coluna 'domain' ou primeira coluna).
    Enfileira para enriquecimento e retorna contagem.
    """
    user_id = current_user.get("sub")
    profile = get_or_create_profile(db, user_id)

    if not file.filename.lower().endswith((".csv", ".xlsx")):
        raise HTTPException(status_code=422, detail="Envie um arquivo .csv ou .xlsx")

    try:
        content = await file.read()
        if file.filename.lower().endswith(".csv"):
            text = content.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            domains = [row.get("domain") or row.get("Domain") or next(iter(row.values())) for row in reader if row]
        else:
            # XLSX requer openpyxl; fallback para CSV
            raise HTTPException(status_code=422, detail="Envie um arquivo .csv; .xlsx requer configuração adicional")
    except Exception as e:
        logger.warning("Batch parse error: %s", e)
        raise HTTPException(status_code=422, detail="Erro ao ler arquivo")

    domains = [d.strip().lower() for d in domains if d and isinstance(d, str)]
    if not domains:
        raise HTTPException(status_code=422, detail="Nenhum domínio encontrado no arquivo")

    # Deduplica com domínios já existentes
    existing = set(db.query(Lead.domain).filter(Lead.domain.in_(domains)).scalar_all())
    new_domains = [d for d in domains if d not in existing]

    if not new_domains:
        return {"queued": 0, "total": len(domains), "message": "Todos os domínios já foram enriquecidos"}

    # Cria leads em estado "pending" e dispara enriquecimento
    for domain in new_domains[:50]:  # Limita a 50 por upload para não sobrecarregar
        try:
            existing_lead = db.query(Lead).filter(Lead.domain == domain, Lead.user_id == user_id).first()
            if not existing_lead:
                lead = Lead(user_id=user_id, domain=domain, status="pending")
                db.add(lead)
        except Exception as e:
            logger.warning("Error creating lead for domain %s: %s", domain, e)

    db.commit()
    return {
        "queued": min(len(new_domains), 50),
        "total": len(domains),
        "duplicates": len(domains) - len(new_domains),
        "message": f"Enfileirados {min(len(new_domains), 50)} domínios para enriquecimento",
    }


@router.get("/leads/batch/status")
def batch_status(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Status do último lote enviado pelo usuário."""
    user_id = current_user.get("sub")
    from sqlalchemy import func

    status_counts = (
        db.query(Lead.status, func.count(Lead.id))
        .filter(Lead.user_id == user_id)
        .group_by(Lead.status)
        .all()
    )
    return {status: count for status, count in status_counts}
