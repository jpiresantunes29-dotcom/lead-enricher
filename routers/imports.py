"""
Importação de planilhas de empresas.

Fluxo em duas etapas (o upload nunca escreve direto no banco):
  1. POST /api/import/preview  — envia o arquivo, recebe o mapeamento de
     colunas e o diagnóstico linha a linha.
  2. POST /api/import          — envia as linhas confirmadas, que viram leads
     com status "imported".

Os leads importados entram sem consumir cota (não há coleta externa aqui).
O enriquecimento é opcional e acontece depois, um domínio por requisição, via
POST /api/leads/{id}/enrich — cada um consome 1 busca da cota, igual à busca
manual. Um por requisição porque a função da Vercel tem maxDuration de 60 s e
um enriquecimento leva de 10 a 30 s.
"""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from slowapi import Limiter
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, rate_limit_key
from models.database import Lead, get_db
from models.schemas import (
    ImportCommitRequest, ImportCommitResponse, ImportedLeadOut,
    ImportPreviewResponse, ImportRowOut,
)
from services._utils import normalize_domain
from services.importer import (
    MAX_FILE_BYTES, MAX_ROWS, SpreadsheetError, TEXT_FIELDS,
    build_template_csv, build_template_xlsx, parse_spreadsheet,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["import"])
limiter = Limiter(key_func=rate_limit_key)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv; charset=utf-8"


def _existing_domains(db: Session, user_id: str, domains: set[str]) -> set[str]:
    """Domínios que o usuário já tem no histórico (em lotes, para caber no IN)."""
    found: set[str] = set()
    ordered = list(domains)
    for start in range(0, len(ordered), 200):
        chunk = ordered[start:start + 200]
        rows = (
            db.query(Lead.domain)
            .filter(Lead.user_id == user_id, Lead.domain.in_(chunk))
            .all()
        )
        found.update(d for (d,) in rows if d)
    return found


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
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    # Lê no máximo o limite + 1 byte: arquivo maior que isso é rejeitado pelo
    # parser sem nunca carregar o resto na memória da função.
    content = await file.read(MAX_FILE_BYTES + 1)

    try:
        parsed = parse_spreadsheet(file.filename or "", content)
    except SpreadsheetError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Falha inesperada ao ler planilha: %s", e)
        raise HTTPException(status_code=422, detail="Não consegui ler esta planilha.")

    # Marca o que o usuário já tem no histórico — evita reimportar duplicado
    candidates = {
        r["data"]["domain"] for r in parsed["rows"]
        if r["status"] == "ok" and r["data"].get("domain")
    }
    already = _existing_domains(db, user_id, candidates) if candidates else set()
    for row in parsed["rows"]:
        if row["status"] == "ok" and row["data"].get("domain") in already:
            row["status"] = "duplicate_db"
            row["reason"] = "Este domínio já está no seu histórico."

    importable = sum(1 for r in parsed["rows"] if r["status"] == "ok")

    parts = [f"{importable} empresa(s) prontas para importar"]
    if len(parsed["rows"]) - importable:
        parts.append(f"{len(parsed['rows']) - importable} ignorada(s)")
    message = " · ".join(parts) + "."
    if parsed["truncated"]:
        message += f" A planilha foi cortada nas primeiras {MAX_ROWS} linhas."

    logger.info(
        "Import preview user=%s file=%s rows=%d importable=%d",
        user_id, file.filename, parsed["total_rows"], importable,
    )

    return ImportPreviewResponse(
        success=importable > 0,
        message=message,
        columns=parsed["columns"],
        mapping=parsed["mapping"],
        unmapped=parsed["unmapped"],
        total_rows=parsed["total_rows"],
        importable=importable,
        truncated=parsed["truncated"],
        rows=[ImportRowOut(**r) for r in parsed["rows"]],
    )


@router.post("/import", response_model=ImportCommitResponse)
@limiter.limit("10/minute")
def commit_import(
    request: Request,
    body: ImportCommitRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not body.rows:
        raise HTTPException(status_code=422, detail="Nenhuma linha para importar.")
    if len(body.rows) > MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"Máximo de {MAX_ROWS} empresas por importação.",
        )

    user_id = current_user.get("sub")

    # Revalida no servidor — o preview é só uma sugestão, o cliente pode editar
    normalized: dict[str, dict] = {}
    for row in body.rows:
        domain = normalize_domain(row.domain or "")
        if not domain or "." not in domain:
            continue
        normalized.setdefault(domain, row.model_dump(exclude_none=True))

    if not normalized:
        raise HTTPException(status_code=422, detail="Nenhum domínio válido nas linhas enviadas.")

    already = _existing_domains(db, user_id, set(normalized)) if body.skip_existing else set()

    created: list[Lead] = []
    for domain, data in normalized.items():
        if domain in already:
            continue
        lead = Lead(
            user_id=user_id,
            raw_input_domain=domain,
            domain=domain,
            website=f"https://{domain}",
            status="imported",
            stage="novo",
        )
        for field in TEXT_FIELDS:
            value = data.get(field)
            if value:
                setattr(lead, field, value)
        if data.get("employee_count"):
            lead.employee_count = data["employee_count"]
        # Sem score aqui de propósito: o scoring depende de sinais que só a
        # coleta traz (DNS/MX, LinkedIn, decisores). Pontuar a planilha crua
        # encheria o dashboard de leads "prioridade baixa" que ninguém avaliou.
        db.add(lead)
        created.append(lead)

    db.commit()
    for lead in created:
        db.refresh(lead)

    skipped = len(body.rows) - len(created)
    logger.info("Import commit user=%s created=%d skipped=%d", user_id, len(created), skipped)

    return ImportCommitResponse(
        success=bool(created),
        message=(
            f"{len(created)} empresa(s) importada(s)."
            if created else "Nenhuma empresa nova para importar."
        ),
        created=len(created),
        skipped=skipped,
        leads=[ImportedLeadOut.model_validate(lead) for lead in created],
    )
