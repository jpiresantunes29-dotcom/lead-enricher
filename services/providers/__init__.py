"""
Camada de provedores premium plugáveis (proposta v3 §5.4).

A pipeline gratuita (scraping + SMTP probe) é sempre o default; provedores
pagos são *enhancers* opcionais ativados por env var. Cada módulo expõe:
  is_configured() -> bool
  verify_email(email) -> Optional[str]   # valid|invalid|catch_all|unknown|None
"""
from . import hunter

# Ordem de preferência para verificação de e-mail premium
EMAIL_VERIFIERS = [hunter]


def premium_verify_email(email: str):
    """Tenta verificar via provedores premium configurados. None = sem resposta."""
    for provider in EMAIL_VERIFIERS:
        if provider.is_configured():
            status = provider.verify_email(email)
            if status:
                return status
    return None
