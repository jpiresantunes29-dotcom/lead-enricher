"""
Rotas de manutenção, chamadas por agendador — não por gente.

Protegidas por `CRON_SECRET` (header `Authorization: Bearer <segredo>`, que é
o formato que o cron da Vercel envia). Sem o segredo configurado, as rotas
respondem 503: endpoint de manutenção aberto é endpoint que qualquer um usa
para gastar o tempo de função do deploy ou apagar dados.
"""
import hmac
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from models.database import get_db
from services import jobs
from services.wa import orchestrator

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


@router.get("/preflight", dependencies=[Depends(require_cron_secret)])
def prontidao(db: Session = Depends(get_db)):
    """
    O que precisa estar no lugar antes de ligar — e o que acontece se não estiver.

    Protegida pelo segredo do cron porque a resposta é um mapa do que está e do
    que não está configurado no servidor: útil para quem opera, útil demais
    para quem estiver sondando.
    """
    from services import preflight
    return preflight.verificar(db=db).como_dict()


@router.post("/wa/pending", dependencies=[Depends(require_cron_secret)])
def responder_pendentes(db: Session = Depends(get_db)):
    """
    Responde as conversas em que o lead falou por último e ficou sem resposta.

    Duas situações caem aqui e as duas são silêncio, que é o pior resultado
    possível: o lead que escreveu de madrugada (o portão recusou o envio na
    hora) e o turno que não chegou a rodar porque a função foi interrompida no
    meio. Sem esta rodada, a mensagem dele ficaria parada sem ninguém saber.
    """
    return {"ok": True, **orchestrator.rodar_pendentes(db)}
