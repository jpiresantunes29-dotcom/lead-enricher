"""
Coexistência: o WhatsApp Business do celular, espelhado aqui.

Com o número conectado em modo Coexistência (services/wa/embedded.py), o app
do celular continua sendo usado — e a Meta passa a mandar para o webhook três
coisas além das mensagens recebidas:

  `history`              até ~6 meses de conversas, em lotes, uma vez.
  `smb_message_echoes`   o que o dono digitou **no celular**, em tempo real.
  `smb_app_state_sync`   a agenda de contatos do app.

Três regras que o resto do arquivo segue:

  **Nem todo contato é lead.** O histórico traz clientes, fornecedores e
  conhecidos. Eles viram conversas sem lead (`lead_id` nulo), sempre humanas:
  o portão já recusa a automação sem lead, e elas nascem em HUMAN_HANDOFF
  para nem chegar a tentar.

  **Histórico não é atividade.** Mensagem importada não abre janela de 24 h
  (a Meta só abre com mensagem recebida *depois* da conexão), não acende
  "aguardando você" e não acorda a IA. Por isso ela não toca em
  `last_inbound_at`/`last_outbound_at`, só na prévia e na ordem da lista.

  **O celular manda.** Quem responde pelo celular está conduzindo a conversa;
  a automação não pode responder por cima. Ver `apos_eco_do_celular`.

Nada aqui commita: quem chama (o webhook) grava tudo junto ou nada.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from models.database import (
    AI_ACTIVE, HUMAN_HANDOFF,
    HISTORICO_CONCLUIDO, HISTORICO_IMPORTANDO, HISTORICO_RECUSADO,
    Conversation, WaContact, WaMessage, WhatsAppConnection, utcnow,
)
from services.wa import states
from services.wa.formato import corpo_da_mensagem, e164, instante

logger = logging.getLogger(__name__)

#: Campos de webhook que só existem na Coexistência.
CAMPO_HISTORICO = "history"
CAMPO_ECOS = "smb_message_echoes"
CAMPO_AGENDA = "smb_app_state_sync"
CAMPOS = (CAMPO_HISTORICO, CAMPO_ECOS, CAMPO_AGENDA)

#: O dono não autorizou, no celular, compartilhar o histórico.
ERRO_HISTORICO_RECUSADO = 2593109

MOTIVO_SEM_LEAD = "Contato fora da base de leads — a conversa é sua."


def processar(db: Session, campo: str, valor: dict, dono: Optional[str]) -> None:
    """Despacha um `change` de Coexistência para quem trata o campo."""
    if not dono:
        # Sem dono não há em que conta gravar. Acontece com o número do
        # ambiente, que não é Coexistência — nada a fazer.
        logger.info("Webhook de Coexistência (%s) sem conta associada; ignorado.", campo)
        return
    if campo == CAMPO_HISTORICO:
        importar_historico(db, valor, dono)
    elif campo == CAMPO_ECOS:
        registrar_ecos(db, valor, dono)
    elif campo == CAMPO_AGENDA:
        sincronizar_agenda(db, valor, dono)


# ── Conversas de quem não é lead ─────────────────────────────────────────────

def nome_na_agenda(db: Session, dono: str, telefone: str) -> Optional[str]:
    contato = (
        db.query(WaContact)
        .filter(WaContact.user_id == dono, WaContact.phone_e164 == telefone)
        .first()
    )
    return contato.name if contato else None


def conversa_do_contato(db: Session, dono: str, telefone: str) -> Conversation:
    """
    A conversa deste usuário com este número — existente ou nova, sem lead.

    Procura em qualquer conversa do dono com o número, com ou sem lead: se o
    número já é de um lead em conversa, o que chegou pelo celular pertence a
    ela, não a uma segunda conversa paralela com a mesma pessoa.
    """
    conversa = (
        db.query(Conversation)
        .filter(Conversation.user_id == dono, Conversation.phone_e164 == telefone)
        .order_by(Conversation.updated_at.desc())
        .first()
    )
    if conversa is not None:
        return conversa

    conversa = Conversation(
        lead_id=None,
        user_id=dono,
        phone_e164=telefone,
        contact_name=nome_na_agenda(db, dono, telefone),
        ai_status=HUMAN_HANDOFF,
        handoff_reason=MOTIVO_SEM_LEAD,
    )
    db.add(conversa)
    db.flush()   # id antes de qualquer mensagem apontar para ela
    return conversa


def _ja_gravada(db: Session, wamid: Optional[str]) -> bool:
    return bool(wamid) and db.query(WaMessage.id).filter(
        WaMessage.wa_message_id == wamid).first() is not None


# ── Histórico ────────────────────────────────────────────────────────────────

def _conexao(db: Session, dono: str) -> Optional[WhatsAppConnection]:
    return (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.user_id == dono)
        .first()
    )


def importar_historico(db: Session, valor: dict, dono: str) -> int:
    """
    Grava um lote do histórico. Devolve quantas mensagens novas entraram.

    Idempotente pelo `wa_message_id`: a Meta reentrega o lote que não
    confirmamos a tempo, e lote repetido não pode duplicar a conversa.
    """
    conexao = _conexao(db, dono)
    novas = 0
    for bloco in valor.get("history") or []:
        erros = bloco.get("errors") or []
        if any((e or {}).get("code") == ERRO_HISTORICO_RECUSADO for e in erros):
            if conexao is not None:
                conexao.historico_status = HISTORICO_RECUSADO
            logger.info("O dono do número %s não autorizou importar o histórico.", dono)
            continue
        if erros:
            logger.warning("Lote de histórico com erro da Meta: %s",
                           [(e or {}).get("code") for e in erros])
            continue

        progresso = (bloco.get("metadata") or {}).get("progress")
        if conexao is not None and progresso is not None:
            try:
                progresso = max(0, min(100, int(progresso)))
            except (TypeError, ValueError):
                progresso = None
            if progresso is not None:
                # Lotes chegam fora de ordem; o progresso só anda para a frente.
                conexao.historico_progresso = max(conexao.historico_progresso or 0, progresso)
                conexao.historico_status = (HISTORICO_CONCLUIDO
                                            if conexao.historico_progresso >= 100
                                            else HISTORICO_IMPORTANDO)

        for thread in bloco.get("threads") or []:
            novas += _importar_thread(db, thread, dono)
    return novas


def _importar_thread(db: Session, thread: dict, dono: str) -> int:
    telefone = e164(thread.get("id"))
    if not telefone:
        return 0
    mensagens = thread.get("messages") or []
    if not mensagens:
        return 0

    conversa = conversa_do_contato(db, dono, telefone)
    novas = 0
    for msg in mensagens:
        wamid = msg.get("id")
        if _ja_gravada(db, wamid):
            continue
        # Na thread, `from` é o contato (recebida) ou o próprio número da
        # empresa (enviada). Comparar com o contato evita depender de como a
        # Meta escreve o número da empresa.
        recebida = e164(msg.get("from")) == telefone
        quando = instante(msg.get("timestamp")) or utcnow()
        corpo = corpo_da_mensagem(msg)
        status = ((msg.get("history_context") or {}).get("status") or "").lower() or None
        db.add(WaMessage(
            conversation_id=conversa.id,
            direction="in" if recebida else "out",
            wa_message_id=wamid,
            type=msg.get("type") or "text",
            body=corpo,
            status=status,
            sent_by=None if recebida else "human",
            created_at=quando,
        ))
        novas += 1
    db.flush()
    if novas:
        _reposicionar(db, conversa)
    return novas


def _reposicionar(db: Session, conversa: Conversation) -> None:
    """
    Prévia e posição na lista a partir da mensagem mais recente gravada.

    Só a prévia e a ordem — ver a docstring do módulo: histórico não é
    atividade, e não pode acender pendência nem abrir janela. Conversa que já
    teve atividade de verdade (mensagem recebida ou enviada depois da conexão)
    mantém a posição que essa atividade deu; um lote antigo chegando agora não
    pode jogá-la para o fim da lista.
    """
    if conversa.last_inbound_at is not None or conversa.last_outbound_at is not None:
        return
    ultima = (
        db.query(WaMessage)
        .filter(WaMessage.conversation_id == conversa.id)
        .order_by(WaMessage.created_at.desc(), WaMessage.id.desc())
        .first()
    )
    if ultima is None:
        return
    conversa.last_message_body = (ultima.body or "")[:500] or None
    conversa.updated_at = ultima.created_at


# ── Ecos: o que o dono digitou no celular ────────────────────────────────────

def registrar_ecos(db: Session, valor: dict, dono: str) -> int:
    """Grava cada mensagem enviada pelo app do celular. Devolve quantas."""
    gravadas = 0
    for eco in valor.get("message_echoes") or []:
        telefone = e164(eco.get("to"))
        wamid = eco.get("id")
        if not telefone or _ja_gravada(db, wamid):
            continue
        conversa = conversa_do_contato(db, dono, telefone)
        corpo = corpo_da_mensagem(eco)
        db.add(WaMessage(
            conversation_id=conversa.id, direction="out", wa_message_id=wamid,
            type=eco.get("type") or "text", body=corpo, status="sent",
            sent_by="human",
        ))
        # Sem `quando`: o eco é atividade de agora, e o relógio daqui é o mesmo
        # das mensagens recebidas — é o que mantém "aguardando você" coerente.
        states.register_outbound(db, conversa, corpo)
        apos_eco_do_celular(db, conversa)
        gravadas += 1
    db.flush()
    return gravadas


def apos_eco_do_celular(db: Session, conversa: Conversation) -> None:
    """
    O dono respondeu pelo celular. O que acontece com a automação?

    A resposta pelo celular é um humano conduzindo a conversa — exatamente o
    caso de "Você respondeu manualmente" em routers/wa.py::responder, só que
    por outra porta. Deixar a IA ativa faria ela responder a próxima mensagem
    do lead por cima do que o dono acabou de combinar.
    """
    # Só mexe em quem está com a IA: pausada, assumida ou encerrada já não tem
    # automação falando, e reescrever o estado apagaria o motivo que está lá.
    if conversa.ai_status == AI_ACTIVE:
        states.handoff(db, conversa, "Você respondeu pelo celular.", states.ATOR_HUMANO)


# ── Agenda ───────────────────────────────────────────────────────────────────

def sincronizar_agenda(db: Session, valor: dict, dono: str) -> int:
    """
    Grava a agenda do WhatsApp Business. Devolve quantos contatos mudaram.

    Também põe o nome nas conversas que já existem com aquele número: a agenda
    pode chegar depois do histórico, e a lista não deve ficar mostrando só
    números quando o nome já é conhecido.
    """
    mudaram = 0
    for item in valor.get("state_sync") or []:
        if item.get("type") != "contact":
            continue
        contato = item.get("contact") or {}
        telefone = e164(contato.get("phone_number"))
        if not telefone:
            continue
        existente = (
            db.query(WaContact)
            .filter(WaContact.user_id == dono, WaContact.phone_e164 == telefone)
            .first()
        )
        if item.get("action") == "remove":
            if existente is not None:
                db.delete(existente)
                mudaram += 1
            continue

        nome = (contato.get("full_name") or contato.get("first_name") or "").strip() or None
        if existente is None:
            db.add(WaContact(user_id=dono, phone_e164=telefone, name=nome))
        else:
            existente.name = nome or existente.name
        if nome:
            (db.query(Conversation)
             .filter(Conversation.user_id == dono, Conversation.phone_e164 == telefone)
             # `updated_at` reescrito com ele mesmo: sem isso o `onupdate` o
             # poria em "agora", e dar nome a um contato não é atividade — a
             # conversa pularia para o topo da lista sem ninguém ter escrito.
             .update({Conversation.contact_name: nome,
                      Conversation.updated_at: Conversation.updated_at},
                     synchronize_session=False))
        mudaram += 1
    db.flush()
    return mudaram
