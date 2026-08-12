"""
Autenticidade do webhook da Meta.

O endpoint que recebe mensagens é público — tem que ser, é a Meta que chama.
Sem verificar assinatura, qualquer um forja uma "mensagem do lead" e faz o
sistema responder para um número escolhido por ele, no nosso número, às nossas
custas. A assinatura é o que separa "webhook aberto" de "webhook público".

A Meta assina o corpo **cru** com o App Secret e manda em
`X-Hub-Signature-256: sha256=<hex>`. Assinar o JSON reserializado não funciona:
um espaço a mais e o digest muda.
"""
import hashlib
import hmac
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "x-hub-signature-256"
_PREFIX = "sha256="


def app_secret() -> str:
    return (os.getenv("WHATSAPP_APP_SECRET") or "").strip()


def is_configured() -> bool:
    return bool(app_secret())


def expected_signature(corpo: bytes, secret: Optional[str] = None) -> str:
    """Assinatura que a Meta deveria ter mandado para este corpo."""
    chave = (secret if secret is not None else app_secret()).encode("utf-8")
    digest = hmac.new(chave, corpo, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"


def verify_signature(corpo: bytes, assinatura: Optional[str],
                     secret: Optional[str] = None) -> bool:
    """
    Confere a assinatura em tempo constante.

    Recusa quando não há segredo configurado. A alternativa — "sem segredo,
    aceita tudo" — transformaria uma variável de ambiente esquecida no deploy
    em um endpoint aberto, que é exatamente o incidente que ninguém percebe
    até aparecer no extrato.
    """
    if not is_configured() and secret is None:
        logger.error("WHATSAPP_APP_SECRET ausente: webhook recusado.")
        return False
    if not assinatura:
        return False

    recebida = assinatura.strip()
    if not recebida.startswith(_PREFIX):
        return False
    return hmac.compare_digest(recebida, expected_signature(corpo, secret))


def verify_token() -> str:
    """Token do handshake de verificação (GET) que a Meta faz ao cadastrar."""
    return (os.getenv("WHATSAPP_VERIFY_TOKEN") or "").strip()


def check_verify_token(recebido: Optional[str]) -> bool:
    esperado = verify_token()
    if not esperado or not recebido:
        return False
    return hmac.compare_digest(recebido.strip(), esperado)
