import os
from datetime import datetime, UTC

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Text, ForeignKey, JSON,
    Boolean, Float, UniqueConstraint, Index,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lead_enricher.db")
if DATABASE_URL.startswith("postgres://"):
    # Supabase/Heroku entregam a connection string com o esquema legado
    # "postgres://", que o SQLAlchemy 2.0 não reconhece mais (exige "postgresql://").
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(String(36), primary_key=True)  # UUID from Supabase Auth
    plan = Column(String(20), nullable=False, default="free")
    searches_used = Column(Integer, nullable=False, default=0)
    searches_limit = Column(Integer, nullable=False, default=5)
    # Créditos de revelação de contato (extensão) — medidor independente das buscas
    reveals_used = Column(Integer, nullable=False, default=0)
    reveals_limit = Column(Integer, nullable=False, default=5)
    stripe_customer_id = Column(String(255), nullable=True)
    quota_reset_at = Column(DateTime, nullable=True)  # próximo reset mensal
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class StripeEvent(Base):
    __tablename__ = "stripe_events"

    id = Column(String(255), primary_key=True)  # Stripe event ID — garante idempotência
    processed_at = Column(DateTime, default=lambda: datetime.now(UTC))


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(36), nullable=True, index=True)  # UUID from Supabase Auth
    raw_input_domain = Column(String(500), nullable=False)
    domain = Column(String(255), index=True)
    company_name = Column(String(255))
    website = Column(String(500))
    linkedin_url = Column(String(500))
    linkedin_confidence = Column(String(20))  # verified | probable | unverified
    mx_provider = Column(String(100))
    mx_provider_confidence = Column(String(20))  # high | medium | low
    mx_records = Column(JSON)
    dns_report = Column(JSON)  # full DNS Dumpster style report
    hosting_provider = Column(String(100))
    employee_count = Column(JSON)  # {raw, min, max, band, exact, source}
    employee_count_linkedin = Column(Integer, nullable=True)  # contagem exata da aba People
    sector = Column(String(255))
    location = Column(String(255))
    description = Column(Text)
    corporate_email = Column(String(255))
    phone = Column(String(100))
    status = Column(String(50), default="enriched")
    # Versão da lógica de coleta que gerou a ficha (services.enricher).
    # Ficha de versão antiga não é servida do cache — ver routers/enrichment.py.
    enrichment_version = Column(Integer, nullable=True)
    # Pipeline comercial
    stage = Column(String(20), nullable=False, default="novo")
    # Fase 5 — resumo executivo gerado por IA (cacheado)
    ai_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    decision_makers = relationship("DecisionMaker", back_populates="lead", cascade="all, delete-orphan")
    activities = relationship("Activity", back_populates="lead", cascade="all, delete-orphan")


class DecisionMaker(Base):
    __tablename__ = "decision_makers"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    name = Column(String(255))
    title_searched = Column(String(255))
    title_found = Column(String(500))
    snippet = Column(Text)
    linkedin_url = Column(String(500))
    probable_emails = Column(JSON)  # lista de {email, status}
    match_confidence = Column(String(20))  # high | medium | low
    phone = Column(String(100))
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    lead = relationship("Lead", back_populates="decision_makers")


class Activity(Base):
    """
    Camada de execução comercial: ligações, notas, tarefas de follow-up e
    reuniões. Substitui as Phone Call Activities / Tasks do plano Dynamics.
    """
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    type = Column(String(20), nullable=False)   # call | email | meeting | note | task
    outcome = Column(String(30), nullable=True)  # no_answer | busy | voicemail | talked | meeting_scheduled
    notes = Column(Text, nullable=True)
    due_at = Column(DateTime, nullable=True)       # follow-ups e reuniões
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    lead = relationship("Lead", back_populates="activities")


# ─────────────────────────────────────────────────────────────────────────────
# Contact Intelligence — entidades globais (compartilhadas entre usuários)
#
# Lead continua sendo "a empresa dentro da pipeline de UM usuário".
# Company/Person são o banco de dados de contatos do produto: cada revelação
# alimenta o estoque próprio e a próxima consulta sai do cache, de graça.
# ─────────────────────────────────────────────────────────────────────────────

class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String(255), unique=True, index=True, nullable=False)
    linkedin_slug = Column(String(255), index=True, nullable=True)
    name = Column(String(255))
    cnpj = Column(String(14), index=True, nullable=True)
    sector = Column(String(255))
    location = Column(String(255))
    employee_count = Column(JSON)
    phone = Column(String(100))          # telefone comercial consolidado
    phones = Column(JSON)                # [{e164, formatted, type, source, confidence}]
    main_email = Column(String(255))
    emails = Column(JSON)                # e-mails vistos no domínio (alimenta padrão)
    cnpj_data = Column(JSON)             # recorte público da Receita (razão social, QSA...)
    enriched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    persons = relationship("Person", back_populates="company")


class Person(Base):
    __tablename__ = "persons"

    id = Column(Integer, primary_key=True, index=True)
    # Chave de deduplicação: slug do LinkedIn quando existe; senão hash de
    # nome normalizado + domínio da empresa.
    dedupe_key = Column(String(120), unique=True, index=True, nullable=False)
    linkedin_slug = Column(String(255), index=True, nullable=True)
    full_name = Column(String(255))
    first_name = Column(String(120))
    last_name = Column(String(120))
    headline = Column(String(500))
    title = Column(String(255))
    seniority = Column(String(30))       # founder|c_level|vp|director|head|manager|other
    department = Column(String(30))      # tech|sales|marketing|finance|hr|ops|legal|other
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    company_name_raw = Column(String(255))
    company_domain = Column(String(255), index=True, nullable=True)
    location = Column(String(255))
    photo_url = Column(String(1000))
    source = Column(String(40))          # linkedin_dom|search|cnpj_qsa|site|manual
    last_seen_at = Column(DateTime, default=lambda: datetime.now(UTC))
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    company = relationship("Company", back_populates="persons")
    emails = relationship("PersonEmail", back_populates="person", cascade="all, delete-orphan")
    phones = relationship("PersonPhone", back_populates="person", cascade="all, delete-orphan")


class PersonEmail(Base):
    __tablename__ = "person_emails"
    __table_args__ = (UniqueConstraint("person_id", "email", name="uq_person_email"),)

    id = Column(Integer, primary_key=True, index=True)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=False, index=True)
    email = Column(String(320), nullable=False)
    type = Column(String(20), default="work")     # work | generic
    status = Column(String(20), default="unknown")  # valid|catch_all|invalid|unknown
    confidence = Column(Integer, default=0)         # 0-100
    source = Column(String(40))                     # pattern|site|smtp|hunter|cnpj
    pattern = Column(String(50), nullable=True)     # padrão que gerou o palpite
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    person = relationship("Person", back_populates="emails")


class PersonPhone(Base):
    __tablename__ = "person_phones"
    __table_args__ = (UniqueConstraint("person_id", "e164", name="uq_person_phone"),)

    id = Column(Integer, primary_key=True, index=True)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=False, index=True)
    e164 = Column(String(20), nullable=False)
    formatted = Column(String(40))
    type = Column(String(20))            # mobile|fixed_line|company|unknown
    confidence = Column(Integer, default=0)
    source = Column(String(40))          # site|cnpj|places|apollo...
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    person = relationship("Person", back_populates="phones")


class EmailPattern(Base):
    """
    Padrão de e-mail aprendido por domínio — a peça de maior ROI do produto.
    Um e-mail confirmado ensina o formato da empresa inteira.
    """
    __tablename__ = "email_patterns"

    domain = Column(String(255), primary_key=True)
    pattern = Column(String(50), nullable=True)   # ex.: "{first}.{last}"
    confidence = Column(Integer, default=0)
    samples_count = Column(Integer, default=0)
    votes = Column(JSON)                          # {"{first}.{last}": 3, "{f}{last}": 1}
    evidence = Column(JSON)                       # [{email, name, source}] (máx. 5)
    catch_all = Column(Boolean, nullable=True)    # domínio aceita qualquer destinatário
    mx_ok = Column(Boolean, nullable=True)
    last_confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class Reveal(Base):
    """Ledger de créditos: quem revelou quem, quando e por qual caminho."""
    __tablename__ = "reveals"
    __table_args__ = (Index("ix_reveals_user_person", "user_id", "person_id"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=False, index=True)
    kind = Column(String(20), default="both")     # email|phone|both
    credits_charged = Column(Integer, default=0)
    found_email = Column(Boolean, default=False)
    found_phone = Column(Boolean, default=False)
    provider_chain = Column(JSON)                 # ["cache", "pattern+smtp", "cnpj"]
    cost_usd = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class ProviderCall(Base):
    """Auditoria de custo/latência por provedor externo."""
    __tablename__ = "provider_calls"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(40), nullable=False, index=True)
    operation = Column(String(40))
    hit = Column(Boolean, default=False)
    cost_usd = Column(Float, default=0.0)
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class OptOut(Base):
    """
    LGPD: bloqueio permanente. Guardamos apenas o HASH do valor — o pedido de
    remoção não pode virar mais uma base de dados pessoais.
    """
    __tablename__ = "opt_outs"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(20), nullable=False)      # email|phone|linkedin
    value_hash = Column(String(64), nullable=False, unique=True, index=True)
    reason = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class ExtensionToken(Base):
    """Pareamento navegador ↔ conta, via código curto exibido no /app."""
    __tablename__ = "extension_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    code = Column(String(16), nullable=True, index=True)      # código de pareamento
    code_expires_at = Column(DateTime, nullable=True)
    token_hash = Column(String(64), nullable=True, index=True)
    device_label = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_JSON_DDL = "TEXT" if _is_sqlite else "JSON"

# Colunas adicionadas depois que a tabela já existia em produção.
_ADDITIVE_COLUMNS = {
    "leads": {
        "stage": "VARCHAR(20) DEFAULT 'novo'",
        "ai_summary": "TEXT",
        "enrichment_version": "INTEGER",
    },
    "profiles": {
        "reveals_used": "INTEGER DEFAULT 0",
        "reveals_limit": "INTEGER DEFAULT 5",
    },
}


def _ensure_new_columns():
    """
    Migração aditiva leve: create_all não altera tabelas existentes, então
    colunas novas em bancos já criados precisam de ALTER TABLE explícito.
    Cobre apenas ADD COLUMN (suficiente até a adoção de Alembic).
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, additions in _ADDITIVE_COLUMNS.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in additions.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


class CRMConnection(Base):
    """Configuração de integração CRM por usuário."""
    __tablename__ = "crm_connections"

    user_id = Column(String, primary_key=True)
    provider = Column(String, primary_key=True)  # webhook, dynamics, hubspot, pipedrive
    webhook_url = Column(String, nullable=True)
    webhook_secret = Column(String, nullable=True)
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    account_id = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


def init_db():
    Base.metadata.create_all(bind=engine)
    _ensure_new_columns()
