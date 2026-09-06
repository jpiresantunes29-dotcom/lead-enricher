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

Cada envio sai por um número, e o número é de alguém: as funções daqui
recebem `cred` (services/wa/credenciais.py) dizendo por qual conta falar.
Sem `cred` valem as variáveis de ambiente — é o que mantém de pé a
instalação que ainda não conectou nenhuma conta.
"""
import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests

from services.wa.credenciais import Credenciais, do_ambiente

logger = logging.getLogger(__name__)

GRAPH_VERSION = os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0")
TIMEOUT = 20


def _cred(cred: Optional[Credenciais]) -> Credenciais:
    return cred if cred is not None else do_ambiente()


def phone_number_id(cred: Optional[Credenciais] = None) -> str:
    """Id do número emissor no WABA — não é o telefone, é o id da Meta."""
    return _cred(cred).phone_number_id


def access_token(cred: Optional[Credenciais] = None) -> str:
    return _cred(cred).access_token


def template_name(cred: Optional[Credenciais] = None) -> str:
    """Template de abertura aprovado. Sem ele não há primeiro contato."""
    return _cred(cred).template_name


def template_language(cred: Optional[Credenciais] = None) -> str:
    return _cred(cred).template_language


def is_configured(cred: Optional[Credenciais] = None) -> bool:
    """
    Tudo configurado? Enquanto não estiver, o produto continua de pé e a tela
    mostra o botão apagado dizendo o que falta — em vez de sumir com ele.
    """
    return _cred(cred).configurado


def missing_config(cred: Optional[Credenciais] = None) -> list:
    """O que falta, em nomes que a tela mostra para quem for configurar."""
    return _cred(cred).faltando()


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


def _url(cred: Credenciais) -> str:
    return f"https://graph.facebook.com/{GRAPH_VERSION}/{cred.phone_number_id}/messages"


def _post(payload: dict, cred: Optional[Credenciais] = None) -> SendResult:
    cred = _cred(cred)
    if cred.erro:
        return SendResult(False, error=cred.erro)
    if not cred.configurado:
        return SendResult(False, error="WhatsApp não configurado nesta conta.")
    try:
        resp = requests.post(
            _url(cred),
            headers={"Authorization": f"Bearer {cred.access_token}",
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
                  variaveis: Optional[list] = None,
                  cred: Optional[Credenciais] = None) -> SendResult:
    """
    Envia o template de abertura. **Custa dinheiro por mensagem.**

    Chame só a partir de uma ação explícita do usuário — nunca de um laço,
    nunca de um cron. Abertura fria em massa é o caminho mais rápido para a
    Meta restringir o número.
    """
    cred = _cred(cred)
    nome = nome or cred.template_name
    if not nome:
        return SendResult(False, error="Nenhum template de abertura configurado.")

    template: dict = {"name": nome, "language": {"code": cred.template_language}}
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
    }, cred)


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


def phone_quality(cred: Optional[Credenciais] = None) -> Optional[dict]:
    """
    Consulta a qualidade do número na Meta.

    Devolve `{rating, tom, recado}` ou None quando não dá para saber. É o sinal
    que antecede a restrição — e a restrição, para uma operação comercial que
    depende de um número só, é o incidente que para tudo.
    """
    cred = _cred(cred)
    if not cred.configurado:
        return None
    try:
        resp = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{cred.phone_number_id}",
            params={"fields": "quality_rating,display_phone_number,messaging_limit_tier"},
            headers={"Authorization": f"Bearer {cred.access_token}"},
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


def send_text(para_e164: str, texto: str,
              cred: Optional[Credenciais] = None) -> SendResult:
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
    }, cred)


def conferir(cred: Credenciais) -> tuple:
    """
    As credenciais funcionam? Pergunta à Meta pelo próprio número.

    É o que separa "gravei o token" de "o token vale": token expirado só
    apareceria no primeiro convite, que é pago e vai para o celular de alguém.
    Devolve `(ok, dados_ou_erro)` — em `dados`, o número legível e o WABA, que
    a tela mostra para o usuário confirmar que conectou o número certo.
    """
    if not cred.phone_number_id or not cred.access_token:
        return False, "Informe o id do número e o token de acesso."
    try:
        resp = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{cred.phone_number_id}",
            params={"fields": "display_phone_number,verified_name,quality_rating,"
                              "whatsapp_business_account_id"},
            headers={"Authorization": f"Bearer {cred.access_token}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return False, "Não foi possível falar com a Meta agora. Tente de novo."

    if resp.status_code >= 400:
        try:
            erro = (resp.json() or {}).get("error", {})
            msg = erro.get("message") or f"HTTP {resp.status_code}"
        except Exception:
            msg = f"HTTP {resp.status_code}"
        # A frase da Meta é a que diz o que fazer ("token expirado", "número
        # não pertence a este app"); repassá-la é melhor do que traduzir para
        # um "falhou" que não ajuda ninguém.
        return False, f"A Meta recusou: {msg}"

    dados = resp.json() or {}
    return True, {
        "display_phone_number": dados.get("display_phone_number"),
        "verified_name": dados.get("verified_name"),
        "waba_id": dados.get("whatsapp_business_account_id"),
        "quality_rating": dados.get("quality_rating"),
    }


def listar_templates(cred: Credenciais) -> tuple:
    """
    Os templates aprovados da conta, para a tela oferecer uma lista em vez de
    pedir que o usuário digite o nome exato de cabeça.

    Precisa do WABA id (o número sozinho não lista templates). Sem ele,
    devolve lista vazia sem erro: é informação de conforto, não requisito.
    """
    if not cred.access_token:
        return False, "Sem token de acesso."
    waba = (getattr(cred, "waba_id", "") or "").strip()
    if not waba:
        return True, []
    try:
        resp = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{waba}/message_templates",
            params={"fields": "name,status,language,category", "limit": 100},
            headers={"Authorization": f"Bearer {cred.access_token}"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        dados = resp.json() or {}
    except Exception:
        return True, []

    return True, [
        {"name": t.get("name"), "language": t.get("language"),
         "category": t.get("category"), "status": t.get("status")}
        for t in (dados.get("data") or [])
        if (t.get("status") or "").upper() == "APPROVED"
    ]
