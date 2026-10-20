"""
Embedded Signup: conectar o WhatsApp Business pelo popup da Meta.

Substitui o "cole aqui o Phone Number ID e o token" por um botão. O usuário
entra com o Facebook, escolhe a empresa e — no modo **Coexistência** — lê um
QR code com o app WhatsApp Business do celular. O número continua no celular e
passa a funcionar também aqui.

O que o popup devolve ao navegador não é credencial nenhuma: é um `code` de
uso único que **expira em 30 segundos** e os ids da conta (`waba_id`, às vezes
`phone_number_id`). Quem transforma o `code` em token é o servidor, com o App
Secret — que por isso nunca vai à tela.

Depois de conectado, três chamadas, nesta ordem:

  1. `assinar_webhooks`   o app passa a receber os webhooks desta conta.
  2. `sincronizar(contatos)` e `sincronizar(historico)` — só na Coexistência.
     Cada uma vale **uma vez por conexão** e precisa acontecer em até 24 h;
     depois disso, só desconectando no celular e refazendo o fluxo.

O que chega depois (histórico, ecos do celular, agenda) é tratado em
services/wa/coexistencia.py, a partir do webhook.

Variáveis do servidor:

  META_APP_ID          id do app na Meta (público; vai para o SDK do navegador)
  META_ES_CONFIG_ID    id da configuração de Embedded Signup (Facebook Login
                       for Business), também público
  WHATSAPP_APP_SECRET  o App Secret — o mesmo que assina o webhook
"""
import logging
import os
from typing import Optional

import requests

from services.wa.client import GRAPH_VERSION, TIMEOUT

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com"

#: Os dois tipos de sincronização da Coexistência, como a Meta os nomeia.
SYNC_CONTATOS = "smb_app_state_sync"
SYNC_HISTORICO = "history"

#: Valor de `featureType` que abre o fluxo de Coexistência no popup.
FEATURE_COEXISTENCIA = "whatsapp_business_app_onboarding"

#: Eventos de fim de fluxo que o popup posta para a página. Só o primeiro é
#: Coexistência; `FINISH` é número novo (sem celular, sem histórico).
EVENTO_COEXISTENCIA = "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"
EVENTOS_DE_SUCESSO = (EVENTO_COEXISTENCIA, "FINISH", "FINISH_ONLY_WABA")


def app_id() -> str:
    return (os.getenv("META_APP_ID") or "").strip()


def config_id() -> str:
    return (os.getenv("META_ES_CONFIG_ID") or "").strip()


def _app_secret() -> str:
    return (os.getenv("WHATSAPP_APP_SECRET") or "").strip()


def configuracao() -> dict:
    """
    O que a tela precisa para abrir o popup — tudo público.

    `faltando` existe pelo mesmo motivo que em `Credenciais.faltando`: botão
    que some não ensina nada; botão apagado dizendo o que falta, sim.
    """
    faltando = []
    if not app_id():
        faltando.append("META_APP_ID")
    if not config_id():
        faltando.append("META_ES_CONFIG_ID")
    if not _app_secret():
        faltando.append("WHATSAPP_APP_SECRET")
    return {
        "disponivel": not faltando,
        "faltando": faltando,
        "app_id": app_id(),
        "config_id": config_id(),
        "graph_version": GRAPH_VERSION,
        "feature_type": FEATURE_COEXISTENCIA,
    }


def _erro_da_meta(resp) -> str:
    """A frase da Meta, que diz o que fazer — melhor que um "falhou"."""
    try:
        erro = (resp.json() or {}).get("error", {})
        return erro.get("message") or f"HTTP {resp.status_code}"
    except Exception:
        return f"HTTP {resp.status_code}"


def trocar_codigo(code: str) -> tuple:
    """
    `code` do popup → token de acesso da empresa.

    Devolve `(ok, token_ou_erro)`. O `code` vale 30 segundos e uma vez só: a
    tela manda assim que recebe, e uma segunda tentativa com o mesmo `code`
    falha por definição — o usuário precisa abrir o popup de novo.
    """
    if not code:
        return False, "A Meta não devolveu o código de autorização."
    if not app_id() or not _app_secret():
        return False, "O servidor não tem META_APP_ID e WHATSAPP_APP_SECRET configurados."
    try:
        resp = requests.get(
            f"{_GRAPH}/{GRAPH_VERSION}/oauth/access_token",
            params={"client_id": app_id(), "client_secret": _app_secret(), "code": code},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return False, "Não foi possível falar com a Meta agora. Tente de novo."
    if resp.status_code >= 400:
        # Não loga a frase: ela pode ecoar parâmetros da requisição.
        logger.warning("Meta recusou a troca do código (HTTP %s).", resp.status_code)
        return False, f"A Meta recusou a autorização: {_erro_da_meta(resp)}"
    token = (resp.json() or {}).get("access_token")
    if not token:
        return False, "A Meta não devolveu o token de acesso."
    return True, token


def numero_da_conta(waba_id: str, token: str,
                    preferido: Optional[str] = None) -> tuple:
    """
    Qual número da conta (WABA) foi conectado.

    O evento de Coexistência traz só o `waba_id` — o `phone_number_id` vem
    daqui. Com mais de um número na conta, vale o que o popup informou; sem
    essa pista, o que está no app do celular (`is_on_biz_app`), que é o que a
    Coexistência acabou de ligar.

    Devolve `(ok, dados_ou_erro)`, com `dados` no formato de `client.conferir`.
    """
    if not waba_id:
        return False, "A Meta não informou a conta do WhatsApp Business."
    try:
        resp = requests.get(
            f"{_GRAPH}/{GRAPH_VERSION}/{waba_id}/phone_numbers",
            params={"fields": "id,display_phone_number,verified_name,"
                              "quality_rating,is_on_biz_app,platform_type"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return False, "Não foi possível falar com a Meta agora. Tente de novo."
    if resp.status_code >= 400:
        return False, f"A Meta recusou: {_erro_da_meta(resp)}"

    numeros = (resp.json() or {}).get("data") or []
    if not numeros:
        return False, "Esta conta do WhatsApp Business não tem nenhum número."

    escolhido = None
    if preferido:
        escolhido = next((n for n in numeros if str(n.get("id")) == str(preferido)), None)
    if escolhido is None:
        escolhido = next((n for n in numeros if n.get("is_on_biz_app")), numeros[0])

    return True, {
        "phone_number_id": str(escolhido.get("id") or ""),
        "display_phone_number": escolhido.get("display_phone_number"),
        "verified_name": escolhido.get("verified_name"),
        "waba_id": waba_id,
        "quality_rating": escolhido.get("quality_rating"),
        "coexistencia": bool(escolhido.get("is_on_biz_app")),
    }


def assinar_webhooks(waba_id: str, token: str) -> tuple:
    """
    Inscreve o app nos webhooks desta conta.

    Sem isto a conexão parece pronta e nada chega: o webhook do app recebe só
    das contas inscritas. Os *campos* (messages, history, smb_message_echoes,
    smb_app_state_sync) são escolhidos uma vez no painel do app, não aqui.
    """
    try:
        resp = requests.post(
            f"{_GRAPH}/{GRAPH_VERSION}/{waba_id}/subscribed_apps",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return False, "Não foi possível falar com a Meta para assinar os webhooks."
    if resp.status_code >= 400:
        return False, f"A Meta recusou a assinatura dos webhooks: {_erro_da_meta(resp)}"
    return True, None


def sincronizar(phone_number_id: str, token: str, tipo: str) -> tuple:
    """
    Pede à Meta a agenda (`SYNC_CONTATOS`) ou o histórico (`SYNC_HISTORICO`).

    A resposta só confirma que o pedido foi aceito; o conteúdo chega depois,
    em lotes, pelo webhook. Uma vez por conexão — repetir dá erro da Meta.
    """
    if tipo not in (SYNC_CONTATOS, SYNC_HISTORICO):
        raise ValueError(f"Tipo de sincronização inválido: {tipo!r}")
    try:
        resp = requests.post(
            f"{_GRAPH}/{GRAPH_VERSION}/{phone_number_id}/smb_app_data",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "sync_type": tipo},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return False, "Não foi possível falar com a Meta para sincronizar."
    if resp.status_code >= 400:
        logger.warning("Meta recusou a sincronização %s (HTTP %s).", tipo, resp.status_code)
        return False, f"A Meta recusou a sincronização: {_erro_da_meta(resp)}"
    return True, (resp.json() or {}).get("request_id")
