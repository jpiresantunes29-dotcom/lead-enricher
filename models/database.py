import os
from datetime import datetime, UTC

from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, JSON, Boolean
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
    # Lead scoring (services/lead_scorer.py)
    score = Column(Integer, nullable=True)
    priority = Column(String(10), nullable=True)   # alta | media | baixa
    score_breakdown = Column(JSON, nullable=True)  # [{criterion, points, evidence}]
    score_version = Column(String(10), nullable=True)
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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_new_columns():
    """
    Migração aditiva leve: create_all não altera tabelas existentes, então
    colunas novas em bancos já criados precisam de ALTER TABLE explícito.
    Cobre apenas ADD COLUMN (suficiente até a adoção de Alembic).
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "leads" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("leads")}
    additions = {
        "score": "INTEGER",
        "priority": "VARCHAR(10)",
        "score_breakdown": "JSON" if not _is_sqlite else "TEXT",
        "score_version": "VARCHAR(10)",
        "stage": "VARCHAR(20) DEFAULT 'novo'",
        "ai_summary": "TEXT",
    }
    with engine.begin() as conn:
        for name, ddl in additions.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE leads ADD COLUMN {name} {ddl}"))


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
