"""
O portão: a única porta por onde uma mensagem automática pode sair.

Regra de projeto: **negar é o padrão**. `can_send()` só devolve liberado
depois de todas as condições passarem, e cada uma delas é lida do banco no
momento da decisão — nunca de um valor carregado antes. É isso que faz
"pausar" e "assumir" valerem imediatamente, e não a partir do próximo turno.

O modelo de linguagem não participa de nenhuma decisão daqui. Ele classifica a
intenção e redige um rascunho; se este arquivo disser não, o rascunho é
descartado. Uma regra que mora num prompt é uma regra que uma frase bem
escrita do outro lado consegue mudar.

Ordem das checagens (da mais estrutural para a mais circunstancial):
  1. a empresa é um lead?           relationship
  2. a automação está ligada?       ai_status
  3. o número pediu para sair?      opt-out (LGPD)
  4. a janela de 24 h está aberta?  window_expires_at
  5. é hora de falar com alguém?    horário de atendimento
"""
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models.database import AI_ACTIVE, Conversation, Lead, RELATIONSHIP_LEAD, utcnow
from services.people import optout

logger = logging.getLogger(__name__)

# Fuso do atendimento. A conta guarda tudo em UTC; horário comercial só faz
# sentido no relógio de quem atende e de quem recebe.
TIMEZONE = os.getenv("WA_TIMEZONE", "America/Sao_Paulo")

# Horário comercial: conversa normal.
SERVICE_START_HOUR = int(os.getenv("WA_SERVICE_START", "9"))
SERVICE_END_HOUR = int(os.getenv("WA_SERVICE_END", "18"))

# Silêncio noturno: nada sai, nem resposta curta. Mensagem comercial de
# madrugada é o caminho mais curto para o destinatário marcar como spam — e
# número marcado como spam é número restringido pela Meta.
QUIET_START_HOUR = int(os.getenv("WA_QUIET_START", "21"))
QUIET_END_HOUR = int(os.getenv("WA_QUIET_END", "8"))

# Fim de semana conta como fora do horário, não como silêncio: responder
# "recebi, retorno segunda" no sábado é melhor do que sumir.
WEEKEND_IS_QUIET = os.getenv("WA_WEEKEND_QUIET", "0") == "1"

# Motivos de recusa. São códigos, não frases: a tela traduz, os testes
# comparam e o log não muda de sentido quando alguém reescrever o texto.
DENY_NOT_A_LEAD = "not_a_lead"
DENY_AI_NOT_ACTIVE = "ai_not_active"
DENY_OPTED_OUT = "opted_out"
DENY_WINDOW_CLOSED = "window_closed"
DENY_QUIET_HOURS = "quiet_hours"
DENY_NO_PHONE = "no_phone"
DENY_LEAD_MISSING = "lead_missing"
DENY_ALREADY_OPEN = "already_open"
DENY_AWAITING_REPLY = "awaiting_reply"

_MOTIVOS = {
    DENY_NOT_A_LEAD: "Esta empresa não está marcada como lead.",
    DENY_AI_NOT_ACTIVE: "A resposta automática não está ativa nesta conversa.",
    DENY_OPTED_OUT: "Este número pediu para não receber mensagens.",
    DENY_WINDOW_CLOSED: "A janela de 24 horas desde a última mensagem do lead fechou.",
    DENY_QUIET_HOURS: "Fora do horário em que enviamos mensagens.",
    # A frase acima é o motivo; `Decision.detail` completa com a faixa e a hora
    # da retomada. Recusa que não diz quando volta manda o usuário adivinhar.
    DENY_NO_PHONE: "A conversa não tem número de destino.",
    DENY_LEAD_MISSING: "A empresa desta conversa não existe mais.",
    DENY_ALREADY_OPEN: (
        "A conversa já está aberta — o lead respondeu nas últimas 24 horas. "
        "Responda por ali, sem custo."
    ),
    DENY_AWAITING_REPLY: (
        "O convite já foi enviado e ainda não houve resposta. "
        "Reenviar cobra de novo e incomoda quem já recebeu."
    ),
}

# Quanto tempo esperar antes de deixar reenviar o template para quem não
# respondeu. Cada envio é cobrado e conta para a reputação do número: insistir
# de hora em hora é como se perde o número, não como se ganha o lead.
TEMPLATE_RETRY_HOURS = int(os.getenv("WA_TEMPLATE_RETRY_HOURS", "72"))


@dataclass(frozen=True)
class Decision:
    """
    Resposta do portão.

    `allowed` sozinho não basta: quem chama precisa saber *por que* não, para
    mostrar na tela em vez de engolir o silêncio, e precisa saber se está fora
    do horário, porque aí a resposta permitida é curta.

    `detail` é a parte que depende do instante — a hora em que a janela volta a
    abrir, por exemplo. Fica fora de `_MOTIVOS` porque aquilo é dicionário de
    códigos, e código não muda de sentido conforme o relógio.
    """
    allowed: bool
    reason: Optional[str] = None
    after_hours: bool = False
    detail: Optional[str] = None

    @property
    def message(self) -> str:
        if self.allowed:
            return "Pode enviar."
        motivo = _MOTIVOS.get(self.reason or "", "Envio não autorizado.")
        return f"{motivo} {self.detail}" if self.detail else motivo


def _zona():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIMEZONE)
    except Exception:
        # Sistema sem base de fusos não pode derrubar o portão. Sem zona
        # confiável, o horário local é indeterminado — e indeterminado, aqui,
        # significa não enviar (ver `service_window`).
        logger.warning("Fuso %s indisponível; horário de atendimento indeterminado.", TIMEZONE)
        return None


def _entre(hora: time, inicio: int, fim: int) -> bool:
    """Faixa de horas que pode virar a meia-noite (21h→8h)."""
    if inicio <= fim:
        return inicio <= hora.hour < fim
    return hora.hour >= inicio or hora.hour < fim


def service_window(agora: Optional[datetime] = None) -> tuple[bool, bool]:
    """
    Devolve `(pode_enviar, fora_do_horario)` para o instante dado.

    Três faixas, não duas: dentro do comercial fala normalmente; à noite não
    fala nada; no meio (começo da manhã, fim da tarde, fim de semana) responde
    de forma curta, porque sumir com quem acabou de escrever custa o lead.
    """
    zona = _zona()
    if zona is None:
        return False, False

    agora = agora or utcnow()
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=UTC)
    local = agora.astimezone(zona)

    fim_de_semana = local.weekday() >= 5
    if fim_de_semana and WEEKEND_IS_QUIET:
        return False, False
    if _entre(local.timetz(), QUIET_START_HOUR, QUIET_END_HOUR):
        return False, False

    comercial = (
        not fim_de_semana
        and _entre(local.timetz(), SERVICE_START_HOUR, SERVICE_END_HOUR)
    )
    return True, not comercial


# ── Quando a janela muda de estado ───────────────────────────────────────────
# Só serve para explicar: nenhuma decisão de envio sai daqui. O portão continua
# respondendo sobre o instante atual — isto responde "e quando, então?".

_DIAS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
         "sexta-feira", "sábado", "domingo")

# Oito dias cobrem qualquer combinação de silêncio noturno com fim de semana
# fechado. Se a busca estourar, é porque a configuração fecha a janela para
# sempre — e aí a resposta honesta é "não sei", não um horário inventado.
_LIMITE_DE_BUSCA_HORAS = 24 * 8

# O identificador IANA não é o nome que alguém usa para falar de hora. Sem
# tradução conhecida, mostra o identificador cru: feio, mas verdadeiro.
_NOMES_DE_FUSO = {"America/Sao_Paulo": "Brasília"}
_NOME_DO_FUSO = _NOMES_DE_FUSO.get(TIMEZONE, TIMEZONE)

_SEM_FUSO = (
    "O servidor não conseguiu determinar o horário local "
    f"({TIMEZONE}), então nada é enviado até isso ser corrigido — "
    "enviar sem saber a hora do destinatário é o que queima o número."
)


def _proxima_virada(agora: Optional[datetime], alvo: bool) -> Optional[datetime]:
    """
    Primeira virada de hora, a partir de `agora`, em que `service_window`
    passa a devolver `alvo`. Devolve no fuso do atendimento, ou `None` quando
    o fuso não está disponível (aí não há horário local que se possa afirmar).
    """
    zona = _zona()
    if zona is None:
        return None

    agora = agora or utcnow()
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=UTC)
    momento = agora.astimezone(zona).replace(minute=0, second=0, microsecond=0)

    for _ in range(_LIMITE_DE_BUSCA_HORAS):
        momento += timedelta(hours=1)
        if service_window(momento)[0] is alvo:
            return momento
    return None


def proxima_abertura(agora: Optional[datetime] = None) -> Optional[datetime]:
    """Quando o envio volta a ser permitido."""
    return _proxima_virada(agora, True)


def proximo_fechamento(agora: Optional[datetime] = None) -> Optional[datetime]:
    """Quando o envio deixa de ser permitido."""
    return _proxima_virada(agora, False)


def _em_palavras(momento: datetime, agora: datetime) -> str:
    """"amanhã às 8h" — a forma como alguém diria a hora, não um carimbo ISO."""
    hora = f"{momento.hour}h" if momento.minute == 0 else momento.strftime("%Hh%M")
    dias = (momento.date() - agora.date()).days
    if dias == 0:
        return f"hoje às {hora}"
    if dias == 1:
        return f"amanhã às {hora}"
    return f"{_DIAS[momento.weekday()]}, {momento.strftime('%d/%m')}, às {hora}"


def faixa_de_envio() -> str:
    """A faixa configurada, em palavras — para a tela dizer a regra, não só o não."""
    return f"{QUIET_END_HOUR}h às {QUIET_START_HOUR}h"


def janela_de_envio(agora: Optional[datetime] = None) -> dict:
    """
    O estado da janela agora, pronto para a tela.

    `muda_em` é o próximo instante em que esta resposta deixa de valer. A tela
    guarda o resultado e o compara com o relógio dela: assim o botão não fica
    dizendo "fora do horário" às nove da manhã só porque a aba está aberta
    desde a madrugada, e mesmo assim a regra continua morando só aqui.
    """
    agora = agora or utcnow()
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=UTC)

    pode, fora_do_horario = service_window(agora)
    zona = _zona()
    if zona is None:
        return {
            "pode_enviar": False,
            "fora_do_horario": False,
            "faixa": faixa_de_envio(),
            "fuso": TIMEZONE,
            "indeterminado": True,
            "explicacao": _SEM_FUSO,
            "volta_em": None,
            "muda_em": None,
        }

    virada = proximo_fechamento(agora) if pode else proxima_abertura(agora)
    local = agora.astimezone(zona)
    quando = _em_palavras(virada, local) if virada else None

    if pode:
        explicacao = (
            f"Enviamos das {faixa_de_envio()} (horário de {_NOME_DO_FUSO})."
            + (f" Esta janela fecha {quando}." if quando else "")
        )
    else:
        explicacao = (
            f"Só enviamos das {faixa_de_envio()} (horário de {_NOME_DO_FUSO}) — "
            "fora dessa faixa a mensagem chega em hora que faz o destinatário "
            "marcar o número como spam, e número marcado é número restringido "
            "pela Meta."
            + (f" O envio volta a ser possível {quando}." if quando else "")
        )

    return {
        "pode_enviar": pode,
        "fora_do_horario": fora_do_horario,
        "faixa": faixa_de_envio(),
        "fuso": TIMEZONE,
        "indeterminado": False,
        "explicacao": explicacao,
        "volta_em": quando if not pode else None,
        "muda_em": virada.astimezone(UTC).isoformat() if virada else None,
    }


def _detalhe_do_horario(agora: Optional[datetime]) -> str:
    """A metade da recusa que depende do relógio: a faixa e a hora da volta."""
    if _zona() is None:
        return _SEM_FUSO
    return janela_de_envio(agora)["explicacao"]


def _com_fuso(momento: datetime) -> datetime:
    """
    SQLite devolve datas sem fuso mesmo em coluna `timezone=True`. Comparar uma
    naive com uma aware levanta TypeError — dentro do portão, isso seria uma
    exceção no lugar de uma decisão.
    """
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _janela_aberta(conversation: Conversation, agora: datetime) -> bool:
    """
    A janela de 24 h da Meta, contada da última mensagem recebida.

    Ausente significa fechada: conversa que o lead nunca respondeu não tem
    janela nenhuma, e tratar `None` como "sem prazo" liberaria justamente o
    caso que a Meta cobra como template.
    """
    prazo = conversation.window_expires_at
    if prazo is None:
        return False
    return _com_fuso(prazo) > agora


def can_start(db: Session, lead: Lead, phone_e164: str,
              conversation: Optional[Conversation] = None,
              agora: Optional[datetime] = None) -> Decision:
    """
    Este lead pode receber o **primeiro contato** (template pago)?

    Portão separado do `can_send` de propósito. A abertura é, por definição,
    fora da janela de 24 h — passá-la pelo mesmo caminho daria "janela
    fechada" para todo primeiro contato, e a saída seria alguém desligar a
    checagem de janela para os dois casos. As outras travas continuam valendo
    todas; o que muda é só a janela.

    As duas recusas exclusivas daqui protegem o bolso e a reputação do número:
    não reabrir o que já está aberto, e não insistir com quem não respondeu.
    """
    agora = agora or utcnow()

    if not phone_e164:
        return Decision(False, DENY_NO_PHONE)
    if lead is None:
        return Decision(False, DENY_LEAD_MISSING)
    if lead.relationship != RELATIONSHIP_LEAD:
        return Decision(False, DENY_NOT_A_LEAD)
    if optout.is_blocked(db, "phone", phone_e164):
        return Decision(False, DENY_OPTED_OUT)

    pode, fora_do_horario = service_window(agora)
    if not pode:
        return Decision(False, DENY_QUIET_HOURS, detail=_detalhe_do_horario(agora))

    if conversation is not None:
        if _janela_aberta(conversation, agora):
            return Decision(False, DENY_ALREADY_OPEN)
        enviado = conversation.last_outbound_at
        if enviado is not None:
            if enviado.tzinfo is None:
                enviado = enviado.replace(tzinfo=UTC)
            respondeu_depois = (
                conversation.last_inbound_at is not None
                and _com_fuso(conversation.last_inbound_at) > enviado
            )
            recente = enviado > agora - timedelta(hours=TEMPLATE_RETRY_HOURS)
            if recente and not respondeu_depois:
                return Decision(False, DENY_AWAITING_REPLY)

    return Decision(True, after_hours=fora_do_horario)


def can_send(db: Session, conversation: Conversation,
             agora: Optional[datetime] = None) -> Decision:
    """
    Esta conversa pode receber uma mensagem automática agora?

    Relê o estado do banco antes de responder. Chame imediatamente antes de
    enviar — nunca no início do turno, guardando o resultado: entre uma coisa
    e outra o usuário pode ter clicado em "assumir".
    """
    agora = agora or utcnow()

    if conversation is None:
        return Decision(False, DENY_LEAD_MISSING)
    if not conversation.phone_e164:
        return Decision(False, DENY_NO_PHONE)

    # Estado fresco: `db.refresh` derruba o que estiver em cache na sessão. Se
    # a linha sumiu no meio do caminho (lead apagado, cascata), refresh levanta
    # exceção — e o portão não pode ser um lugar de onde se sai por exceção.
    # Registro que não existe mais não autoriza envio nenhum.
    try:
        db.refresh(conversation)
    except Exception:
        logger.warning("Conversa %s não existe mais; envio recusado.", conversation.id)
        return Decision(False, DENY_LEAD_MISSING)

    lead = db.query(Lead).filter(Lead.id == conversation.lead_id).first()
    if lead is None:
        return Decision(False, DENY_LEAD_MISSING)

    if lead.relationship != RELATIONSHIP_LEAD:
        return Decision(False, DENY_NOT_A_LEAD)
    if conversation.ai_status != AI_ACTIVE:
        return Decision(False, DENY_AI_NOT_ACTIVE)
    if optout.is_blocked(db, "phone", conversation.phone_e164):
        return Decision(False, DENY_OPTED_OUT)
    if not _janela_aberta(conversation, agora):
        return Decision(False, DENY_WINDOW_CLOSED)

    pode, fora_do_horario = service_window(agora)
    if not pode:
        return Decision(False, DENY_QUIET_HOURS, detail=_detalhe_do_horario(agora))

    return Decision(True, after_hours=fora_do_horario)
