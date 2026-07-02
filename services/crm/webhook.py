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

logger = logging.getLogger(__name__)

_TIMEOUT = 10


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
            "score": lead.score,
            "priority": lead.priority,
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


def push_lead(lead: Any, decision_makers: List[Any], activities: List[Any]) -> dict:
    """
    Envia o lead completo ao webhook configurado.
    Retorna {ok, status_code, error}.
    """
    url = os.getenv("CRM_WEBHOOK_URL", "")
    if not url:
        return {"ok": False, "status_code": None, "error": "not_configured"}

    payload = _serialize_lead(lead, decision_makers, activities)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    secret = os.getenv("CRM_WEBHOOK_SECRET", "")
    if secret:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-LeadEnricher-Signature"] = f"sha256={sig}"

    try:
        resp = requests.post(url, data=body, headers=headers, timeout=_TIMEOUT)
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning("CRM webhook returned %s for lead=%s", resp.status_code, lead.id)
        return {"ok": ok, "status_code": resp.status_code, "error": None if ok else "non_2xx"}
    except requests.RequestException as e:
        logger.warning("CRM webhook failed for lead=%s: %s", lead.id, e)
        return {"ok": False, "status_code": None, "error": str(e)}
