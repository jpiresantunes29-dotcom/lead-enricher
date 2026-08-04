"""
LGPD — bloqueio permanente de contatos (art. 18: oposição e eliminação).

Guardamos apenas o SHA-256 do valor normalizado: o pedido de remoção não pode
virar mais uma base de dados pessoais. Para checar, hasheamos de novo e
comparamos — o valor original nunca é persistido.

O bloqueio é verificado em DOIS pontos:
  - antes de revelar qualquer contato (não entrega)
  - antes de gravar (não reentra na base por outra fonte)
"""
import hashlib
import re
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from models.database import OptOut

KINDS = ("email", "phone", "linkedin")


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


def register(db: Session, kind: str, value: str, reason: Optional[str] = None) -> bool:
    """Registra um bloqueio. Idempotente. Não commita."""
    if kind not in KINDS:
        return False
    digest = hash_value(kind, value)
    if not digest:
        return False
    exists = db.query(OptOut).filter(OptOut.value_hash == digest).first()
    if exists:
        return True
    db.add(OptOut(kind=kind, value_hash=digest, reason=(reason or "")[:500]))
    return True


def is_blocked(db: Session, kind: str, value: str) -> bool:
    digest = hash_value(kind, value)
    if not digest:
        return False
    return db.query(OptOut.id).filter(OptOut.value_hash == digest).first() is not None


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
