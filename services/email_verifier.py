"""
Verifica emails sem enviar mensagem real:
  - DNS MX lookup
  - SMTP HELO/EHLO + MAIL FROM + RCPT TO
  - Detecta catch-all (servidor aceita qualquer destinatário)

Status devolvidos:
  valid     : RCPT 250 e o servidor não é catch-all
  catch_all : servidor aceita qualquer email no domínio (não dá pra confirmar)
  invalid   : RCPT 550/551/553
  unknown   : timeout, greylist, conexão recusada, etc.

ATENÇÃO: muitos servidores corporativos bloqueiam SMTP probing, e ambientes
serverless (Vercel/Lambda) costumam bloquear a saída na porta 25. Por isso:
  - a sondagem é feita em lote por domínio (1 checagem de catch-all, N RCPTs)
  - falhas SEGUIDAS de conexão desligam a sondagem para o resto do processo,
    em vez de gastar 6 s por e-mail para sempre devolver "unknown"
"""
import logging
import os
import socket
import smtplib
import string
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, UTC
from functools import lru_cache
from typing import List, Optional, Tuple

import dns.resolver
import dns.exception

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "5"))
SMTP_FROM = os.getenv("SMTP_PROBE_FROM", "verify@example.com")

# Desliga a sondagem explicitamente (SMTP_PROBE=0) — útil na Vercel, onde a
# porta 25 é bloqueada e cada tentativa é só latência desperdiçada.
_PROBE_CONFIGURED = os.getenv("SMTP_PROBE", "1") != "0"

# Circuit breaker: N falhas de CONEXÃO seguidas → assume rede bloqueada.
_CONNECT_FAILURE_LIMIT = 3
_connect_failures = 0
_smtp_disabled = False

# ── Provedor premium (opcional) ──────────────────────────────────────────────
# Hunter (ou outro EMAIL_VERIFIERS de services/providers) só entra quando a
# sondagem SMTP grátis não deu resposta confiável — inclusive quando ela nem
# roda, porque `SMTP_PROBE_AVAILABLE` está desligado (comum em serverless: a
# Vercel bloqueia a porta 25, então lá TODO e-mail sairia "unknown" sem isto).
# Cada chamada é paga, então três freios convivem:
#   1. cache por e-mail (PREMIUM_CACHE_DAYS) — nunca paga duas vezes a mesma;
#   2. teto por lote (PREMIUM_MAX_PER_BATCH) — uma busca de decisor não pode
#      virar N chamadas pagas de uma vez só;
#   3. teto diário (PREMIUM_DAILY_LIMIT) — o freio que protege contra um lote
#      grande (200 domínios, vários decisores cada) somar uma conta alta sem
#      ninguém ter decidido isso.
PREMIUM_CACHE_DAYS = int(os.getenv("HUNTER_CACHE_DAYS", "90"))
PREMIUM_DAILY_LIMIT = int(os.getenv("HUNTER_DAILY_LIMIT", "200"))
PREMIUM_MAX_PER_BATCH = int(os.getenv("HUNTER_MAX_PER_BATCH", "3"))
# Estimativa para o log de custo (`ProviderCall.cost_usd`) — não é uma cobrança
# real, é o que permite somar "quanto gastamos hoje" sem inventar um valor no
# meio do código toda vez. Ajuste conforme o plano contratado no Hunter.
PREMIUM_COST_PER_CALL_USD = float(os.getenv("HUNTER_COST_PER_CALL_USD", "0.01"))


def _fresco(quando, dias: int) -> bool:
    if quando is None:
        return False
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=UTC)
    return quando >= datetime.now(UTC) - timedelta(days=dias)


def _chamadas_premium_hoje(db) -> int:
    from models.database import ProviderCall

    inicio_do_dia = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(ProviderCall)
        .filter(ProviderCall.provider == "premium_email", ProviderCall.created_at >= inicio_do_dia)
        .count()
    )


def _verificar_premium_com_cache(db, email: str) -> Optional[str]:
    """
    Verifica um e-mail no provedor premium configurado, com cache e teto diário.

    Devolve `None` só quando não há nem resposta nova nem cache — nesse caso,
    quem chamou deve manter o "unknown" que já tinha, nunca inventar um status.
    """
    from models.database import PremiumEmailCheck, ProviderCall, utcnow

    cache = db.query(PremiumEmailCheck).filter(PremiumEmailCheck.email == email).first()
    if cache and _fresco(cache.checked_at, PREMIUM_CACHE_DAYS):
        return cache.status

    if _chamadas_premium_hoje(db) >= PREMIUM_DAILY_LIMIT:
        logger.info("Teto diário de verificação premium atingido; mantendo o que há em cache.")
        return cache.status if cache else None

    from .providers import premium_verify_email

    inicio = time.monotonic()
    status = premium_verify_email(email)
    db.add(ProviderCall(
        provider="premium_email", operation="verify_email",
        hit=status is not None,
        cost_usd=PREMIUM_COST_PER_CALL_USD if status is not None else 0.0,
        latency_ms=int((time.monotonic() - inicio) * 1000),
    ))
    if status is None:
        db.commit()
        return cache.status if cache else None

    if cache:
        cache.status = status
        cache.checked_at = utcnow()
    else:
        db.add(PremiumEmailCheck(email=email, status=status))
    db.commit()
    return status


def _upgrade_com_premium(db, resultados: List[dict]) -> List[dict]:
    """Tenta o provedor premium só para quem ficou `unknown`, até o teto do lote."""
    from .providers import any_email_verifier_configured

    if not any_email_verifier_configured():
        return resultados

    tentativas = 0
    for item in resultados:
        if item["status"] != "unknown":
            continue
        if tentativas >= PREMIUM_MAX_PER_BATCH:
            break
        tentativas += 1
        status = _verificar_premium_com_cache(db, item["email"])
        if status:
            item["status"] = status
    return resultados


def uso_premium_hoje(db) -> dict:
    """Quanto já foi gasto hoje em verificação premium — para a tela mostrar."""
    usadas = _chamadas_premium_hoje(db)
    return {
        "usadas": usadas,
        "limite": PREMIUM_DAILY_LIMIT,
        "esgotado": usadas >= PREMIUM_DAILY_LIMIT,
    }


def verify_emails_effective(emails: List[str], budget_seconds: float = 8.0,
                            max_workers: int = 4, db=None) -> List[dict]:
    """
    Verificação "melhor esforço": sondagem SMTP grátis primeiro; o que sobrar
    `unknown` — inclusive quando a sondagem está globalmente desligada — tenta
    um provedor premium configurado, se `db` for passado. Sem provedor
    configurado (o caso comum, sem HUNTER_API_KEY), o comportamento é
    idêntico ao de chamar `verify_batch` direto.
    """
    emails = [e for e in emails if e and "@" in e]
    if not emails:
        return []

    if smtp_probe_available():
        resultados = verify_batch(emails, budget_seconds=budget_seconds, max_workers=max_workers)
    else:
        resultados = [{"email": e, "status": "unknown"} for e in emails]

    if db is not None:
        resultados = _upgrade_com_premium(db, resultados)

    return resultados


def smtp_probe_available() -> bool:
    """False quando a sondagem está desligada por config ou por circuit breaker."""
    return _PROBE_CONFIGURED and not _smtp_disabled


def _note_connect_failure() -> None:
    global _connect_failures, _smtp_disabled
    _connect_failures += 1
    if _connect_failures >= _CONNECT_FAILURE_LIMIT and not _smtp_disabled:
        _smtp_disabled = True
        logger.warning(
            "SMTP probing desativado neste processo após %d falhas de conexão "
            "(porta 25 provavelmente bloqueada). Confiança passa a vir do padrão de domínio.",
            _connect_failures,
        )


def _note_connect_success() -> None:
    global _connect_failures
    _connect_failures = 0


@lru_cache(maxsize=512)
def _get_mx_host(domain: str) -> Optional[str]:
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=4)
        records = sorted(answers, key=lambda r: r.preference)
        return str(records[0].exchange).rstrip(".") if records else None
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
            dns.resolver.NoNameservers, dns.exception.Timeout):
        return None
    except Exception:
        return None


def has_mx(domain: str) -> bool:
    """O domínio recebe e-mail? Sinal barato e confiável (só DNS)."""
    return bool(_get_mx_host(domain))


def _smtp_rcpt(email: str, mx_host: str) -> Tuple[str, bool]:
    """
    Sondagem SMTP RCPT TO.
    Retorna (status, conectou) — 'conectou' distingue resposta do servidor
    de falha de rede, que é o que alimenta o circuit breaker.
    """
    try:
        with smtplib.SMTP(mx_host, 25, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo_or_helo_if_needed()
            try:
                code, _ = smtp.mail(SMTP_FROM)
                if code >= 400:
                    return "unknown", True
                code, _ = smtp.rcpt(email)
            except smtplib.SMTPException:
                return "unknown", True
            if code in (250, 251):
                return "valid", True
            if code in (550, 551, 553, 554):
                return "invalid", True
            return "unknown", True
    except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError):
        return "unknown", False
    except smtplib.SMTPException:
        return "unknown", True
    except Exception:
        return "unknown", False


@lru_cache(maxsize=256)
def _is_catch_all(domain: str, mx_host: str) -> bool:
    """Testa se o domínio aceita um email aleatório → indica catch-all."""
    fake_local = "".join(random.choices(string.ascii_lowercase, k=16))
    status, connected = _smtp_rcpt(f"nonexistent_{fake_local}@{domain}", mx_host)
    if not connected:
        _note_connect_failure()
        return False
    _note_connect_success()
    return status == "valid"


def verify_email(email: str) -> str:
    """Verifica um email. Retorna: valid | catch_all | invalid | unknown."""
    if not email or "@" not in email:
        return "invalid"
    domain = email.split("@", 1)[1].lower()
    if not _get_mx_host(domain):
        return "invalid"
    if not smtp_probe_available():
        return "unknown"

    mx = _get_mx_host(domain)
    status, connected = _smtp_rcpt(email, mx)
    if not connected:
        _note_connect_failure()
        return "unknown"
    _note_connect_success()
    if status == "valid" and _is_catch_all(domain, mx):
        return "catch_all"
    return status


def verify_batch(emails: List[str], budget_seconds: float = 8.0,
                 max_workers: int = 4) -> List[dict]:
    """
    Verifica vários e-mails de forma eficiente, agrupando por domínio:

      1. um único lookup de MX por domínio  (sem MX → todos inválidos)
      2. uma única checagem de catch-all    (catch-all → nem testa os demais)
      3. RCPTs em paralelo com prazo total  (budget_seconds)

    Devolve [{email, status}] na ordem de entrada. Nunca levanta exceção e
    nunca ultrapassa o prazo: o que não deu tempo volta como "unknown".
    """
    emails = [e for e in emails if e and "@" in e]
    if not emails:
        return []

    groups: dict = {}
    for e in emails:
        groups.setdefault(e.split("@", 1)[1].lower(), []).append(e)

    per_domain_budget = max(1.0, budget_seconds / len(groups))
    status_by_email: dict = {}
    for domain, group in groups.items():
        for item in _verify_same_domain(domain, group, per_domain_budget, max_workers):
            status_by_email[item["email"]] = item["status"]

    return [{"email": e, "status": status_by_email.get(e, "unknown")} for e in emails]


def _verify_same_domain(domain: str, emails: List[str], budget_seconds: float,
                        max_workers: int) -> List[dict]:
    deadline = time.monotonic() + budget_seconds
    results = {e: "unknown" for e in emails}

    mx = _get_mx_host(domain)
    if not mx:
        return [{"email": e, "status": "invalid"} for e in emails]

    if not smtp_probe_available():
        return [{"email": e, "status": "unknown"} for e in emails]

    if _is_catch_all(domain, mx):
        return [{"email": e, "status": "catch_all"} for e in emails]

    if time.monotonic() >= deadline or not smtp_probe_available():
        return [{"email": e, "status": results[e]} for e in emails]

    def probe(addr: str) -> Tuple[str, str, bool]:
        status, connected = _smtp_rcpt(addr, mx)
        return addr, status, connected

    with ThreadPoolExecutor(max_workers=min(max_workers, len(emails))) as pool:
        futures = {pool.submit(probe, e): e for e in emails}
        try:
            for fut in as_completed(futures, timeout=max(0.1, deadline - time.monotonic())):
                addr, status, connected = fut.result()
                if connected:
                    _note_connect_success()
                    results[addr] = status
                else:
                    _note_connect_failure()
        except Exception:
            # TimeoutError do as_completed: o que sobrou fica "unknown"
            pass

    return [{"email": e, "status": results[e]} for e in emails]


