"""
Simulador: conversar com a IA sem gastar mensagem e sem tocar num lead real.

Serve para responder à pergunta que só se responde vendo: *o que a automação
faria se o lead escrevesse isto?* Hoje a única forma de descobrir é mandar um
convite pago para o celular de alguém e torcer.

Três garantias que definem o módulo:

1. **Nada sai daqui.** `client.send_text` não é chamado em lugar nenhum deste
   arquivo. O que a IA escreve é gravado na sessão de teste e devolvido à tela.
2. **Nada entra no banco.** A sessão vive na memória do processo, não em
   `conversations`. Uma conversa de teste no banco contaminaria as métricas
   (taxa de resposta, handoffs) com números que ninguém viveu.
3. **A decisão é a mesma da produção.** A classificação é `brain.ler` — a de
   verdade, com o mesmo prompt e o mesmo corte de confiança — e o que se faz
   com a intenção sai de `orchestrator.plano_para`. Um simulador com regras
   próprias aprovaria o que a produção recusa, que é pior do que não ter
   simulador.

O preço da memória é honesto e a tela diz: um deploy ou um processo novo zera
as sessões. É teste, não histórico.
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from services.wa import brain, gate, orchestrator

logger = logging.getLogger(__name__)

#: Quantas mensagens uma sessão guarda. Acima disso as mais antigas caem — o
#: que interessa num teste são as últimas trocas, e o histórico que vai para a
#: IA já é cortado em 12 por `brain._historico`.
MAX_MENSAGENS = 60

#: Sessões abertas ao mesmo tempo. Teto para o dicionário não crescer sem fim
#: num processo de vida longa; ao estourar, a sessão mais antiga sai.
MAX_SESSOES = 200

#: Empresa fictícia que vai no prompt no lugar do nome do lead. A IA usa isso
#: para contextualizar; um valor vazio deixaria o teste diferente da produção.
EMPRESA_PADRAO = "Empresa Exemplo Ltda"

# ── Ações do turno, no vocabulário da tela ───────────────────────────────────
RESPONDEU = "respondeu"            # a IA escreveu e, em produção, teria enviado
CHAMOU_HUMANO = "chamou_humano"    # levantou a mão: quem responde é o vendedor
ENCERROU = "encerrou"              # já é cliente / pediu para parar
NAO_ENVIOU = "nao_enviou"          # a trava de horário barrou


@dataclass
class Mensagem:
    """Uma linha da conversa de teste."""
    direction: str                 # in (lead) | out (IA)
    body: str
    created_at: float
    sent_by: Optional[str] = None  # ai | human
    intent_detected: Optional[str] = None


@dataclass
class Turno:
    """
    O raio-x de uma resposta: o que a IA entendeu, o que se decidiu com isso e
    quanto tempo levou.

    É o motivo de o simulador existir. A conversa em si a pessoa vê no chat; o
    que ela não vê em lugar nenhum é a intenção classificada, a confiança e a
    regra que transformou uma na outra.
    """
    acao: str
    intencao: Optional[str] = None
    confianca: float = 0.0
    confiavel: bool = False
    texto: Optional[str] = None
    motivo: Optional[str] = None
    erro: Optional[str] = None
    fora_do_horario: bool = False
    ms: int = 0


@dataclass
class Sessao:
    """A conversa de teste de um usuário."""
    empresa: str = EMPRESA_PADRAO
    mensagens: List[Mensagem] = field(default_factory=list)
    turnos: List[Turno] = field(default_factory=list)
    criada_em: float = field(default_factory=time.time)
    tocada_em: float = field(default_factory=time.time)


# O dicionário é lido e escrito por requisições concorrentes; o lock protege
# só as operações de estrutura (criar, apagar, podar), que são rápidas.
_sessoes: dict = {}
_trava = threading.Lock()


def _agora() -> float:
    return time.time()


def sessao(user_id: str, criar: bool = True) -> Optional[Sessao]:
    """A sessão do usuário, criando uma vazia se ainda não existir."""
    with _trava:
        s = _sessoes.get(user_id)
        if s is None and criar:
            if len(_sessoes) >= MAX_SESSOES:
                mais_antiga = min(_sessoes, key=lambda k: _sessoes[k].tocada_em)
                _sessoes.pop(mais_antiga, None)
            s = Sessao()
            _sessoes[user_id] = s
        if s is not None:
            s.tocada_em = _agora()
        return s


def reiniciar(user_id: str, empresa: Optional[str] = None) -> Sessao:
    """Joga fora a conversa de teste e começa outra."""
    with _trava:
        s = Sessao(empresa=(empresa or "").strip() or EMPRESA_PADRAO)
        _sessoes[user_id] = s
        return s


def _historico_para_ia(s: Sessao) -> List[dict]:
    return [{"direction": m.direction, "body": m.body} for m in s.mensagens]


def _podar(s: Sessao) -> None:
    if len(s.mensagens) > MAX_MENSAGENS:
        del s.mensagens[: len(s.mensagens) - MAX_MENSAGENS]
    if len(s.turnos) > MAX_MENSAGENS:
        del s.turnos[: len(s.turnos) - MAX_MENSAGENS]


def enviar_do_lead(user_id: str, texto: str,
                   ignorar_horario: bool = True) -> Turno:
    """
    O lead fictício escreve; a IA lê e decide. Devolve o raio-x do turno.

    `ignorar_horario` existe porque a trava de madrugada é da produção, não do
    teste: quem está configurando a IA às 23h precisa poder testá-la às 23h. A
    tela mostra o que a trava teria feito de qualquer jeito, então a informação
    não se perde — só deixa de bloquear.
    """
    s = sessao(user_id)
    texto = (texto or "").strip()
    if not texto:
        return Turno(NAO_ENVIOU, motivo="Escreva a mensagem antes de enviar.")

    s.mensagens.append(Mensagem("in", texto[:4000], _agora()))
    _podar(s)

    inicio = time.monotonic()
    turno = _decidir(s, ignorar_horario)
    turno.ms = int((time.monotonic() - inicio) * 1000)

    if turno.acao == RESPONDEU and turno.texto:
        s.mensagens.append(Mensagem(
            "out", turno.texto, _agora(), sent_by="ai",
            intent_detected=turno.intencao,
        ))
        _podar(s)

    s.turnos.append(turno)
    _podar(s)
    return turno


def _decidir(s: Sessao, ignorar_horario: bool) -> Turno:
    """
    O turno propriamente dito, na mesma ordem da produção:
    trava de horário → IA lê → intenção vira ação.

    O que falta em relação ao `orchestrator` é justamente o que não faz sentido
    fora de uma conversa real: janela de 24 h, opt-out do número e estado da
    conversa. Nenhum deles depende do que o lead escreveu — são do cadastro —,
    então tirá-los não muda o que se está testando aqui, que é a leitura.
    """
    pode, fora_do_horario = gate.service_window()
    if not pode and not ignorar_horario:
        return Turno(
            NAO_ENVIOU, fora_do_horario=True,
            motivo="Fora do horário de envio: a automação ficaria calada agora. "
                   + gate.janela_de_envio()["explicacao"],
        )

    if not brain.is_configured():
        return Turno(
            CHAMOU_HUMANO,
            motivo="A IA não está configurada neste servidor.",
            erro="Falta ANTHROPIC_API_KEY.",
            fora_do_horario=fora_do_horario,
        )

    leitura = brain.ler(
        _historico_para_ia(s),
        empresa=s.empresa,
        fora_do_horario=fora_do_horario and pode,
    )
    base = dict(
        intencao=leitura.intencao, confianca=leitura.confianca,
        confiavel=leitura.confiavel, fora_do_horario=fora_do_horario,
    )

    if not leitura.confiavel:
        return Turno(
            CHAMOU_HUMANO,
            motivo=leitura.erro or (
                f"Confiança de {leitura.confianca:.0%} — abaixo do corte de "
                f"{brain.CONFIANCA_MINIMA:.0%}. Na dúvida, a automação chama você."
            ),
            erro=leitura.erro, **base,
        )

    plano = orchestrator.plano_para(leitura.intencao)
    if plano is None:
        return Turno(CHAMOU_HUMANO,
                     motivo="Intenção sem ação definida.", **base)

    if plano.get("cliente") or plano.get("parar"):
        return Turno(ENCERROU, motivo=plano["handoff"], **base)

    if not plano.get("responder"):
        return Turno(CHAMOU_HUMANO, motivo=plano["handoff"], **base)

    texto = (leitura.rascunho or "").strip()
    if not texto:
        return Turno(CHAMOU_HUMANO,
                     motivo="A IA classificou a mensagem mas não escreveu "
                            "resposta. Em produção, a conversa passaria para você.",
                     **base)

    if not pode:
        # Chegou aqui só porque o teste pediu para ignorar a trava: mostra o
        # texto, mas diz que na produção ele teria ficado para depois.
        return Turno(RESPONDEU, texto=texto,
                     motivo="Em produção esta resposta esperaria o horário de "
                            "envio — você pediu para ignorar a trava.", **base)
    return Turno(RESPONDEU, texto=texto, **base)
