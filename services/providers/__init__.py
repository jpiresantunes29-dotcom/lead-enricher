"""
Camada de provedores premium plugáveis (proposta v3 §5.4).

A pipeline gratuita (padrão de domínio + SMTP + site + Receita) é sempre o
default. Provedores pagos são *enhancers* opcionais, ativados por env var:
sem chave configurada, nada aqui faz requisição e nada custa.

Contrato de cada módulo:
  is_configured() -> bool
  verify_email(email) -> Optional[str]              # valid|invalid|catch_all|unknown
  find_contacts(**kwargs) -> Optional[dict]         # opcional

Retorno de find_contacts:
  {
    "provider": "apollo",
    "emails": [{"email": ..., "status": ..., "confidence": 0-100}],
    "phones": [{"e164": ..., "formatted": ..., "type": "mobile", "confidence": 0-100}],
    "cost_usd": 0.0,
  }
"""
import logging
from typing import Optional

from . import cnpj_receita, hunter

logger = logging.getLogger(__name__)

# Ordem de preferência para verificação de e-mail premium
EMAIL_VERIFIERS = [hunter]

# Provedores capazes de descobrir contato a partir de nome + empresa.
# Vazio hoje: o produto inteiro roda sem custo. Quando houver orçamento,
# basta criar o módulo (apollo.py, dropcontact.py...) e registrá-lo aqui.
CONTACT_FINDERS: list = []


def premium_verify_email(email: str) -> Optional[str]:
    """Tenta verificar via provedores premium configurados. None = sem resposta."""
    for provider in EMAIL_VERIFIERS:
        if provider.is_configured():
            status = provider.verify_email(email)
            if status:
                return status
    return None


def premium_find_contacts(**kwargs) -> Optional[dict]:
    """
    Cascata de provedores pagos de descoberta de contato.
    Sem nenhum configurado, devolve None imediatamente (custo zero, 0 ms).
    """
    for provider in CONTACT_FINDERS:
        try:
            if not provider.is_configured():
                continue
            result = provider.find_contacts(**kwargs)
            if result and (result.get("emails") or result.get("phones")):
                return result
        except Exception as e:
            logger.warning("Provedor %s falhou: %s", getattr(provider, "__name__", provider), e)
    return None


def configured_providers() -> list:
    """Nomes dos provedores pagos ativos — exibido em /api/extension/me."""
    active = []
    for provider in EMAIL_VERIFIERS + CONTACT_FINDERS:
        try:
            if provider.is_configured():
                active.append(provider.__name__.rsplit(".", 1)[-1])
        except Exception:
            continue
    return active


__all__ = [
    "cnpj_receita",
    "hunter",
    "premium_verify_email",
    "premium_find_contacts",
    "configured_providers",
]
