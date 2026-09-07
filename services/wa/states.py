"""
Transições de estado da conversa e do relacionamento.

Existe para que "pausar", "assumir" e "esta empresa é cliente" sejam **uma
escrita no banco**, e não um sinal que alguém precisa lembrar de checar. O
portão (`gate.can_send`) lê esse estado antes de cada envio; portanto pausar
já vale para a próxima mensagem, inclusive uma que esteja sendo redigida
neste instante.

Nenhuma função aqui commita. Quem chama decide o limite da transação — o
webhook precisa gravar mensagem e estado juntos, ou nenhum dos dois.
"""
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models.database import (
    AI_ACTIVE, AI_PAUSED, HUMAN_HANDOFF, STOPPED,
    AUDIT_ASSUMIDA, AUDIT_CONVERSA_ABERTA, AUDIT_ENCERRADA, AUDIT_PAUSADA,
    AUDIT_RELACIONAMENTO, AUDIT_RETOMADA,
    AuditLog, Conversation, Lead, RELATIONSHIP_BLOCKED, RELATIONSHIP_CUSTOMER,
    RELATIONSHIP_DO_NOT_CONTACT, RELATIONSHIP_LEAD, RELATIONSHIPS, utcnow,
)
from services.people import optout

logger = logging.getLogger(__name__)

#: Janela livre da Meta depois de cada mensagem do lead.
WINDOW_HOURS = 24

#: Quem provocou a transição. Sai como parâmetro em vez de ser adivinhado:
#: "conversa encerrada" por clique e "conversa encerrada" por gatilho da
#: automação são o mesmo estado e histórias completamente diferentes.
ATOR_HUMANO = "humano"
ATOR_IA = "ia"
ATOR_SISTEMA = "sistema"


def registrar(db: Session, acao: str, ator: str = ATOR_SISTEMA,
              conversa: Optional[Conversation] = None,
              lead: Optional[Lead] = None,
              detalhe: Optional[str] = None) -> None:
    """
    Anota uma decisão na trilha de auditoria. Não commita.

    Falha aqui nunca derruba a operação: perder uma linha de log é ruim, mas
    não gravar a pausa que o usuário pediu é bem pior.
    """
    user_id = (conversa.user_id if conversa else None) or (lead.user_id if lead else None)
    if not user_id:
        return
    try:
        db.add(AuditLog(
            user_id=user_id,
            conversation_id=conversa.id if conversa else None,
            lead_id=(conversa.lead_id if conversa else None) or (lead.id if lead else None),
            acao=acao, ator=ator, detalhe=(detalhe or "")[:500] or None,
        ))
    except Exception:
        logger.warning("Não foi possível registrar a auditoria de %s.", acao, exc_info=True)


def start(db: Session, lead: Lead, phone_e164: str, user_id: str,
          decision_maker_id: Optional[int] = None) -> Conversation:
    """
    Abre (ou reabre) a conversa de um lead.

    Não envia nada: quem inicia o contato é o humano, num segundo passo. Aqui
    só nasce o registro que o portão vai consultar.

    Reabrir uma conversa encerrada é permitido — o lead pode voltar meses
    depois. O que não se reabre é o estado: `relationship` continua sendo a
    palavra final, e uma conversa reaberta com um cliente segue barrada.
    """
    conversa = (
        db.query(Conversation)
        .filter(Conversation.lead_id == lead.id,
                Conversation.phone_e164 == phone_e164)
        .first()
    )
    if conversa is None:
        conversa = Conversation(
            lead_id=lead.id,
            decision_maker_id=decision_maker_id,
            user_id=user_id,
            phone_e164=phone_e164,
        )
        db.add(conversa)
        # Sem este flush a conversa ainda não tem id, e o registro de auditoria
        # nasceria solto — presente na tabela, invisível no histórico da
        # conversa, que é justamente onde alguém vai procurá-lo.
        db.flush()
    conversa.ai_status = AI_ACTIVE
    conversa.handoff_reason = None
    conversa.updated_at = utcnow()
    # O convite é a única ação do produto que gasta dinheiro. Fica na trilha
    # sempre, com quem mandou e para qual número.
    registrar(db, AUDIT_CONVERSA_ABERTA, ATOR_HUMANO, conversa=conversa,
              detalhe=f"Convite enviado para {phone_e164}.")
    return conversa


def pause(db: Session, conversa: Conversation, motivo: Optional[str] = None,
          ator: str = ATOR_HUMANO) -> Conversation:
    """O usuário pausou a automação. Conversa continua viva; a IA cala."""
    conversa.ai_status = AI_PAUSED
    conversa.handoff_reason = (motivo or "Pausada pelo usuário.")[:500]
    conversa.updated_at = utcnow()
    registrar(db, AUDIT_PAUSADA, ator, conversa=conversa, detalhe=conversa.handoff_reason)
    return conversa


def handoff(db: Session, conversa: Conversation, motivo: str,
            ator: str = ATOR_HUMANO) -> Conversation:
    """
    A conversa passa para o humano.

    Chamado tanto pelo botão "Assumir agora" quanto pelos gatilhos (pediu para
    falar com gente, começou a negociar, perguntou algo fora da base, resposta
    ambígua). Do ponto de vista do portão os dois casos são idênticos, e é
    assim que tem de ser: a IA não decide o que é grave, ela só levanta a mão.
    """
    conversa.ai_status = HUMAN_HANDOFF
    conversa.handoff_reason = (motivo or "Assumida pelo usuário.")[:500]
    conversa.updated_at = utcnow()
    registrar(db, AUDIT_ASSUMIDA, ator, conversa=conversa, detalhe=conversa.handoff_reason)
    return conversa


def resume(db: Session, conversa: Conversation,
           ator: str = ATOR_HUMANO) -> Conversation:
    """Devolve a conversa para a automação, se o relacionamento permitir."""
    conversa.ai_status = AI_ACTIVE
    conversa.handoff_reason = None
    conversa.updated_at = utcnow()
    registrar(db, AUDIT_RETOMADA, ator, conversa=conversa)
    return conversa


def stop(db: Session, conversa: Conversation, motivo: Optional[str] = None,
         ator: str = ATOR_HUMANO) -> Conversation:
    conversa.ai_status = STOPPED
    conversa.handoff_reason = (motivo or "Encerrada.")[:500]
    conversa.updated_at = utcnow()
    registrar(db, AUDIT_ENCERRADA, ator, conversa=conversa, detalhe=conversa.handoff_reason)
    return conversa


# ── Relacionamento: a trava estrutural ───────────────────────────────────────

def set_relationship(db: Session, lead: Lead, valor: str,
                     motivo: Optional[str] = None,
                     ator: str = ATOR_HUMANO) -> Lead:
    """
    Entrada única e validada para `Lead.relationship`.

    Um valor fora da lista não vira erro de banco (a coluna é um varchar); ele
    vira um lead que o portão recusa para sempre, sem ninguém entender por quê.
    Melhor recusar a escrita agora do que investigar o silêncio depois.
    """
    if valor not in RELATIONSHIPS:
        raise ValueError(
            f"Relacionamento inválido: {valor!r}. Use um de {', '.join(RELATIONSHIPS)}."
        )
    if valor == RELATIONSHIP_CUSTOMER:
        return mark_customer(db, lead, motivo or "Já é cliente.", ator)
    if valor == RELATIONSHIP_DO_NOT_CONTACT:
        return mark_do_not_contact(
            db, lead, motivo or "Pediu para não receber mensagens.", ator)
    if valor == RELATIONSHIP_BLOCKED:
        lead.relationship = RELATIONSHIP_BLOCKED
        registrar(db, AUDIT_RELACIONAMENTO, ator, lead=lead,
                  detalhe=f"{RELATIONSHIP_BLOCKED}: {motivo or 'Bloqueado.'}")
        _encerrar_conversas(db, lead, motivo or "Bloqueado.", ator)
        return lead
    return mark_lead(db, lead, ator)


def mark_customer(db: Session, lead: Lead, motivo: str = "Já é cliente.",
                  ator: str = ATOR_HUMANO) -> Lead:
    """
    A empresa é cliente. A automação nunca mais fala com ela.

    Encerra as conversas abertas na mesma transação: deixar `relationship` e
    `ai_status` divergirem por um instante é abrir espaço para um envio em
    andamento passar antes do portão reler.
    """
    lead.relationship = RELATIONSHIP_CUSTOMER
    registrar(db, AUDIT_RELACIONAMENTO, ator, lead=lead,
              detalhe=f"{RELATIONSHIP_CUSTOMER}: {motivo}")
    _encerrar_conversas(db, lead, motivo, ator)
    logger.info("Lead %s marcado como cliente; automação encerrada.", lead.id)
    return lead


def mark_do_not_contact(db: Session, lead: Lead,
                        motivo: str = "Pediu para não receber mensagens.",
                        ator: str = ATOR_HUMANO) -> Lead:
    """
    O lead pediu para parar.

    Duas gravações, de propósito. `relationship` protege este lead; o opt-out
    por hash protege o **número**, inclusive se ele reaparecer amanhã em outra
    empresa, por outra fonte, para outro usuário. Só a primeira seria uma
    promessa que a base quebra sozinha na próxima importação.
    """
    lead.relationship = RELATIONSHIP_DO_NOT_CONTACT
    registrar(db, AUDIT_RELACIONAMENTO, ator, lead=lead,
              detalhe=f"{RELATIONSHIP_DO_NOT_CONTACT}: {motivo}")
    for conversa in _conversas_abertas(db, lead):
        optout.register(db, "phone", conversa.phone_e164,
                        reason=motivo, source="whatsapp")
    _encerrar_conversas(db, lead, motivo, ator)
    logger.info("Lead %s marcado como não-contatar; número bloqueado.", lead.id)
    return lead


def mark_lead(db: Session, lead: Lead, ator: str = ATOR_HUMANO) -> Lead:
    """
    Devolve a empresa à condição de prospecto.

    Não desfaz opt-out: quem pediu para não receber continua bloqueado pelo
    hash do número, e é o portão que vai recusar. Corrigir uma marcação errada
    é uma coisa; cancelar o pedido de um titular é outra, e não se faz por um
    clique nosso.
    """
    lead.relationship = RELATIONSHIP_LEAD
    registrar(db, AUDIT_RELACIONAMENTO, ator, lead=lead,
              detalhe=f"{RELATIONSHIP_LEAD}: voltou a ser prospecto.")
    return lead


def _conversas_abertas(db: Session, lead: Lead):
    return (
        db.query(Conversation)
        .filter(Conversation.lead_id == lead.id, Conversation.ai_status != STOPPED)
        .all()
    )


def _encerrar_conversas(db: Session, lead: Lead, motivo: str,
                        ator: str = ATOR_SISTEMA) -> int:
    conversas = _conversas_abertas(db, lead)
    for conversa in conversas:
        stop(db, conversa, motivo, ator)
    return len(conversas)


# ── Registro das mensagens ───────────────────────────────────────────────────

def register_inbound(db: Session, conversa: Conversation, corpo: Optional[str],
                     quando=None) -> Conversation:
    """
    Mensagem recebida: reabre a janela de 24 h.

    A janela é da Meta e conta da última mensagem *do lead* — por isso ela
    nasce aqui e em nenhum outro lugar.
    """
    quando = quando or utcnow()
    conversa.last_inbound_at = quando
    conversa.window_expires_at = quando + timedelta(hours=WINDOW_HOURS)
    conversa.last_message_body = (corpo or "")[:500] or None
    conversa.updated_at = quando
    return conversa


def register_outbound(db: Session, conversa: Conversation, corpo: Optional[str],
                      quando=None) -> Conversation:
    """Mensagem enviada. Não mexe na janela: responder não estende prazo."""
    quando = quando or utcnow()
    conversa.last_outbound_at = quando
    conversa.last_message_body = (corpo or "")[:500] or None
    conversa.updated_at = quando
    return conversa
