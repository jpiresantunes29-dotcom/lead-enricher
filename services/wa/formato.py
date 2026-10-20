"""
Como a Meta escreve números e mensagens, e como o banco os guarda.

Vive fora do router porque dois caminhos precisam das mesmas respostas: o
webhook de mensagens (routers/wa.py) e o da Coexistência
(services/wa/coexistencia.py). Se cada um normalizasse o número do seu jeito,
a mensagem que chega pelo celular não encontraria a conversa aberta pela API.
"""
import re
from datetime import UTC, datetime
from typing import Optional

from services.phone_normalizer import normalize_input

_SO_DIGITOS = re.compile(r"\D")


def e164(bruto: Optional[str]) -> Optional[str]:
    """
    Número no formato que a Meta usa e que o banco guarda.

    A Meta manda `5511988887777`, sem o `+`; o usuário digita
    `(11) 98888-7777`. Os dois precisam virar a mesma string, senão a mensagem
    que chega não encontra a conversa que existe.
    """
    if not bruto or not str(bruto).strip():
        return None
    bruto = str(bruto).strip()

    # Sem `+`, um número só de dígitos é ambíguo: `5511988887777` é o formato
    # da Meta (com país), mas `11988887777` é o que se digita no Brasil.
    # Tenta primeiro como internacional e cai para a região padrão.
    if not bruto.startswith("+"):
        digitos = _SO_DIGITOS.sub("", bruto)
        dados = normalize_input("+" + digitos) if digitos else None
        if dados:
            return dados["e164"]

    dados = normalize_input(bruto)
    return dados["e164"] if dados else None


def corpo_da_mensagem(msg: dict) -> Optional[str]:
    """Texto quando há; senão, uma descrição do que veio (áudio, imagem…)."""
    tipo = msg.get("type")
    if tipo == "text":
        return (msg.get("text") or {}).get("body")
    if tipo == "button":
        return (msg.get("button") or {}).get("text")
    if tipo == "interactive":
        interativo = msg.get("interactive") or {}
        for chave in ("button_reply", "list_reply"):
            if chave in interativo:
                return (interativo[chave] or {}).get("title")
    if tipo in ("image", "video", "document"):
        # Legenda é texto que alguém escreveu; vale mais que o rótulo do tipo.
        legenda = (msg.get(tipo) or {}).get("caption")
        if legenda:
            return f"[{tipo}] {legenda}"
    if tipo == "media_placeholder":
        # Mídia do histórico da Coexistência: a Meta manda o arquivo depois,
        # e só do que foi trocado nos últimos 14 dias.
        return "[mídia]"
    return f"[{tipo or 'desconhecido'}]"


def instante(timestamp) -> Optional[datetime]:
    """Unix timestamp da Meta (string de segundos) → datetime em UTC."""
    try:
        return datetime.fromtimestamp(int(timestamp), UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
