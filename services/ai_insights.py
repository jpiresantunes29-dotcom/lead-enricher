"""
Fase 5 — Inteligência de abordagem via Groq API
(docs/PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md §9).

Ativado por GROQ_API_KEY no ambiente; sem a chave, os endpoints
respondem 503 e o restante do produto segue funcionando normalmente.
Usa requests direto (sem SDK) para não adicionar dependência. A API do
Groq é compatível com o formato de chat completions da OpenAI.
"""
import json
import logging
import os
from typing import Any, List, Optional

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Modelo grande e gratuito no free tier do Groq — suficiente para o caso de uso
_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
_TIMEOUT = 30


def is_configured() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def _call_claude(prompt: str, max_tokens: int = 600) -> Optional[str]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json={
                "model": _MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return None
        text = choices[0].get("message", {}).get("content") or ""
        return text.strip() or None
    except Exception as e:
        logger.warning("Groq API call failed: %s", e)
        return None


def _lead_context(lead: Any, decision_makers: Optional[List[Any]] = None) -> str:
    """Monta o contexto compacto do lead para o prompt (só campos preenchidos)."""
    parts = []

    def add(label, value):
        if value:
            parts.append(f"{label}: {value}")

    add("Empresa", getattr(lead, "company_name", None))
    add("Domínio", getattr(lead, "domain", None))
    add("Setor", getattr(lead, "sector", None))
    add("Localização", getattr(lead, "location", None))
    add("Descrição", (getattr(lead, "description", None) or "")[:400])
    add("Provedor de e-mail", getattr(lead, "mx_provider", None))
    add("Hosting", getattr(lead, "hosting_provider", None))

    emp = getattr(lead, "employee_count", None)
    if isinstance(emp, dict):
        add("Funcionários", emp.get("exact") or emp.get("band") or emp.get("min"))

    dns = getattr(lead, "dns_report", None)
    if isinstance(dns, dict):
        signals = []
        if dns.get("spf"):
            signals.append("SPF")
        if dns.get("dmarc"):
            signals.append("DMARC")
        if signals:
            add("Maturidade de e-mail", " + ".join(signals))

    for dm in (decision_makers or [])[:3]:
        name = getattr(dm, "name", None)
        title = getattr(dm, "title_found", None) or getattr(dm, "title_searched", None)
        if name:
            parts.append(f"Decisor: {name} ({title or 'cargo n/d'})")

    return "\n".join(parts)


def generate_summary(lead: Any, decision_makers: Optional[List[Any]] = None) -> Optional[str]:
    """Resumo executivo pré-ligação (1 parágrafo + 3 bullets de abordagem)."""
    context = _lead_context(lead, decision_makers)
    prompt = (
        "Você é um analista de pré-vendas B2B brasileiro. Com base nos dados "
        "coletados automaticamente abaixo, escreva em português:\n"
        "1. Um parágrafo curto (máx. 60 palavras) resumindo quem é a empresa e "
        "seu provável momento/maturidade tecnológica.\n"
        "2. Três bullets objetivos de ganchos de abordagem comercial baseados "
        "APENAS nas evidências dos dados (ex.: provedor de e-mail, porte, decisores).\n"
        "Não invente fatos que não estejam nos dados.\n\n"
        f"DADOS:\n{context}"
    )
    return _call_claude(prompt, max_tokens=500)
