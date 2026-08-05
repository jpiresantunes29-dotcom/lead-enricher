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
from routers.billing import _maybe_reset_quota
from services import domain_list, jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["lote"])
limiter = Limiter(key_func=rate_limit_key)


def _quota_restante(profile) -> int | None:
    """None = ilimitado."""
    if not profile.searches_limit or profile.searches_limit <= 0:
        return None
    return max(0, profile.searches_limit - (profile.searches_used or 0))


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
    profile = get_or_create_profile(db, user_id)
    if _maybe_reset_quota(profile):
        db.commit()

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

    # O lote é criado inteiro mesmo sem cota para tudo: os primeiros rodam
    # agora e o resto espera a renovação do ciclo, em vez de o usuário ter que
    # montar a lista de novo. O aviso vai na resposta.
    restante = _quota_restante(profile)
    batch_id, criados = jobs.create_batch(db, user_id, dominios)

    return BatchCreateResponse(
        batch_id=batch_id,
        total=len(criados),
        ignorados=ignorados,
        quota_restante=restante,
        cabe_na_quota=restante is None or restante >= len(criados),
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
