"""
Persistência do banco de contatos: Company, Person, e-mails e telefones.

Regras de gravação (o que garante precisão ao longo do tempo):
  - nunca rebaixar um dado: 'valid' não vira 'unknown' porque a rede falhou
  - confiança só sobe quando há evidência melhor
  - toda linha carrega origem (source) e data de verificação (verified_at),
    que é o que permite responder "de onde veio esse dado?" (LGPD)
"""
import logging
from datetime import datetime, UTC, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from models.database import Company, Person, PersonEmail, PersonPhone
from .identity import (
    clean_name, dedupe_key, display_name, linkedin_slug, split_name,
    title_department, title_seniority, SENIORITY_RANK,
)

logger = logging.getLogger(__name__)

# Contato verificado há mais de 90 dias vira candidato a revalidação
FRESHNESS_DAYS = 90

_STATUS_RANK = {"valid": 3, "catch_all": 2, "unknown": 1, "invalid": 0}


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite devolve datetime sem tzinfo; normaliza para comparar com now()."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def is_fresh(value: Optional[datetime], days: int = FRESHNESS_DAYS) -> bool:
    value = _aware(value)
    return bool(value and value >= _now() - timedelta(days=days))


# ── Company ──────────────────────────────────────────────────────────────────

def get_company(db: Session, domain: str) -> Optional[Company]:
    if not domain:
        return None
    return db.query(Company).filter(Company.domain == domain.lower()).first()


def upsert_company(db: Session, domain: str, **fields) -> Optional[Company]:
    """Cria/atualiza a empresa global. Só preenche campo vazio ou explicitamente novo."""
    if not domain:
        return None
    domain = domain.lower()
    company = get_company(db, domain)
    if not company:
        company = Company(domain=domain)
        db.add(company)
        db.flush()

    for key, value in fields.items():
        if value in (None, "", [], {}) or not hasattr(company, key):
            continue
        current = getattr(company, key)
        if current in (None, "", [], {}):
            setattr(company, key, value)
        elif key in ("employee_count", "cnpj_data", "phones", "emails", "name", "sector", "location"):
            # Campos que valem atualizar quando chega versão mais completa
            setattr(company, key, value)
    return company


# ── Person ───────────────────────────────────────────────────────────────────

def find_person(db: Session, slug: Optional[str] = None, key: Optional[str] = None) -> Optional[Person]:
    if slug:
        person = db.query(Person).filter(Person.linkedin_slug == slug).first()
        if person:
            return person
    if key:
        return db.query(Person).filter(Person.dedupe_key == key).first()
    return None


def upsert_person(
    db: Session,
    full_name: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    slug: Optional[str] = None,
    title: Optional[str] = None,
    headline: Optional[str] = None,
    company_domain: Optional[str] = None,
    company_name: Optional[str] = None,
    company: Optional[Company] = None,
    location: Optional[str] = None,
    photo_url: Optional[str] = None,
    source: str = "manual",
) -> Optional[Person]:
    """
    Cria ou atualiza a pessoa. Devolve None se não há identidade suficiente
    (sem slug do LinkedIn e sem nome+domínio não dá para deduplicar).
    """
    slug = slug or linkedin_slug(linkedin_url or "")
    name = display_name(full_name or "") or (clean_name(full_name or "") or None)
    domain = (company_domain or "").lower() or None
    key = dedupe_key(slug, name, domain)
    if not key:
        return None

    person = find_person(db, slug=slug, key=key)
    if not person:
        person = Person(dedupe_key=key)
        db.add(person)
        db.flush()

    first, last = split_name(name or "")
    updates = {
        "linkedin_slug": slug,
        "full_name": name,
        "first_name": first,
        "last_name": last,
        "headline": headline,
        "title": title,
        "company_name_raw": company_name,
        "company_domain": domain,
        "location": location,
        "photo_url": photo_url,
    }
    for field, value in updates.items():
        if value:
            setattr(person, field, value)

    effective_title = person.title or person.headline
    if effective_title:
        person.seniority = title_seniority(effective_title)
        person.department = title_department(effective_title)
    if company is not None:
        person.company_id = company.id
        if not person.company_domain:
            person.company_domain = company.domain
    if source and not person.source:
        person.source = source
    person.last_seen_at = _now()
    return person


def decision_rank(person: Person) -> int:
    return SENIORITY_RANK.get(person.seniority or "other", 9)


# ── Contatos ─────────────────────────────────────────────────────────────────

def add_email(db: Session, person: Person, email: str, status: str = "unknown",
              confidence: int = 0, source: str = "pattern",
              pattern: Optional[str] = None, type_: str = "work") -> Optional[PersonEmail]:
    """Grava/atualiza um e-mail da pessoa sem rebaixar informação melhor."""
    from . import optout

    if not person or not email or "@" not in email:
        return None
    email = email.strip().lower()
    # Trava de LGPD na gravação: sem ela, o palpite do padrão regravaria um
    # contato que a pessoa pediu para remover — prometemos remoção definitiva.
    if optout.is_blocked(db, "email", email):
        return None

    row = next((e for e in person.emails if e.email == email), None)
    if not row:
        row = PersonEmail(person_id=person.id, email=email, type=type_)
        db.add(row)
        person.emails.append(row)

    if _STATUS_RANK.get(status, 1) >= _STATUS_RANK.get(row.status or "unknown", 1):
        row.status = status
        if status in ("valid", "invalid", "catch_all"):
            row.verified_at = _now()
    row.confidence = max(row.confidence or 0, int(confidence))
    if source and (not row.source or confidence >= (row.confidence or 0)):
        row.source = source
    if pattern:
        row.pattern = pattern
    return row


def add_phone(db: Session, person: Person, e164: str, formatted: Optional[str] = None,
              type_: str = "unknown", confidence: int = 0,
              source: str = "site") -> Optional[PersonPhone]:
    from . import optout

    if not person or not e164:
        return None
    if optout.is_blocked(db, "phone", e164):
        return None
    row = next((p for p in person.phones if p.e164 == e164), None)
    if not row:
        row = PersonPhone(person_id=person.id, e164=e164)
        db.add(row)
        person.phones.append(row)
    row.formatted = formatted or row.formatted or e164
    if type_ and type_ != "unknown":
        row.type = type_
    elif not row.type:
        row.type = type_
    row.confidence = max(row.confidence or 0, int(confidence))
    if source:
        row.source = source
    row.verified_at = row.verified_at or _now()
    return row


def sorted_emails(person: Person) -> List[PersonEmail]:
    """Melhor primeiro: status confirmado pesa mais que confiança do palpite."""
    return sorted(
        [e for e in person.emails if e.status != "invalid"],
        key=lambda e: (-_STATUS_RANK.get(e.status or "unknown", 1), -(e.confidence or 0)),
    )


def sorted_phones(person: Person) -> List[PersonPhone]:
    type_rank = {"mobile": 0, "fixed_or_mobile": 1, "fixed_line": 2, "company": 3, "unknown": 4}
    return sorted(
        person.phones,
        key=lambda p: (-(p.confidence or 0), type_rank.get(p.type or "unknown", 4)),
    )


def best_email(person: Person) -> Optional[PersonEmail]:
    items = sorted_emails(person)
    return items[0] if items else None


def best_phone(person: Person) -> Optional[PersonPhone]:
    items = sorted_phones(person)
    return items[0] if items else None


def has_fresh_contacts(person: Person) -> bool:
    """Já temos contato bom e recente? Então o reveal sai do cache, de graça."""
    email = best_email(person)
    if email and (email.confidence or 0) >= 70 and is_fresh(email.verified_at or email.created_at):
        return True
    phone = best_phone(person)
    return bool(phone and (phone.confidence or 0) >= 70 and is_fresh(phone.verified_at or phone.created_at))
