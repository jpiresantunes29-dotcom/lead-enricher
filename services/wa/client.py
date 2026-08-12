"""
Cliente da WhatsApp Cloud API (Graph API da Meta).

Duas operações, e a diferença entre elas é dinheiro:

  `send_template`  abre a conversa. Só templates pré-aprovados pela Meta, e a
                   categoria de prospecção é *marketing* — **cobrada por
                   mensagem**. É o primeiro contato, e quem dispara é o humano.
  `send_text`      texto livre. Só vale dentro da janela de 24 h aberta pela
                   última mensagem do lead, e aí é **gratuito**. É onde a
                   automação trabalha.

Mandar texto livre fora da janela não dá erro de cobrança: dá erro 470 da
Meta. Por isso quem decide se pode enviar é o portão (`gate.py`), antes daqui.

Usa `requests` direto, sem SDK — mesma escolha de `services/ai_insights.py`.
"""
import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GRAPH_VERSION = os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0")
TIMEOUT = 20


def phone_number_id() -> str:
    """Id do número emissor no WABA — não é o telefone, é o id da Meta."""
    return (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()


def access_token() -> str:
    return (os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()


def template_name() -> str:
    """Template de abertura aprovado. Sem ele não há primeiro contato."""
    return (os.getenv("WHATSAPP_TEMPLATE_NAME") or "").strip()


def template_language() -> str:
    return (os.getenv("WHATSAPP_TEMPLATE_LANG") or "pt_BR").strip()


def is_configured() -> bool:
    """
    Tudo configurado? Enquanto não estiver, o produto continua de pé e a tela
    mostra o botão apagado dizendo o que falta — em vez de sumir com ele.
    """
    return bool(phone_number_id() and access_token())


def missing_config() -> list:
    """O que falta, em nomes de variável, para a tela poder explicar."""
    faltando = []
    if not phone_number_id():
        faltando.append("WHATSAPP_PHONE_NUMBER_ID")
    if not access_token():
        faltando.append("WHATSAPP_ACCESS_TOKEN")
    if not template_name():
        faltando.append("WHATSAPP_TEMPLATE_NAME")
    return faltando


@dataclass(frozen=True)
class SendResult:
    """
    Resultado de um envio.

    `wa_message_id` é o que a Meta devolve e o que amarra a idempotência: a
    mesma mensagem reentregue no webhook é reconhecida por ele.
    """
    ok: bool
    wa_message_id: Optional[str] = None
    error: Optional[str] = None


def _url() -> str:
    return f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_number_id()}/messages"


def _post(payload: dict) -> SendResult:
    if not is_configured():
        return SendResult(False, error="WhatsApp não configurado no servidor.")
    try:
        resp = requests.post(
            _url(),
            headers={"Authorization": f"Bearer {access_token()}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("Falha de rede ao falar com a Meta: %s", e)
        return SendResult(False, error="Não foi possível falar com a Meta.")

    if resp.status_code >= 400:
        # A mensagem de erro da Meta costuma citar o destinatário ("recipient
        # +55...") — e o log é um arquivo que fica. Só o **código** vai para lá;
        # ele já identifica a causa na documentação da Meta. A frase completa
        # volta no `SendResult`, que a tela mostra para o dono da conta: ele já
        # conhece o número, é o lead dele.
        codigo = f"HTTP {resp.status_code}"
        detalhe = codigo
        try:
            erro = (resp.json() or {}).get("error", {})
            if erro.get("code") is not None:
                codigo = str(erro["code"])
            detalhe = f"{codigo}: {erro.get('message')}" if erro else codigo
        except Exception:
            pass
        logger.warning("Meta recusou o envio (código %s).", codigo)
        return SendResult(False, error=detalhe or "Envio recusado pela Meta.")

    try:
        dados = resp.json()
        wamid = (dados.get("messages") or [{}])[0].get("id")
    except Exception:
        wamid = None
    return SendResult(True, wa_message_id=wamid)


def send_template(para_e164: str, nome: Optional[str] = None,
                  variaveis: Optional[list] = None) -> SendResult:
    """
    Envia o template de abertura. **Custa dinheiro por mensagem.**

    Chame só a partir de uma ação explícita do usuário — nunca de um laço,
    nunca de um cron. Abertura fria em massa é o caminho mais rápido para a
    Meta restringir o número.
    """
    nome = nome or template_name()
    if not nome:
        return SendResult(False, error="Nenhum template de abertura configurado.")

    template: dict = {"name": nome, "language": {"code": template_language()}}
    if variaveis:
        template["components"] = [{
            "type": "body",
            "parameters": [{"type": "text", "text": str(v)} for v in variaveis],
        }]
    return _post({
        "messaging_product": "whatsapp",
        "to": para_e164,
        "type": "template",
        "template": template,
    })


#: Qualidade do número, como a Meta reporta. Vermelho não é aviso: é o degrau
#: antes da restrição de envio, e depois dela o número pode ser desligado.
QUALIDADE = {
    "GREEN": ("ok", "Número saudável."),
    "YELLOW": ("atencao", "A Meta rebaixou a qualidade do número. "
                          "Reduza o volume de convites e revise o texto do template."),
    "RED": ("critico", "Qualidade crítica: o número está a um passo de ser "
                       "restringido. Pare os convites frios agora."),
    "UNKNOWN": ("desconhecido", "A Meta ainda não classificou este número."),
}


def phone_quality() -> Optional[dict]:
    """
    Consulta a qualidade do número na Meta.

    Devolve `{rating, tom, recado}` ou None quando não dá para saber. É o sinal
    que antecede a restrição — e a restrição, para uma operação comercial que
    depende de um número só, é o incidente que para tudo.
    """
    if not is_configured():
        return None
    try:
        resp = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{phone_number_id()}",
            params={"fields": "quality_rating,display_phone_number,messaging_limit_tier"},
            headers={"Authorization": f"Bearer {access_token()}"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        dados = resp.json() or {}
    except Exception as e:
        logger.warning("Não foi possível consultar a qualidade do número: %s",
                       type(e).__name__)
        return None

    rating = (dados.get("quality_rating") or "UNKNOWN").upper()
    tom, recado = QUALIDADE.get(rating, QUALIDADE["UNKNOWN"])
    return {
        "rating": rating,
        "tom": tom,
        "recado": recado,
        "limite": dados.get("messaging_limit_tier"),
    }


def send_text(para_e164: str, texto: str) -> SendResult:
    """Texto livre. Só dentro da janela de 24 h — o portão garante isso."""
    if not texto or not texto.strip():
        return SendResult(False, error="Mensagem vazia.")
    return _post({
        "messaging_product": "whatsapp",
        "to": para_e164,
        "type": "text",
        # A Meta transforma links em prévia por padrão; desligado porque a
        # prévia muda o tamanho da mensagem e o que o destinatário vê.
        "text": {"preview_url": False, "body": texto[:4096]},
    })
