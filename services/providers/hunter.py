"""
Hunter.io — verificação premium de e-mail (ativada por HUNTER_API_KEY).

Mapeamento de status Hunter → nosso vocabulário:
  valid/deliverable     → valid
  invalid/undeliverable → invalid
  accept_all            → catch_all
  webmail/disposable/risky/unknown → unknown
"""
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.hunter.io/v2/email-verifier"
_TIMEOUT = 12

_STATUS_MAP = {
    "valid": "valid",
    "deliverable": "valid",
    "invalid": "invalid",
    "undeliverable": "invalid",
    "accept_all": "catch_all",
}


def is_configured() -> bool:
    return bool(os.getenv("HUNTER_API_KEY"))


def verify_email(email: str) -> Optional[str]:
    api_key = os.getenv("HUNTER_API_KEY")
    if not api_key or not email:
        return None
    try:
        resp = requests.get(
            _API_URL,
            params={"email": email, "api_key": api_key},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {})
        status = data.get("status") or data.get("result")
        return _STATUS_MAP.get(status, "unknown") if status else None
    except requests.RequestException as e:
        logger.debug("Hunter verify failed for %s: %s", email, e)
        return None
