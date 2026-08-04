from datetime import datetime, UTC, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from models.database import get_db, Lead
from services.exporter import build_excel, build_csv
from middleware.auth import get_current_user

router = APIRouter(prefix="/api", tags=["export"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv; charset=utf-8"

_PRESETS = ("today", "7days", "30days", "month", "all", "custom")


def _date_range(preset: str, date_start: Optional[str], date_end: Optional[str]) -> tuple:
    """Converte preset (ou datas customizadas) em (start, end) — end é exclusivo do dia seguinte."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if preset == "today":
        return today_start, now
    if preset == "7days":
        return now - timedelta(days=7), now
    if preset == "30days":
        return now - timedelta(days=30), now
    if preset == "month":
        return today_start.replace(day=1), now
    if preset == "all":
        return None, None
    if preset == "custom":
        if not date_start or not date_end:
            raise HTTPException(status_code=422, detail="Informe as duas datas do intervalo.")
        try:
            start = datetime.fromisoformat(date_start).replace(tzinfo=UTC)
            # Inclui o dia final inteiro
            end = datetime.fromisoformat(date_end).replace(tzinfo=UTC) + timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=422, detail="Datas em formato inválido.")
        if end <= start:
            raise HTTPException(status_code=422, detail="Data final deve ser após a inicial.")
        return start, end
    raise HTTPException(status_code=422, detail="Período inválido.")


@router.get("/export")
def export_all(
    fmt: str = Query("xlsx", alias="format", pattern="^(xlsx|csv)$"),
    preset: str = Query("all", pattern="^(today|7days|30days|month|all|custom)$"),
    date_start: Optional[str] = Query(None),
    date_end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    start, end = _date_range(preset, date_start, date_end)

    q = db.query(Lead).filter(Lead.user_id == user_id, Lead.status != "failed")
    if start is not None:
        q = q.filter(Lead.created_at >= start, Lead.created_at < end)
    leads = q.order_by(Lead.created_at.desc()).all()

    if not leads:
        raise HTTPException(status_code=404, detail="Nenhum lead encontrado para o período selecionado.")

    suffix = datetime.now(UTC).strftime("%Y%m%d")
    if fmt == "csv":
        return Response(
            content=build_csv(leads),
            media_type=CSV_MIME,
            headers={"Content-Disposition": f'attachment; filename="leads_{suffix}.csv"'},
        )
    return Response(
        content=build_excel(leads),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="leads_{suffix}.xlsx"'},
    )
