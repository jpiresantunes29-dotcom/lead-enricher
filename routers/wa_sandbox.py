"""
Simulador da IA de WhatsApp: rotas da aba de teste.

Tudo aqui é autenticado e **nada aqui fala com a Meta**. É a diferença que
justifica um router separado de `wa.py`: lá dentro cada rota ou recebe da Meta
ou envia para ela, e uma rota de teste no meio dessas seria uma linha a mais
para conferir toda vez que alguém mexesse no envio de verdade.

A sessão de teste vive na memória do processo (`services/wa/sandbox.py`), não
no banco. Conversa de teste em `conversations` entraria nas métricas — taxa de
resposta, handoffs, convites — e ninguém quer descobrir depois que o número do
dashboard inclui os testes de quinta à noite.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from middleware.auth import get_current_user
from models.schemas import (
    SandboxMessageRequest, SandboxResetRequest, SandboxSession, SandboxStatus,
    SandboxTurn, SandboxMessageOut,
)
from services.wa import brain, gate, sandbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wa/sandbox", tags=["whatsapp-simulador"])


def _mensagem(m) -> SandboxMessageOut:
    return SandboxMessageOut(
        direction=m.direction, body=m.body, created_at=m.created_at,
        sent_by=m.sent_by, intent_detected=m.intent_detected,
    )


def _turno(t) -> SandboxTurn:
    return SandboxTurn(
        acao=t.acao, intencao=t.intencao, confianca=t.confianca,
        confiavel=t.confiavel, texto=t.texto, motivo=t.motivo, erro=t.erro,
        fora_do_horario=t.fora_do_horario, ms=t.ms,
    )


def _sessao(s) -> SandboxSession:
    return SandboxSession(
        empresa=s.empresa,
        mensagens=[_mensagem(m) for m in s.mensagens],
        turnos=[_turno(t) for t in s.turnos],
    )


@router.get("/status", response_model=SandboxStatus)
def status_do_simulador(current_user: dict = Depends(get_current_user)):
    """
    O que o simulador consegue fazer agora.

    A tela usa isto para explicar o que falta em vez de simplesmente falhar no
    primeiro envio: sem `ANTHROPIC_API_KEY` não há classificação nenhuma, e
    descobrir isso por uma mensagem de erro genérica custa meia hora de dúvida.
    """
    pode, fora = gate.service_window()
    return SandboxStatus(
        ia_configurada=brain.is_configured(),
        modelo=brain._MODEL if brain.is_configured() else None,
        confianca_minima=brain.CONFIANCA_MINIMA,
        intencoes=list(brain.INTENCOES),
        dentro_do_horario=pode and not fora,
        pode_enviar_agora=pode,
        fora_do_horario=fora,
    )


@router.get("", response_model=SandboxSession)
def ver_sessao(current_user: dict = Depends(get_current_user)):
    """A conversa de teste como ela está."""
    return _sessao(sandbox.sessao(current_user.get("sub")))


@router.post("/reset", response_model=SandboxSession)
def reiniciar_sessao(
    body: SandboxResetRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Começa uma conversa de teste do zero.

    Trocar o nome da empresa importa: ele vai no prompt, e parte do que se está
    testando é como a IA usa o contexto do lead.
    """
    return _sessao(sandbox.reiniciar(current_user.get("sub"), body.empresa))


@router.post("/message", response_model=SandboxTurn)
def mensagem_do_lead(
    body: SandboxMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    O lead fictício escreve. Devolve o raio-x do que a IA fez com a mensagem.

    Síncrono de propósito: a tela mostra "digitando…" durante a espera e a
    resposta chega no lugar dela. Um turno leva o tempo de uma chamada ao
    modelo — os mesmos segundos que a produção leva.
    """
    texto = (body.texto or "").strip()
    if not texto:
        raise HTTPException(status_code=422, detail="Escreva a mensagem antes de enviar.")
    turno = sandbox.enviar_do_lead(
        current_user.get("sub"), texto, ignorar_horario=body.ignorar_horario,
    )
    return _turno(turno)
