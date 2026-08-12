"""
A parte de IA — e só a parte de IA.

Este módulo faz duas coisas e nenhuma outra:
  1. **classifica** a intenção da última mensagem do lead;
  2. **redige** um rascunho de resposta.

Ele não decide se a mensagem sai, não muda estado, não grava nada e não fala
com a Meta. Quem decide é `orchestrator.py`, com regras em código.

A razão é assimétrica, e vale escrever: uma classificação errada que leva a
**parar** custa um lead. Uma classificação errada que leva a **enviar** custa
uma mensagem indevida no celular de alguém, e é assim que se perde um número.
Por isso tudo que dá errado aqui — rede fora, JSON quebrado, confiança baixa,
intenção desconhecida — vira `AMBIGUO`, que o orquestrador trata como
"chame o humano" e nunca como "responda".
"""
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MODEL = os.getenv("WA_AI_MODEL", os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"))
_TIMEOUT = int(os.getenv("WA_AI_TIMEOUT", "20"))

# Resposta de qualificação é curta por natureza. O teto existe também para o
# turno caber no tempo da função serverless.
_MAX_TOKENS = 400

# ── Intenções ────────────────────────────────────────────────────────────────
# São as únicas que o orquestrador sabe tratar. Qualquer coisa fora desta lista
# vira AMBIGUO — inclusive uma intenção nova que o modelo resolva inventar.

CONFIRMOU_PESSOA = "CONFIRMOU_PESSOA"   # "sou eu mesmo", "pode falar"
CONVERSANDO = "CONVERSANDO"             # abertura, pergunta simples sobre o assunto
QUER_HUMANO = "QUER_HUMANO"             # "quero falar com alguém"
NEGOCIANDO = "NEGOCIANDO"               # interesse concreto, preço, proposta
JA_E_CLIENTE = "JA_E_CLIENTE"           # "já trabalhamos com vocês"
PEDIU_PARAR = "PEDIU_PARAR"             # "não quero mais receber"
PESSOA_ERRADA = "PESSOA_ERRADA"         # "não sou eu quem cuida disso"
FORA_DA_BASE = "FORA_DA_BASE"           # pergunta que não temos como responder
AMBIGUO = "AMBIGUO"                     # não deu para entender — e o padrão

INTENCOES = (
    CONFIRMOU_PESSOA, CONVERSANDO, QUER_HUMANO, NEGOCIANDO, JA_E_CLIENTE,
    PEDIU_PARAR, PESSOA_ERRADA, FORA_DA_BASE, AMBIGUO,
)

# Abaixo disto, a classificação não é usada: vira AMBIGUO. O modelo é bom em
# dizer o óbvio e ruim em admitir dúvida, então o corte é deliberadamente alto.
CONFIANCA_MINIMA = float(os.getenv("WA_AI_MIN_CONFIDENCE", "0.7"))


@dataclass(frozen=True)
class Leitura:
    """O que a IA entendeu e escreveu. Nada aqui é uma decisão."""
    intencao: str
    confianca: float
    rascunho: Optional[str] = None
    erro: Optional[str] = None

    @property
    def confiavel(self) -> bool:
        return self.intencao in INTENCOES and self.confianca >= CONFIANCA_MINIMA


def is_configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


_INSTRUCOES = """Você lê mensagens de WhatsApp que chegam para um vendedor brasileiro \
e faz DUAS coisas: classifica a intenção da última mensagem do lead e escreve um \
rascunho curto de resposta em português do Brasil.

Você NÃO decide se a resposta será enviada. Um sistema separado decide isso.

Classifique a ÚLTIMA mensagem do lead em exatamente uma destas intenções:
- CONFIRMOU_PESSOA: confirma que é a pessoa certa ou que pode falar agora
- CONVERSANDO: responde ou pergunta algo sobre o assunto, sem compromisso
- QUER_HUMANO: pede para falar com uma pessoa, ou desconfia de robô
- NEGOCIANDO: demonstra interesse concreto — preço, proposta, prazo, contrato
- JA_E_CLIENTE: diz que já é cliente ou já trabalha com a empresa
- PEDIU_PARAR: pede para não receber mais mensagens, reclama do contato
- PESSOA_ERRADA: diz que não é com ela, indica outra pessoa ou setor
- FORA_DA_BASE: pergunta algo que só quem conhece o negócio poderia responder
- AMBIGUO: não deu para entender com segurança

Na dúvida entre duas, escolha AMBIGUO. Errar para AMBIGUO é barato.

"confianca" é de 0 a 1 e deve refletir dúvida real. Use abaixo de 0.7 sempre \
que a mensagem for curta demais, irônica, ou aceitar mais de uma leitura.

O rascunho:
- no máximo 2 frases curtas, tom profissional e simples, sem emoji
- nunca invente preço, prazo, produto, condição ou qualquer fato do negócio
- nunca afirme ser humano nem finja ser uma pessoa específica
- se a intenção for QUER_HUMANO, NEGOCIANDO, JA_E_CLIENTE, PEDIU_PARAR, \
FORA_DA_BASE ou AMBIGUO, devolva "rascunho": null — nesses casos quem responde \
é o vendedor
- o objetivo, quando faz sentido, é propor uma ligação rápida

Responda SOMENTE com um objeto JSON, sem cercas de código e sem comentários:
{"intencao": "...", "confianca": 0.0, "rascunho": "..." }"""

_MODO_CURTO = """
ATENÇÃO: está fora do horário comercial. O rascunho deve ter no máximo UMA \
frase, reconhecer a mensagem e propor retomar em horário comercial. Não \
aprofunde o assunto."""


def _historico(mensagens: List[dict], limite: int = 12) -> str:
    """
    As últimas trocas, em texto simples.

    Só o suficiente para o modelo entender o fio da conversa: mandar tudo
    encarece o turno e não melhora a classificação da última mensagem.
    """
    recentes = mensagens[-limite:]
    linhas = []
    for m in recentes:
        quem = "LEAD" if m.get("direction") == "in" else "VENDEDOR"
        corpo = (m.get("body") or "").strip()
        if corpo:
            linhas.append(f"{quem}: {corpo}")
    return "\n".join(linhas)


def _extrair_json(texto: str) -> Optional[dict]:
    """
    O primeiro objeto JSON do texto.

    O modelo às vezes embrulha em ```json apesar da instrução. Em vez de
    confiar no formato, procuramos o objeto — e se não houver, o chamador
    trata como ambíguo.
    """
    if not texto:
        return None
    try:
        return json.loads(texto)
    except Exception:
        pass
    match = re.search(r"\{.*\}", texto, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _chamar(prompt: str) -> Optional[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.post(
            _API_URL,
            headers={"x-api-key": api_key, "anthropic-version": _API_VERSION,
                     "content-type": "application/json"},
            json={"model": _MODEL, "max_tokens": _MAX_TOKENS,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        blocos = (resp.json() or {}).get("content", [])
        return "".join(b.get("text", "") for b in blocos if b.get("type") == "text").strip()
    except Exception as e:
        # A mensagem do lead pode estar no corpo do erro; só o tipo vai ao log.
        logger.warning("Falha na chamada de classificação: %s", type(e).__name__)
        return None


def ler(mensagens: List[dict], empresa: Optional[str] = None,
        fora_do_horario: bool = False) -> Leitura:
    """
    Classifica a última mensagem do lead e propõe um rascunho.

    Nunca levanta exceção: qualquer problema vira `AMBIGUO` com o motivo em
    `erro`. O orquestrador trata ambíguo como "chame o humano", então falhar
    aqui degrada para conversa humana — nunca para uma resposta automática que
    ninguém revisou.
    """
    if not is_configured():
        return Leitura(AMBIGUO, 0.0, erro="IA não configurada.")

    historico = _historico(mensagens)
    if not historico:
        return Leitura(AMBIGUO, 0.0, erro="Sem mensagens para ler.")

    prompt = (
        _INSTRUCOES
        + (_MODO_CURTO if fora_do_horario else "")
        + f"\n\nEmpresa do lead: {empresa or 'não informada'}"
        + f"\n\nConversa até aqui:\n{historico}"
    )
    bruto = _chamar(prompt)
    if bruto is None:
        return Leitura(AMBIGUO, 0.0, erro="A IA não respondeu.")

    dados = _extrair_json(bruto)
    if not isinstance(dados, dict):
        logger.warning("Classificação sem JSON reconhecível.")
        return Leitura(AMBIGUO, 0.0, erro="Resposta da IA fora do formato.")

    intencao = str(dados.get("intencao") or "").strip().upper()
    if intencao not in INTENCOES:
        # Inclusive uma intenção nova que o modelo tenha inventado.
        return Leitura(AMBIGUO, 0.0, erro=f"Intenção desconhecida: {intencao[:40]}")

    try:
        confianca = float(dados.get("confianca"))
    except (TypeError, ValueError):
        confianca = 0.0
    confianca = max(0.0, min(1.0, confianca))

    rascunho = dados.get("rascunho")
    rascunho = rascunho.strip() if isinstance(rascunho, str) and rascunho.strip() else None
    return Leitura(intencao, confianca, rascunho=rascunho)
