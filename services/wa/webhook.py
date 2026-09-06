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
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "x-hub-signature-256"
_PREFIX = "sha256="


def phone_number_id_do_corpo(corpo: bytes) -> Optional[str]:
    """
    De qual número é este webhook, lido do corpo **antes** de validar a
    assinatura.

    Parece inverter a ordem certa, e não inverte: com uma conta por usuário,
    cada uma tem o próprio App Secret, e sem saber de quem é a mensagem não há
    com o que comparar a assinatura. O valor lido aqui serve só para **buscar
    o segredo**; nada é gravado, respondido ou enviado antes de a assinatura
    bater com ele. Um invasor escolher o `phone_number_id` do corpo só decide
    contra qual segredo a mentira dele vai ser conferida.
    """
    try:
        dados = json.loads(corpo or b"{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(dados, dict):
        return None
    for entrada in dados.get("entry") or []:
        for mudanca in (entrada or {}).get("changes") or []:
            valor = (mudanca or {}).get("value") or {}
            pnid = ((valor.get("metadata") or {}).get("phone_number_id") or "").strip()
            if pnid:
                return pnid
    return None


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
    efetivo = secret if secret is not None else app_secret()
    if not efetivo:
        # Vale tanto para a variável esquecida no deploy quanto para a conta
        # que conectou sem informar o App Secret: sem segredo não há como
        # distinguir a Meta de qualquer um, e aceitar seria abrir o endpoint.
        logger.error("Sem App Secret para conferir a assinatura: webhook recusado.")
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


def check_verify_token(recebido: Optional[str],
                       esperados: Optional[list] = None) -> bool:
    """
    Confere o token do handshake.

    O GET de verificação não diz de qual número é — a Meta só manda o token.
    Por isso `esperados` aceita vários: com uma conta por usuário, cada uma
    cadastra esta mesma URL com o token dela, e o handshake precisa reconhecer
    qualquer um deles. Comparação em tempo constante em todos, sem sair no
    primeiro acerto, para não transformar o tempo de resposta em pista.
    """
    if not recebido:
        return False
    recebido = recebido.strip()

    candidatos = [t for t in (esperados or []) if t]
    ambiente = verify_token()
    if ambiente:
        candidatos.append(ambiente)
    if not candidatos:
        return False

    achou = False
    for candidato in candidatos:
        if hmac.compare_digest(recebido, candidato):
            achou = True
    return achou
