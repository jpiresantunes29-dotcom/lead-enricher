from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from models.database import get_db, Lead
from services.exporter import build_excel, build_csv
from middleware.auth import get_current_user

router = APIRouter(prefix="/api", tags=["export"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv; charset=utf-8"


@router.get("/export/{lead_id}")
def export_single(
    lead_id: int,
    fmt: str = Query("xlsx", alias="format", pattern="^(xlsx|csv)$"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    name = (lead.company_name or f"lead_{lead_id}").replace(" ", "_").lower()
    if fmt == "csv":
        return Response(
            content=build_csv([lead]),
            media_type=CSV_MIME,
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )
    return Response(
        content=build_excel([lead]),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
    )


@router.get("/export")
def export_all(
    fmt: str = Query("xlsx", alias="format", pattern="^(xlsx|csv)$"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    leads = (
        db.query(Lead)
        .filter(Lead.user_id == user_id, Lead.status != "failed")
        .order_by(Lead.created_at.desc())
        .all()
    )
    if not leads:
        raise HTTPException(status_code=404, detail="Nenhum lead encontrado.")
    if fmt == "csv":
        return Response(
            content=build_csv(leads),
            media_type=CSV_MIME,
            headers={"Content-Disposition": 'attachment; filename="leads_enriquecidos.csv"'},
        )
    return Response(
        content=build_excel(leads),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="leads_enriquecidos.xlsx"'},
    )
