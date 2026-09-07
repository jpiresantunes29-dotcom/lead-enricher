"""
Resolver DNS compartilhado por dns_lookup, dns_intel e email_verifier.

Por que existe: o resolver que o dnspython monta sozinho vem da configuração
do sistema, e essa configuração mente com frequência. No Windows ele lê os
servidores de TODOS os adaptadores — inclusive VPN desligada e placa virtual
morta — e fica esperando resposta de um IP interno que não responde mais.
Como toda consulta do relatório roda com `except: return []`, o efeito na tela
não é "erro de DNS": é uma ficha vazia, indistinguível de um domínio que
realmente não publica nada.

A regra aqui é: tentar o resolver do sistema, e quando ele estourar o tempo
(não quando ele responder "não existe"), repetir a mesma consulta em um
resolver público. Se o sistema falhar uma vez, as consultas seguintes deste
processo já começam pelo público — senão cada campo da ficha pagaria de novo
os segundos de timeout.

Configuração (ambas opcionais, lista separada por vírgula):
  DNS_RESOLVERS           substitui o resolver do sistema
  DNS_FALLBACK_RESOLVERS  substitui os públicos; vazio desliga o fallback
"""
import logging
import os
from functools import lru_cache
from typing import List, Optional

import dns.exception
import dns.resolver

logger = logging.getLogger(__name__)

DEFAULT_FALLBACK = ("8.8.8.8", "1.1.1.1", "9.9.9.9")

# Timeout do sistema não resolveu e o público resolveu: não vale repetir a
# aposta perdida em cada um dos ~10 registros que a ficha consulta.
_primary_degraded = False

# Erros que significam "esse servidor não está respondendo" — os únicos que
# valem uma segunda tentativa. NXDOMAIN e NoAnswer são respostas de verdade:
# repetir em outro servidor daria o mesmo, com o dobro do tempo.
# `LifetimeTimeout` (o que o dnspython levanta quando estoura o lifetime) é
# subclasse de `dns.exception.Timeout`, então já entra por aqui.
_UNREACHABLE = (dns.resolver.NoNameservers, dns.exception.Timeout)


def _parse_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [ip.strip() for ip in raw.split(",") if ip.strip()]


@lru_cache(maxsize=1)
def _primary() -> dns.resolver.Resolver:
    configured = _parse_list(os.getenv("DNS_RESOLVERS"))
    if configured:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = configured
        logger.info("DNS primário configurado por DNS_RESOLVERS: %s", configured)
        return resolver
    return dns.resolver.Resolver()


@lru_cache(maxsize=1)
def _fallback() -> Optional[dns.resolver.Resolver]:
    raw = os.getenv("DNS_FALLBACK_RESOLVERS")
    servers = _parse_list(raw) if raw is not None else list(DEFAULT_FALLBACK)
    if not servers:
        return None
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = servers
    return resolver


def resolve(name: str, rtype: str, lifetime: float = 5.0):
    """
    Mesma assinatura e mesmas exceções de `dns.resolver.resolve` — quem chama
    continua tratando NXDOMAIN/NoAnswer como sempre tratou.

    A diferença é o que acontece quando o servidor não responde: em vez de
    devolver o timeout para o chamador (que o transformaria em campo vazio),
    a consulta é repetida em um resolver público.
    """
    global _primary_degraded
    fallback = _fallback()

    if _primary_degraded and fallback is not None:
        return fallback.resolve(name, rtype, lifetime=lifetime)

    try:
        return _primary().resolve(name, rtype, lifetime=lifetime)
    except _UNREACHABLE as e:
        if fallback is None:
            raise
        logger.warning(
            "DNS do sistema não respondeu para %s/%s (%s); tentando %s",
            name, rtype, type(e).__name__, fallback.nameservers,
        )
        answer = fallback.resolve(name, rtype, lifetime=lifetime)
        # Só marca como degradado depois que o público provou que o problema
        # era o servidor, não a rede inteira nem o domínio.
        if not _primary_degraded:
            _primary_degraded = True
            logger.warning(
                "DNS do sistema marcado como indisponível neste processo; "
                "as próximas consultas vão direto para %s. "
                "Para fixar isso, defina DNS_RESOLVERS.",
                fallback.nameservers,
            )
        return answer


def reset_state() -> None:
    """Zera o cache de resolvers e a marca de degradado (usado nos testes)."""
    global _primary_degraded
    _primary_degraded = False
    # Nos testes esses nomes costumam estar monkeypatchados por funções
    # simples, que não têm cache para limpar.
    for fn in (_primary, _fallback):
        limpar = getattr(fn, "cache_clear", None)
        if limpar:
            limpar()
