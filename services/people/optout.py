"""
LGPD — bloqueio permanente de contatos (art. 18: oposição e eliminação).

Guardamos apenas o SHA-256 do valor normalizado: o pedido de remoção não pode
virar mais uma base de dados pessoais. Para checar, hasheamos de novo e
comparamos — o valor original nunca é persistido.

O bloqueio é verificado em DOIS pontos:
  - antes de revelar qualquer contato (não entrega)
  - antes de gravar (não reentra na base por outra fonte)

Dois caminhos de entrada, com exigências diferentes:
  - `register()`  — bloqueio imediato, para origem já rastreável (usuário
                    autenticado reportando dado errado pela extensão)
  - `create_request()` + `confirm_request()` — formulário público: o pedido
                    fica pendente até o titular abrir o link enviado por
                    e-mail. Só bloqueio CONFIRMADO tem efeito.
"""
import hashlib
import re
import secrets
from datetime import timedelta, UTC
from typing import Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from models.database import OptOut, utcnow

KINDS = ("email", "phone", "linkedin")

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"

# Janela do link de confirmação. Curta o bastante para um link vazado não
# valer nada amanhã, longa o bastante para caber num "vi o e-mail à noite".
TOKEN_TTL_HOURS = 24


def normalize(kind: str, value: str) -> Optional[str]:
    """Forma canônica antes do hash — sem isso o bloqueio vaza por formatação."""
    if not value:
        return None
    value = value.strip().lower()
    if kind == "email":
        return value if "@" in value else None
    if kind == "phone":
        digits = re.sub(r"\D", "", value)
        return f"+{digits}" if digits else None
    if kind == "linkedin":
        m = re.search(r"linkedin\.com/in/([a-z0-9\-._%]+)", value)
        slug = m.group(1) if m else value.split("/")[-1]
        return slug.strip() or None
    return None


def hash_value(kind: str, value: str) -> Optional[str]:
    canonical = normalize(kind, value)
    if not canonical:
        return None
    return hashlib.sha256(f"{kind}:{canonical}".encode("utf-8")).hexdigest()


def register(db: Session, kind: str, value: str, reason: Optional[str] = None,
             source: str = "interno") -> bool:
    """
    Registra um bloqueio JÁ CONFIRMADO. Idempotente. Não commita.

    Para o formulário público use `create_request()`: lá a titularidade ainda
    precisa ser provada.
    """
    if kind not in KINDS:
        return False
    digest = hash_value(kind, value)
    if not digest:
        return False
    row = db.query(OptOut).filter(OptOut.value_hash == digest).first()
    if row:
        # Pedido pendente que chega por caminho confiável vira confirmado.
        if row.status != STATUS_CONFIRMED:
            row.status = STATUS_CONFIRMED
            row.confirmed_at = utcnow()
            row.token_hash = None
        return True
    db.add(OptOut(
        kind=kind,
        value_hash=digest,
        reason=(reason or "")[:500],
        status=STATUS_CONFIRMED,
        confirmed_at=utcnow(),
        source=source,
    ))
    return True


def create_request(db: Session, kind: str, value: str, contact_email: str,
                   reason: Optional[str] = None,
                   source: str = "form_publico") -> Optional[str]:
    """
    Abre um pedido de remoção pendente e devolve o token do link de
    confirmação (em claro — só aqui; no banco fica o hash).

    Enquanto não for confirmado, o pedido NÃO bloqueia nem apaga nada: sem
    isso, qualquer um esvaziaria a base alheia mandando formulário.
    """
    if kind not in KINDS:
        return None
    digest = hash_value(kind, value)
    if not digest:
        return None

    token = secrets.token_urlsafe(32)
    row = db.query(OptOut).filter(OptOut.value_hash == digest).first()
    if row and row.status == STATUS_CONFIRMED:
        return None  # já bloqueado: nada a confirmar

    if not row:
        row = OptOut(kind=kind, value_hash=digest)
        db.add(row)

    row.status = STATUS_PENDING
    row.reason = (reason or "")[:500]
    row.source = source
    row.token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row.token_expires_at = utcnow() + timedelta(hours=TOKEN_TTL_HOURS)
    row.requested_by_hash = hash_value("email", contact_email) if contact_email else None
    # Guardado só até a confirmação: a purga apaga registros que casam pelo
    # valor, e o hash é via de mão única.
    row.pending_value = (normalize(kind, value) or "")[:320]
    return token


def confirm_request(db: Session, token: str) -> Optional[Tuple[str, int]]:
    """
    Confirma um pedido pelo token do link e executa a remoção.

    Devolve (kind, registros_removidos) ou None quando o token é inválido, já
    usado ou expirado. Não commita.
    """
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = db.query(OptOut).filter(OptOut.token_hash == digest).first()
    if not row:
        return None

    expires = row.token_expires_at
    if expires is not None:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < utcnow():
            return None

    value = row.pending_value or ""
    row.status = STATUS_CONFIRMED
    row.confirmed_at = utcnow()
    row.token_hash = None          # link de uso único
    row.token_expires_at = None

    removed = purge(db, row.kind, value) if value else 0
    # A partir daqui só o hash permanece — o dado em claro cumpriu seu papel.
    row.pending_value = None
    return row.kind, removed


def is_blocked(db: Session, kind: str, value: str) -> bool:
    digest = hash_value(kind, value)
    if not digest:
        return False
    return (
        db.query(OptOut.id)
        .filter(OptOut.value_hash == digest, OptOut.status == STATUS_CONFIRMED)
        .first()
        is not None
    )


def blocked_hashes(db: Session, pairs: Iterable[tuple]) -> set:
    """
    Consulta em lote. `pairs` = [(kind, value), ...] → set dos hashes bloqueados.
    Uma query só, para não multiplicar round-trips durante um reveal.
    """
    digests = {}
    for kind, value in pairs:
        digest = hash_value(kind, value)
        if digest:
            digests[digest] = (kind, value)
    if not digests:
        return set()
    rows = db.query(OptOut.value_hash).filter(OptOut.value_hash.in_(list(digests))).all()
    return {r[0] for r in rows}


def purge(db: Session, kind: str, value: str) -> int:
    """
    Apaga fisicamente o contato bloqueado das tabelas de pessoas.
    O bloqueio por hash impede a reentrada por outra fonte. Não commita.
    Devolve quantos registros foram removidos.
    """
    from models.database import PersonEmail, PersonPhone, Person

    canonical = normalize(kind, value)
    if not canonical:
        return 0

    removed = 0
    if kind == "email":
        removed = db.query(PersonEmail).filter(
            PersonEmail.email == canonical
        ).delete(synchronize_session=False)
    elif kind == "phone":
        removed = db.query(PersonPhone).filter(
            PersonPhone.e164 == canonical
        ).delete(synchronize_session=False)
    elif kind == "linkedin":
        person = db.query(Person).filter(Person.linkedin_slug == canonical).first()
        if person:
            removed += db.query(PersonEmail).filter(
                PersonEmail.person_id == person.id
            ).delete(synchronize_session=False)
            removed += db.query(PersonPhone).filter(
                PersonPhone.person_id == person.id
            ).delete(synchronize_session=False)
    return removed


def filter_contacts(db: Session, emails: List[str], phones: List[str],
                    linkedin: Optional[str] = None) -> dict:
    """
    Aplica o bloqueio a um conjunto de contatos.
    Retorna {emails, phones, person_blocked} — person_blocked quando a própria
    pessoa (perfil do LinkedIn) pediu remoção: nada dela pode ser entregue.
    """
    pairs = [("email", e) for e in emails] + [("phone", p) for p in phones]
    if linkedin:
        pairs.append(("linkedin", linkedin))
    blocked = blocked_hashes(db, pairs)

    if linkedin and hash_value("linkedin", linkedin) in blocked:
        return {"emails": [], "phones": [], "person_blocked": True}

    return {
        "emails": [e for e in emails if hash_value("email", e) not in blocked],
        "phones": [p for p in phones if hash_value("phone", p) not in blocked],
        "person_blocked": False,
    }
