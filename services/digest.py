"""
Resumo diário por e-mail: o que aconteceu nas últimas 24 horas e o que está
esperando resposta agora.

Chamado uma vez por dia pelo cron (`POST /api/internal/digest`, ver
`routers/internal.py`). Um usuário sem nenhuma atividade no período E sem
nada pendente não recebe nada — o resumo existe para chamar atenção para o
que precisa de ação, não para virar um e-mail que ninguém abre.
"""
import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from models.database import Conversation, Lead, Profile, WaMessage, utcnow
from services import mailer
from services.wa import gate

logger = logging.getLogger(__name__)

# Janela do resumo. Não é configurável por usuário — o cron roda uma vez por
# dia, e a janela precisa cobrir exatamente o intervalo entre duas rodadas.
JANELA_HORAS = 24


def _resumo_do_usuario(db: Session, user_id: str, desde) -> dict:
    novos_leads = (
        db.query(Lead)
        .filter(Lead.user_id == user_id, Lead.created_at >= desde)
        .count()
    )

    conversas_iniciadas = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id, Conversation.created_at >= desde)
        .count()
    )

    respostas_recebidas = (
        db.query(WaMessage)
        .join(Conversation, WaMessage.conversation_id == Conversation.id)
        .filter(
            Conversation.user_id == user_id,
            WaMessage.direction == "in",
            WaMessage.created_at >= desde,
        )
        .count()
    )

    # Pendência é estado atual, não "do dia": uma conversa parada há 3 dias
    # continua sendo a coisa mais importante do resumo de hoje.
    conversas = db.query(Conversation).filter(Conversation.user_id == user_id).all()
    aguardando_voce = sum(1 for c in conversas if gate.aguardando_voce(c))

    return {
        "novos_leads": novos_leads,
        "conversas_iniciadas": conversas_iniciadas,
        "respostas_recebidas": respostas_recebidas,
        "aguardando_voce": aguardando_voce,
    }


def _tem_algo_para_contar(resumo: dict) -> bool:
    return any(resumo.values())


def _assunto(resumo: dict) -> str:
    if resumo["aguardando_voce"]:
        return f"LeadEnricher: {resumo['aguardando_voce']} conversa(s) esperando você"
    return f"LeadEnricher: {resumo['novos_leads']} novo(s) lead(s) hoje"


def _texto(resumo: dict) -> str:
    linhas = ["Resumo das últimas 24 horas:", ""]
    linhas.append(f"- {resumo['novos_leads']} novo(s) lead(s) capturado(s)")
    linhas.append(f"- {resumo['conversas_iniciadas']} conversa(s) de WhatsApp iniciada(s)")
    linhas.append(f"- {resumo['respostas_recebidas']} resposta(s) recebida(s) de leads")
    if resumo["aguardando_voce"]:
        linhas.append("")
        linhas.append(
            f"⚠️ {resumo['aguardando_voce']} conversa(s) esperando sua resposta agora."
        )
    return "\n".join(linhas)


def _html(resumo: dict) -> str:
    itens = "".join(
        f"<li>{valor} {rotulo}</li>"
        for valor, rotulo in (
            (resumo["novos_leads"], "novo(s) lead(s) capturado(s)"),
            (resumo["conversas_iniciadas"], "conversa(s) de WhatsApp iniciada(s)"),
            (resumo["respostas_recebidas"], "resposta(s) recebida(s) de leads"),
        )
    )
    aviso = (
        f"<p><strong>⚠️ {resumo['aguardando_voce']} conversa(s) esperando "
        "sua resposta agora.</strong></p>"
        if resumo["aguardando_voce"] else ""
    )
    return f"<p>Resumo das últimas 24 horas:</p><ul>{itens}</ul>{aviso}"


def enviar_para_todos(db: Session) -> dict:
    """
    Manda o resumo diário para cada usuário com e-mail conhecido e o digest
    ligado. Devolve as contagens da rodada — é o que o cron loga, para uma
    falha de envio aparecer no log em vez de só no silêncio da caixa de entrada.
    """
    desde = utcnow() - timedelta(hours=JANELA_HORAS)
    perfis = (
        db.query(Profile)
        .filter(Profile.digest_diario.is_(True), Profile.email.isnot(None))
        .all()
    )

    enviados = 0
    sem_atividade = 0
    falhas = 0
    for perfil in perfis:
        resumo = _resumo_do_usuario(db, perfil.id, desde)
        if not _tem_algo_para_contar(resumo):
            sem_atividade += 1
            continue
        ok = mailer.send(
            to=perfil.email,
            subject=_assunto(resumo),
            text=_texto(resumo),
            html=_html(resumo),
        )
        if ok:
            enviados += 1
        else:
            falhas += 1
            logger.warning("Digest diário não enviado para user_id=%s", perfil.id)

    return {
        "total_usuarios": len(perfis),
        "enviados": enviados,
        "sem_atividade": sem_atividade,
        "falhas": falhas,
    }
