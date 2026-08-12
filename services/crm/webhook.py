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


#: Nossa atividade → entidade do Dynamics 365.
#:
#: Vai no payload para o fluxo do Power Automate poder ser um `switch` num
#: campo só, em vez de uma escada de condições sobre o nosso vocabulário
#: interno. Quem monta o fluxo não deveria precisar aprender os nomes que
#: usamos aqui dentro.
ENTIDADE_DYNAMICS = {
    "call": "phonecall",
    "meeting": "appointment",
    "task": "task",
    "email": "email",
    "note": "annotation",
}
#: Atividade de tipo desconhecido vira anotação: registra sem inventar
#: semântica que o Dynamics cobraria em campos obrigatórios.
ENTIDADE_PADRAO = "annotation"


def dedup_key(lead: Any) -> Optional[str]:
    """
    Chave estável para o CRM decidir entre criar e atualizar.

    O domínio é o identificador que o produto inteiro usa e o único que
    sobrevive a uma reimportação de planilha — nosso `lead.id` muda quando a
    mesma empresa entra de novo por outro arquivo, e aí o Dynamics ganharia um
    registro duplicado por importação.
    """
    if getattr(lead, "domain", None):
        return f"domain:{lead.domain.strip().lower()}"
    if getattr(lead, "raw_input_domain", None):
        return f"domain:{lead.raw_input_domain.strip().lower()}"
    return None


def _serialize_lead(lead: Any, decision_makers: List[Any], activities: List[Any]) -> dict:
    return {
        "event": "lead.push",
        "sent_at": datetime.now(UTC).isoformat(),
        # Versão do formato. Um fluxo do Power Automate montado à mão quebra em
        # silêncio quando o payload muda de forma; com isto, dá para o fluxo
        # ramificar em vez de processar errado.
        "schema_version": 2,
        "dedup_key": dedup_key(lead),
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
            # Quem esta empresa é para nós. Vai junto porque um CRM que recebe
            # "cliente atual" na fila de prospecção gera abordagem repetida —
            # o mesmo erro que o portão evita do lado de cá.
            "relationship": getattr(lead, "relationship", None),
            "phone": lead.phone,
            "corporate_email": lead.corporate_email,
        },
        "dynamics": {
            # O alvo de cada parte, com os nomes do Dynamics.
            "lead_entity": "lead",
            "dedup_field": "domain",
            "activity_entities": sorted(set(ENTIDADE_DYNAMICS.values())),
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
                "entity": ENTIDADE_DYNAMICS.get(a.type, ENTIDADE_PADRAO),
                "outcome": a.outcome,
                "notes": a.notes,
                "due_at": a.due_at.isoformat() if a.due_at else None,
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in activities
        ],
    }
    # As conversas de WhatsApp NÃO vão neste payload, de propósito. Mandar o
    # que foi dito para um sistema de terceiros é uma decisão sobre dado
    # pessoal, não um detalhe de integração — e uma vez enviado, não volta.
    # Quando fizer sentido, decida primeiro entre metadado (com quem, quando,
    # quantas trocas) e conteúdo; o segundo precisa constar da política de
    # privacidade.


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
