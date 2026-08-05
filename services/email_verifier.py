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


def verify_emails(emails: list, max_checks: int = 3) -> list:
    """
    Compatibilidade com o fluxo antigo (decision_finder).
    Provedores premium têm prioridade; a sondagem SMTP gratuita é o fallback.
    """
    from .providers import premium_verify_email

    emails = list(emails or [])
    to_check = emails[:max_checks]
    rest = emails[max_checks:]

    premium: dict = {}
    remaining: List[str] = []
    for e in to_check:
        status = premium_verify_email(e)
        if status:
            premium[e] = status
        else:
            remaining.append(e)

    batch = {r["email"]: r["status"] for r in verify_batch(remaining)} if remaining else {}

    out = [{"email": e, "status": premium.get(e) or batch.get(e, "unknown")} for e in to_check]
    out.extend({"email": e, "status": "unknown"} for e in rest)
    return out
