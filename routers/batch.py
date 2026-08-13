"""
Enriquecimento em lote.

O pedido só enfileira e responde na hora; o processamento acontece em rodadas
curtas disparadas por quem estiver disponível — o navegador do usuário
enquanto a tela está aberta, o cron quando não está. Ver services/jobs.py.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from sqlalchemy.orm import Session

from middleware.auth import get_current_user, rate_limit_key
from models.database import get_db
from models.schemas import (
    BatchCreateRequest, BatchCreateResponse, BatchProgressOut, BatchRunResponse,
)
from routers.auth import get_or_create_profile
from services import domain_list, jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["lote"])
limiter = Limiter(key_func=rate_limit_key)


@router.post("/batches", response_model=BatchCreateResponse)
@limiter.limit("6/minute")
def criar_lote(
    request: Request,
    body: BatchCreateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Recebe domínios (lista ou texto/CSV colado) e enfileira um job por domínio."""
    user_id = current_user.get("sub")
    get_or_create_profile(db, user_id)

    brutos = list(body.domains or [])
    if body.text:
        brutos.extend(domain_list.parse(body.text))
    if not brutos:
        raise HTTPException(
            status_code=422,
            detail="Nenhum domínio reconhecido. Cole uma lista ou um CSV com a coluna do site.",
        )

    dominios = jobs.normalize_domains(brutos)
    if not dominios:
        raise HTTPException(status_code=422, detail="Nenhum domínio válido na lista.")

    ignorados = max(0, len(brutos) - len(dominios))

    batch_id, criados = jobs.create_batch(db, user_id, dominios)

    return BatchCreateResponse(
        batch_id=batch_id,
        total=len(criados),
        ignorados=ignorados,
        message=(
            f"{len(criados)} domínio(s) na fila."
            + (f" {ignorados} entrada(s) ignorada(s) por repetição ou formato." if ignorados else "")
        ),
    )


@router.post("/batches/{batch_id}/run", response_model=BatchRunResponse)
@limiter.limit("60/minute")
def rodar_lote(
    request: Request,
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Processa uma rodada do lote.

    É a tela do usuário que chama isto em sequência: cada chamada devolve
    quantos ainda faltam, e o front decide se pede outra. Assim o lote anda
    sem depender de cron — e sem nenhuma requisição passar do limite de tempo
    da função.
    """
    user_id = current_user.get("sub")
    progresso = jobs.batch_progress(db, batch_id, user_id)
    if progresso is None:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")

    resumo = jobs.run_pending(db, user_id=user_id, batch_id=batch_id)
    return BatchRunResponse(
        **resumo,
        progresso=BatchProgressOut(**jobs.batch_progress(db, batch_id, user_id)),
    )


@router.get("/batches/{batch_id}", response_model=BatchProgressOut)
def status_do_lote(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    progresso = jobs.batch_progress(db, batch_id, current_user.get("sub"))
    if progresso is None:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    return BatchProgressOut(**progresso)


@router.get("/batches")
def listar_lotes(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Últimos lotes do usuário — para a tela mostrar o que ficou pela metade."""
    return jobs.recent_batches(db, current_user.get("sub"))
