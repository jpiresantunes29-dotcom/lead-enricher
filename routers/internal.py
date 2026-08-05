"""
Rotas de manutenção, chamadas por agendador — não por gente.

Protegidas por `CRON_SECRET` (header `Authorization: Bearer <segredo>`, que é
o formato que o cron da Vercel envia). Sem o segredo configurado, as rotas
respondem 503: endpoint de manutenção aberto é endpoint que qualquer um usa
para gastar a sua cota de função ou apagar dados.
"""
import hmac
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from models.database import get_db
from services import demo_cleanup, jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal", tags=["interno"], include_in_schema=False)


def require_cron_secret(request: Request) -> None:
    """Comparação em tempo constante — o segredo não vaza por timing."""
    esperado = (os.getenv("CRON_SECRET") or "").strip()
    if not esperado:
        logger.error("CRON_SECRET ausente: rota interna recusada.")
        raise HTTPException(status_code=503, detail="Manutenção não configurada.")

    header = request.headers.get("authorization", "")
    recebido = header[7:] if header.lower().startswith("bearer ") else header
    if not hmac.compare_digest(recebido.strip(), esperado):
        raise HTTPException(status_code=401, detail="Não autorizado.")


@router.post("/jobs/run", dependencies=[Depends(require_cron_secret)])
def rodar_fila(db: Session = Depends(get_db)):
    """
    Uma rodada da fila de enriquecimento, para todos os usuários.

    Cobre quem fechou a aba antes do lote terminar — enquanto a tela está
    aberta, é o próprio navegador que empurra a fila (`/api/batches/{id}/run`).
    """
    resumo = jobs.run_pending(db)
    return {"ok": True, **resumo}


@router.post("/demo/cleanup", dependencies=[Depends(require_cron_secret)])
def limpar_demo(db: Session = Depends(get_db)):
    """Remove sessões de demonstração paradas há mais de DEMO_TTL_DAYS."""
    return {"ok": True, **demo_cleanup.purge_demo_profiles(db)}
