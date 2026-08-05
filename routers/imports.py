"""
Importação de planilhas de empresas.

Fluxo em duas etapas, sem trafegar a planilha inteira duas vezes:
  1. POST /api/import/preview     — sobe o arquivo; o servidor lê, guarda um
     rascunho (import_batches.draft_rows) e devolve as colunas reconhecidas,
     a contagem e as primeiras linhas.
  2. POST /api/import/{id}/commit — confirma; o rascunho vira leads, cada um
     com TODAS as células originais em Lead.cells.

Importar não consome cota (não há coleta externa aqui). O enriquecimento é o
passo seguinte e roda um lead por requisição — cada coleta leva 10–30 s e o
maxDuration da função na Vercel é 60 s.
"""
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from slowapi import Limiter
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, rate_limit_key
from models.database import ImportBatch, Lead, get_db
from models.schemas import (
    ImportBatchOut, ImportCommitRequest, ImportCommitResponse, ImportPreviewResponse,
)
from services.importer import (
    MAX_FILE_BYTES, MAX_ROWS, SpreadsheetError, identity_keys,
    build_template_csv, build_template_xlsx, parse_spreadsheet,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["import"])
limiter = Limiter(key_func=rate_limit_key)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv; charset=utf-8"

PREVIEW_ROWS = 50   # o resto o usuário vê na planilha, depois de importar


def _existing_keys(db: Session, user_id: str) -> set[str]:
    """Identidades que o usuário já tem (domínio, LinkedIn ou nome)."""
    rows = (
        db.query(Lead.domain, Lead.linkedin_url, Lead.company_name)
        .filter(Lead.user_id == user_id)
        .all()
    )
    keys = set()
    for domain, linkedin, name in rows:
        keys |= identity_keys({
            "domain": domain, "linkedin_url": linkedin, "company_name": name,
        })
    return keys


def _get_batch(db: Session, batch_id: str, user_id: str) -> ImportBatch:
    batch = (
        db.query(ImportBatch)
        .filter(ImportBatch.id == batch_id, ImportBatch.user_id == user_id)
        .first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Importação não encontrada.")
    return batch


@router.get("/import/template")
def download_template(
    fmt: str = Query("xlsx", alias="format", pattern="^(xlsx|csv)$"),
    current_user: dict = Depends(get_current_user),
):
    """Modelo de planilha com as colunas que o importador reconhece."""
    if fmt == "csv":
        return Response(
            content=build_template_csv(),
            media_type=CSV_MIME,
            headers={"Content-Disposition": 'attachment; filename="modelo_importacao.csv"'},
        )
    return Response(
        content=build_template_xlsx(),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="modelo_importacao.xlsx"'},
    )


@router.post("/import/preview", response_model=ImportPreviewResponse)
@limiter.limit("20/minute")
async def preview_import(
    request: Request,
    file: UploadFile = File(...),
    sheet: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    # Lê no máximo o limite + 1 byte: arquivo maior é rejeitado pelo parser sem
    # nunca carregar o resto na memória da função.
    content = await file.read(MAX_FILE_BYTES + 1)

    try:
        parsed = parse_spreadsheet(file.filename or "", content, sheet_name=sheet)
    except SpreadsheetError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Falha inesperada ao ler planilha: %s", e)
        raise HTTPException(status_code=422, detail="Não consegui ler esta planilha.")

    # Marca o que o usuário já tem — o commit decide se pula ou não
    already = _existing_keys(db, user_id)
    counts = dict(parsed["counts"])
    counts["duplicate_db"] = 0
    for row in parsed["rows"]:
        if row["status"] == "ok" and identity_keys(row["lead"]) & already:
            row["status"] = "duplicate_db"
            row["reason"] = "Esta empresa já está no seu histórico."
            counts["ok"] -= 1
            counts["duplicate_db"] += 1

    # Um rascunho por vez: o anterior do mesmo usuário já cumpriu seu papel
    db.query(ImportBatch).filter(
        ImportBatch.user_id == user_id, ImportBatch.status == "draft"
    ).delete(synchronize_session=False)

    batch = ImportBatch(
        id=str(uuid.uuid4()),
        user_id=user_id,
        filename=(file.filename or "planilha")[:500],
        sheet_name=parsed["sheet"][:255],
        columns=parsed["columns"],
        row_count=parsed["total_rows"],
        status="draft",
        draft_rows=parsed["rows"],
    )
    db.add(batch)
    db.commit()

    importable = counts["ok"] + counts["duplicate_file"]
    parts = [f"{importable} empresa(s) prontas para importar"]
    if counts["duplicate_db"]:
        parts.append(f"{counts['duplicate_db']} já no histórico")
    if counts["invalid"]:
        parts.append(f"{counts['invalid']} sem identificação")
    message = " · ".join(parts) + "."
    if parsed["truncated"]:
        message += f" O arquivo foi cortado nas primeiras {MAX_ROWS} linhas."

    logger.info(
        "Import preview user=%s file=%s sheet=%s rows=%d importable=%d",
        user_id, file.filename, parsed["sheet"], parsed["total_rows"], importable,
    )

    return ImportPreviewResponse(
        success=importable > 0,
        message=message,
        batch_id=batch.id,
        filename=batch.filename,
        sheet=parsed["sheet"],
        sheets=parsed["sheets"],
        columns=parsed["columns"],
        total_rows=parsed["total_rows"],
        importable=importable,
        counts=counts,
        truncated=parsed["truncated"],
        rows=parsed["rows"][:PREVIEW_ROWS],
    )


@router.post("/import/{batch_id}/commit", response_model=ImportCommitResponse)
@limiter.limit("10/minute")
def commit_import(
    request: Request,
    batch_id: str,
    body: ImportCommitRequest = Body(default=ImportCommitRequest()),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    batch = _get_batch(db, batch_id, user_id)
    if batch.status != "draft" or not batch.draft_rows:
        raise HTTPException(status_code=409, detail="Esta importação já foi concluída.")

    created = 0
    skipped = 0
    for row in batch.draft_rows:
        status = row.get("status")
        if status == "invalid":
            skipped += 1
            continue
        if status == "duplicate_db" and body.skip_existing:
            skipped += 1
            continue
        if status == "duplicate_file" and body.skip_duplicates:
            skipped += 1
            continue

        lead_data = row.get("lead") or {}
        lead = Lead(
            user_id=user_id,
            raw_input_domain=lead_data.get("domain") or lead_data.get("company_name") or "",
            domain=lead_data.get("domain"),
            website=lead_data.get("website"),
            company_name=lead_data.get("company_name"),
            linkedin_url=lead_data.get("linkedin_url"),
            corporate_email=lead_data.get("corporate_email"),
            phone=lead_data.get("phone"),
            sector=lead_data.get("sector"),
            location=lead_data.get("location"),
            description=lead_data.get("description"),
            mx_provider=lead_data.get("mx_provider"),
            employee_count=lead_data.get("employee_count"),
            status="imported",
            stage="novo",
            cells=row.get("cells") or {},
            import_batch_id=batch.id,
            sheet_row=row.get("row_number"),
        )
        # Sem score aqui de propósito: o scoring depende de sinais que só a
        # coleta traz (DNS/MX, LinkedIn verificado, decisores).
        db.add(lead)
        created += 1

    batch.status = "committed"
    batch.draft_rows = None
    batch.row_count = created
    batch.committed_at = datetime.now(UTC)
    db.commit()

    logger.info("Import commit user=%s batch=%s created=%d skipped=%d",
                user_id, batch.id, created, skipped)

    return ImportCommitResponse(
        success=created > 0,
        message=(f"{created} empresa(s) importada(s)." if created
                 else "Nenhuma empresa nova para importar."),
        batch_id=batch.id,
        created=created,
        skipped=skipped,
    )


@router.get("/import/batches", response_model=list[ImportBatchOut])
def list_batches(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    batches = (
        db.query(ImportBatch)
        .filter(ImportBatch.user_id == user_id, ImportBatch.status == "committed")
        .order_by(ImportBatch.created_at.desc())
        .all()
    )
    out = []
    for batch in batches:
        total = db.query(Lead).filter(
            Lead.user_id == user_id, Lead.import_batch_id == batch.id
        ).count()
        enriched = db.query(Lead).filter(
            Lead.user_id == user_id, Lead.import_batch_id == batch.id,
            Lead.status.notin_(("imported", "failed")),
        ).count()
        out.append(ImportBatchOut(
            id=batch.id, filename=batch.filename, sheet_name=batch.sheet_name,
            row_count=total, enriched_count=enriched, created_at=batch.created_at,
        ))
    return out


@router.delete("/import/batches/{batch_id}", status_code=204)
def delete_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Desfaz uma importação: remove o lote e todos os leads que vieram dele."""
    user_id = current_user.get("sub")
    batch = _get_batch(db, batch_id, user_id)
    leads = (
        db.query(Lead)
        .filter(Lead.user_id == user_id, Lead.import_batch_id == batch.id)
        .all()
    )
    # Um a um para o cascade do ORM levar junto decisores e atividades —
    # delete() em massa deixaria essas linhas órfãs.
    for lead in leads:
        db.delete(lead)
    removed = len(leads)
    db.delete(batch)
    db.commit()
    logger.info("Import batch removido user=%s batch=%s leads=%d", user_id, batch_id, removed)
