"""
Conector CRM genérico via webhook assinado (proposta v3 §5.5).

Cobre Zapier/Make/n8n e qualquer CRM com endpoint de entrada — incluindo
Dynamics 365 via Power Automate "When a HTTP request is received".

Configuração (env):
  CRM_WEBHOOK_URL     — destino do POST (obrigatório para ativar)
  CRM_WEBHOOK_SECRET  — chave do HMAC-SHA256 (recomendado)

O payload é assinado em X-LeadEnricher-Signature: sha256=<hex>, permitindo
ao receptor validar a origem.
"""
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, UTC
from typing import Any, List, Optional

import requests

from services._utils import is_public_url

logger = logging.getLogger(__name__)

_TIMEOUT = 10

# Erro devolvido quando a URL não passa na checagem de destino.
INVALID_TARGET = "invalid_target"


def is_valid_target(url: str) -> bool:
    """
    O destino é um endereço público de verdade?

    Sem esta checagem o webhook vira SSRF: o usuário aponta para
    http://169.254.169.254 (metadados da nuvem) ou para um serviço interno e
    faz o NOSSO servidor bater lá, de dentro da rede, com o lead no corpo.
    `is_public_url` resolve o host e recusa qualquer IP privado, loopback,
    link-local ou reservado.
    """
    return bool(url) and is_public_url(url)


def is_configured() -> bool:
    return bool(os.getenv("CRM_WEBHOOK_URL"))


def _serialize_lead(lead: Any, decision_makers: List[Any], activities: List[Any]) -> dict:
    return {
        "event": "lead.push",
        "sent_at": datetime.now(UTC).isoformat(),
        "lead": {
            "id": lead.id,
            "domain": lead.domain,
            "company_name": lead.company_name,
            "website": lead.website,
            "linkedin_url": lead.linkedin_url,
            "mx_provider": lead.mx_provider,
            "hosting_provider": lead.hosting_provider,
            "employee_count": lead.employee_count,
            "sector": lead.sector,
            "location": lead.location,
            "stage": lead.stage,
        },
        "decision_makers": [
            {
                "name": dm.name,
                "title": dm.title_found or dm.title_searched,
                "linkedin_url": dm.linkedin_url,
                "emails": dm.probable_emails,
                "phone": dm.phone,
            }
            for dm in decision_makers
        ],
        "activities": [
            {
                "type": a.type,
                "outcome": a.outcome,
                "notes": a.notes,
                "due_at": a.due_at.isoformat() if a.due_at else None,
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in activities
        ],
    }


def push_lead(
    lead: Any,
    decision_makers: List[Any],
    activities: List[Any],
    url: Optional[str] = None,
    secret: Optional[str] = None,
) -> dict:
    """
    Envia o lead completo ao webhook configurado.
    url/secret explícitos (conexão por usuário) têm precedência sobre o env.
    Retorna {ok, status_code, error}.
    """
    if url is None:
        url = os.getenv("CRM_WEBHOOK_URL", "")
        secret = os.getenv("CRM_WEBHOOK_SECRET", "")
    if not url:
        return {"ok": False, "status_code": None, "error": "not_configured"}

    # Revalidado no envio, não só na gravação: um domínio válido ontem pode
    # apontar para 127.0.0.1 hoje (DNS rebinding), e a URL do env nunca passou
    # pela validação do formulário.
    if not is_valid_target(url):
        logger.warning("CRM webhook bloqueado: destino não público (lead=%s)", lead.id)
        return {"ok": False, "status_code": None, "error": INVALID_TARGET}

    payload = _serialize_lead(lead, decision_makers, activities)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-LeadEnricher-Signature"] = f"sha256={sig}"

    try:
        # Sem seguir redirect: um destino público que responde 302 para
        # http://127.0.0.1 contornaria a validação acima.
        resp = requests.post(
            url, data=body, headers=headers, timeout=_TIMEOUT, allow_redirects=False,
        )
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning("CRM webhook returned %s for lead=%s", resp.status_code, lead.id)
        return {"ok": ok, "status_code": resp.status_code, "error": None if ok else "non_2xx"}
    except requests.RequestException as e:
        logger.warning("CRM webhook failed for lead=%s: %s", lead.id, e)
        return {"ok": False, "status_code": None, "error": str(e)}
