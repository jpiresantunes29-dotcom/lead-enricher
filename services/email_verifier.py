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

ATENÇÃO: muitos servidores corporativos bloqueiam SMTP probing.
Use timeout curto e cache por domínio para não fazer muitas conexões.
"""
import socket
import smtplib
import string
import random
import dns.resolver
import dns.exception
from functools import lru_cache
from typing import Optional


SMTP_TIMEOUT = 6
SMTP_FROM = "verify@example.com"


@lru_cache(maxsize=256)
def _get_mx_host(domain: str) -> Optional[str]:
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        records = sorted(answers, key=lambda r: r.preference)
        return str(records[0].exchange).rstrip(".") if records else None
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
            dns.resolver.NoNameservers, dns.exception.Timeout):
        return None
    except Exception:
        return None


@lru_cache(maxsize=128)
def _is_catch_all(domain: str, mx_host: str) -> bool:
    """Testa se o domínio aceita um email aleatório → indica catch-all."""
    fake_local = "".join(random.choices(string.ascii_lowercase, k=16))
    fake_email = f"nonexistent_{fake_local}@{domain}"
    status = _smtp_rcpt(fake_email, mx_host)
    return status == "valid"


def _smtp_rcpt(email: str, mx_host: str) -> str:
    """Faz uma sondagem SMTP RCPT TO. Retorna valid|invalid|unknown."""
    try:
        with smtplib.SMTP(mx_host, 25, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo_or_helo_if_needed()
            try:
                code, _ = smtp.mail(SMTP_FROM)
                if code >= 400:
                    return "unknown"
                code, _ = smtp.rcpt(email)
            except smtplib.SMTPException:
                return "unknown"
            if code == 250 or code == 251:
                return "valid"
            if code in (550, 551, 553, 554):
                return "invalid"
            if 400 <= code < 500:
                return "unknown"  # greylisting / temporário
            return "unknown"
    except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError, smtplib.SMTPException):
        return "unknown"
    except Exception:
        return "unknown"


def verify_email(email: str) -> str:
    """
    Verifica um email. Retorna: valid | catch_all | invalid | unknown.
    """
    if not email or "@" not in email:
        return "invalid"
    domain = email.split("@", 1)[1].lower()
    mx = _get_mx_host(domain)
    if not mx:
        return "invalid"

    status = _smtp_rcpt(email, mx)
    if status == "valid":
        # Confirmar que não é catch-all
        if _is_catch_all(domain, mx):
            return "catch_all"
        return "valid"
    return status


def verify_emails(emails: list, max_checks: int = 3) -> list:
    """
    Verifica até max_checks emails. Devolve lista de dicts {email, status}.
    Provedores premium (Hunter etc., se configurados) têm prioridade;
    a sondagem SMTP gratuita é o fallback.
    """
    from .providers import premium_verify_email

    out = []
    for i, e in enumerate(emails):
        if i >= max_checks:
            out.append({"email": e, "status": "unknown"})
            continue
        status = premium_verify_email(e) or verify_email(e)
        out.append({"email": e, "status": status})
    return out
