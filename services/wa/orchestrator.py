"""
O turno: o que acontece quando um lead responde.

Este é o arquivo onde as decisões moram. `brain.py` classifica e redige;
`gate.py` autoriza; `states.py` grava; `client.py` entrega. Aqui se costura,
e a costura é toda em código — nenhuma regra depende do texto de um prompt.

A sequência de um turno:

    portão  →  IA lê  →  intenção vira ação  →  portão de novo  →  envia

O portão aparece **duas vezes**, e não é redundância. Entre a primeira
checagem e o envio existe uma chamada de rede de alguns segundos, e é
exatamente nesse intervalo que o usuário pode clicar em "Assumir agora". Sem a
segunda leitura, a automação responderia por cima de alguém que acabou de
tomar a conversa para si.

Duas regras que valem para tudo aqui:

1. **Silêncio é falha.** Se qualquer coisa der errado — IA fora do ar, JSON
   quebrado, Meta recusando — a conversa vai para `HUMAN_HANDOFF` com o motivo
   escrito. Assim o problema aparece como pendência na tela, com badge, em vez
   de a mensagem do lead simplesmente morrer sem ninguém saber.
2. **Na dúvida, não envia.** Intenção desconhecida, confiança baixa, rascunho
   vazio: tudo isso chama o humano. O custo de chamar o humano à toa é o tempo
   dele; o custo de responder errado é uma mensagem indevida no celular de
   alguém — e, repetido, o número restringido pela Meta.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from models.database import AI_ACTIVE, Conversation, Lead, WaMessage, utcnow
from services.wa import brain, client, gate, states

logger = logging.getLogger(__name__)

# Quantas respostas automáticas podem sair fora do expediente antes de a
# conversa silenciar até o próximo dia útil.
MAX_TURNOS_FORA_DO_HORARIO = 3

# ── Ações ────────────────────────────────────────────────────────────────────
ENVIOU = "enviou"
CHAMOU_HUMANO = "chamou_humano"
ENCERROU = "encerrou"
NAO_FEZ_NADA = "nao_fez_nada"


@dataclass(frozen=True)
class Turno:
    """O que o turno fez, para o log, os testes e a tela."""
    acao: str
    motivo: Optional[str] = None
    intencao: Optional[str] = None
    texto: Optional[str] = None


#: Intenção → o que fazer. É uma tabela e não uma cadeia de `if` porque a
#: pergunta "o que acontece quando o lead diz X?" precisa ter uma resposta que
#: se lê de uma vez, sem seguir o fluxo do código.
#:
#: `responder` só é verdadeiro nas intenções em que uma frase automática é
#: inofensiva. Tudo que envolve dinheiro, compromisso ou desconforto do lead
#: passa para o humano — mesmo quando a IA se diz confiante.
_ACOES = {
    brain.CONFIRMOU_PESSOA: {"responder": True},
    brain.CONVERSANDO: {"responder": True},
    brain.QUER_HUMANO: {
        "responder": False,
        "handoff": "O lead pediu para falar com uma pessoa.",
    },
    brain.NEGOCIANDO: {
        "responder": False,
        "handoff": "O lead demonstrou interesse concreto — assuma daqui.",
    },
    brain.PESSOA_ERRADA: {
        "responder": False,
        "handoff": "O lead disse que não é a pessoa certa; confira o contato.",
    },
    brain.FORA_DA_BASE: {
        "responder": False,
        "handoff": "O lead perguntou algo que a automação não tem como responder.",
    },
    brain.AMBIGUO: {
        "responder": False,
        "handoff": "Não deu para entender a mensagem com segurança.",
    },
    brain.JA_E_CLIENTE: {
        "responder": False,
        "cliente": True,
        "handoff": "O lead disse que já é cliente. A automação foi encerrada.",
    },
    brain.PEDIU_PARAR: {
        "responder": False,
        "parar": True,
        "handoff": "O lead pediu para não receber mais mensagens.",
    },
}


def plano_para(intencao: str) -> Optional[dict]:
    """
    O que a tabela acima manda fazer com uma intenção.

    Existe para o simulador (`services/wa/sandbox.py`) poder mostrar a mesma
    decisão que a produção tomaria sem reimplementar a tabela — duas cópias da
    regra virariam, na primeira mudança, um teste que aprova o que a produção
    recusa.
    """
    return _ACOES.get(intencao)


def _historico(db: Session, conversa: Conversation) -> list:
    mensagens = (
        db.query(WaMessage)
        .filter(WaMessage.conversation_id == conversa.id)
        .order_by(WaMessage.id.asc())
        .all()
    )
    return [{"direction": m.direction, "body": m.body} for m in mensagens]


def _ultima_e_do_lead(db: Session, conversa: Conversation) -> bool:
    """
    Só respondemos quando a última palavra foi do lead.

    Protege contra o turno rodar duas vezes pela mesma mensagem (uma pelo
    webhook, outra pelo cron de retomada) e responder duas vezes.
    """
    ultima = (
        db.query(WaMessage)
        .filter(WaMessage.conversation_id == conversa.id)
        .order_by(WaMessage.id.desc())
        .first()
    )
    return ultima is not None and ultima.direction == "in"


def responder(db: Session, conversa: Conversation,
              agora=None) -> Turno:
    """
    Roda um turno para esta conversa. Commita o que decidir.

    Não levanta exceção: qualquer falha vira `CHAMOU_HUMANO`, porque uma
    exceção que sobe daqui viraria erro 500 no webhook — e erro no webhook faz
    a Meta reentregar a mensagem, o que produziria uma segunda resposta.
    """
    agora = agora or utcnow()
    try:
        return _turno(db, conversa, agora)
    except Exception:
        logger.exception("Falha no turno da conversa %s.", conversa.id)
        db.rollback()
        return _passar_para_humano(
            db, conversa,
            "A automação falhou ao processar esta mensagem. Responda você.",
        )


def _passar_para_humano(db: Session, conversa: Conversation, motivo: str,
                        intencao: Optional[str] = None) -> Turno:
    # Ator `ia`: foi a automação que levantou a mão, não um clique do usuário.
    states.handoff(db, conversa, motivo, states.ATOR_IA)
    db.commit()
    return Turno(CHAMOU_HUMANO, motivo=motivo, intencao=intencao)


def _turno(db: Session, conversa: Conversation, agora) -> Turno:
    if not _ultima_e_do_lead(db, conversa):
        return Turno(NAO_FEZ_NADA, motivo="A última mensagem não é do lead.")

    # Primeiro portão: barra o que nem deveria chegar à IA — cliente atual,
    # opt-out, conversa pausada, janela fechada, madrugada.
    decisao = gate.can_send(db, conversa, agora=agora)
    if not decisao.allowed:
        return _recusa_do_portao(db, conversa, decisao)

    # Teto de fora-do-horário: determinístico, não uma instrução no prompt.
    if decisao.after_hours and (conversa.after_hours_turns or 0) >= MAX_TURNOS_FORA_DO_HORARIO:
        return Turno(NAO_FEZ_NADA,
                     motivo="Limite de respostas fora do expediente atingido; "
                            "retomamos no próximo dia útil.")

    if not brain.is_configured():
        return _passar_para_humano(
            db, conversa,
            "A IA não está configurada neste servidor. Responda você.",
        )

    lead = db.query(Lead).filter(Lead.id == conversa.lead_id).first()
    leitura = brain.ler(
        _historico(db, conversa),
        empresa=(lead.company_name or lead.domain) if lead else None,
        fora_do_horario=decisao.after_hours,
    )

    if not leitura.confiavel:
        return _passar_para_humano(
            db, conversa,
            leitura.erro or "A IA não teve confiança suficiente na leitura.",
            intencao=leitura.intencao,
        )

    plano = _ACOES.get(leitura.intencao)
    if plano is None:
        return _passar_para_humano(db, conversa, "Intenção sem ação definida.",
                                   intencao=leitura.intencao)

    # Marcações estruturais primeiro: mesmo que o envio não aconteça, "é
    # cliente" e "pediu para parar" precisam ficar gravados.
    if plano.get("cliente") and lead is not None:
        states.mark_customer(db, lead, plano["handoff"], states.ATOR_IA)
        db.commit()
        return Turno(ENCERROU, motivo=plano["handoff"], intencao=leitura.intencao)
    if plano.get("parar") and lead is not None:
        states.mark_do_not_contact(db, lead, plano["handoff"], states.ATOR_IA)
        db.commit()
        return Turno(ENCERROU, motivo=plano["handoff"], intencao=leitura.intencao)

    if not plano.get("responder"):
        return _passar_para_humano(db, conversa, plano["handoff"], leitura.intencao)

    texto = (leitura.rascunho or "").strip()
    if not texto:
        return _passar_para_humano(
            db, conversa, "A IA não produziu resposta. Responda você.",
            intencao=leitura.intencao,
        )

    # Segundo portão, agora do outro lado da chamada de rede: entre a leitura e
    # aqui passaram segundos em que o usuário pode ter assumido a conversa.
    decisao = gate.can_send(db, conversa, agora=utcnow())
    if not decisao.allowed:
        logger.info("Envio cancelado depois da leitura: %s", decisao.reason)
        return Turno(NAO_FEZ_NADA, motivo=decisao.message, intencao=leitura.intencao)

    envio = client.send_text(conversa.phone_e164, texto)
    if not envio.ok:
        return _passar_para_humano(
            db, conversa,
            f"A Meta recusou a resposta automática ({envio.error}). Responda você.",
            intencao=leitura.intencao,
        )

    db.add(WaMessage(
        conversation_id=conversa.id, direction="out",
        wa_message_id=envio.wa_message_id, type="text", body=texto,
        status="sent", sent_by="ai", intent_detected=leitura.intencao,
    ))
    states.register_outbound(db, conversa, texto)
    conversa.after_hours = decisao.after_hours
    conversa.after_hours_turns = (
        (conversa.after_hours_turns or 0) + 1 if decisao.after_hours else 0
    )
    db.commit()
    return Turno(ENVIOU, intencao=leitura.intencao, texto=texto)


def _recusa_do_portao(db: Session, conversa: Conversation, decisao) -> Turno:
    """
    O que fazer quando o portão nega.

    A diferença que importa: recusa **temporária** (madrugada) não é problema —
    o cron retoma no horário. Recusa **definitiva** (janela fechada) precisa
    virar pendência, senão o lead escreveu e ninguém nunca vai saber.
    """
    if decisao.reason == gate.DENY_QUIET_HOURS:
        volta = gate.janela_de_envio().get("volta_em")
        return Turno(NAO_FEZ_NADA, motivo=(
            f"Fora do horário de envio; retomamos {volta}." if volta
            else "Fora do horário de envio; retomamos mais tarde."
        ))
    if decisao.reason == gate.DENY_AI_NOT_ACTIVE:
        # O humano já está com a conversa. Nada a fazer, e nada errado.
        return Turno(NAO_FEZ_NADA, motivo=decisao.message)
    if decisao.reason in (gate.DENY_NOT_A_LEAD, gate.DENY_OPTED_OUT):
        # Silêncio correto: são justamente os casos em que não se fala.
        return Turno(NAO_FEZ_NADA, motivo=decisao.message)
    return _passar_para_humano(db, conversa, decisao.message)


# ── Retomada ─────────────────────────────────────────────────────────────────

def conversas_pendentes(db: Session, limite: int = 50) -> list:
    """
    Conversas em que o lead falou por último e a automação ainda deve responder.

    É o que o cron usa para pegar o que caiu na madrugada e o que ficou para
    trás quando um turno não chegou a rodar.
    """
    candidatas = (
        db.query(Conversation)
        .filter(Conversation.ai_status == AI_ACTIVE,
                Conversation.last_inbound_at.isnot(None))
        .order_by(Conversation.updated_at.asc())
        .limit(limite * 4)
        .all()
    )
    pendentes = []
    for conversa in candidatas:
        saida = conversa.last_outbound_at
        entrada = conversa.last_inbound_at
        if saida is None or gate._com_fuso(entrada) > gate._com_fuso(saida):
            pendentes.append(conversa)
        if len(pendentes) >= limite:
            break
    return pendentes


def rodar_pendentes(db: Session, limite: int = 50) -> dict:
    """Uma rodada de retomada. Devolve o resumo, no formato dos crons daqui."""
    resumo = {ENVIOU: 0, CHAMOU_HUMANO: 0, ENCERROU: 0, NAO_FEZ_NADA: 0}
    for conversa in conversas_pendentes(db, limite):
        turno = responder(db, conversa)
        resumo[turno.acao] = resumo.get(turno.acao, 0) + 1
    return resumo
