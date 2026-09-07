"""
Envio de e-mail transacional — hoje só o link de confirmação de opt-out.

Provedor plugável por variável de ambiente. Sem `RESEND_API_KEY`, nada é
enviado e o link vai para o log: em desenvolvimento é o comportamento certo
(dá para testar o fluxo inteiro sem provedor), e em produção o aviso no log
deixa explícito que o pedido ficou pendente de ação manual.
"""
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT = 10

DEFAULT_FROM = "LeadEnricher <nao-responda@leadenricher.app>"


def is_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


def sender() -> str:
    return os.getenv("MAIL_FROM", DEFAULT_FROM)


def send(to: str, subject: str, text: str, html: Optional[str] = None) -> bool:
    """
    Envia um e-mail. Devolve True se o provedor aceitou.

    Nunca levanta exceção: falha de e-mail não pode derrubar o fluxo que a
    disparou (o pedido de remoção continua registrado como pendente).
    """
    if not to:
        return False

    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.warning(
            "E-mail não enviado (RESEND_API_KEY ausente). Assunto=%r destinatário=%s\n%s",
            subject, to, text,
        )
        return False

    try:
        resp = requests.post(
            _RESEND_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": sender(),
                "to": [to],
                "subject": subject,
                "text": text,
                **({"html": html} if html else {}),
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 300:
            logger.warning("Provedor de e-mail recusou (%s): %s", resp.status_code, resp.text[:300])
            return False
        return True
    except requests.RequestException as e:
        logger.warning("Falha ao enviar e-mail: %s", e)
        return False
