import logging
import os
from datetime import datetime, UTC

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Text, ForeignKey, JSON,
    Boolean, Float, UniqueConstraint, Index,
)
from sqlalchemy.orm import declarative_base
# Importado com outro nome porque `Lead.relationship` é uma coluna: dentro do
# corpo da classe, o nome `relationship` passaria a ser a Column e as ligações
# declaradas depois quebrariam com "'Column' object is not callable".
from sqlalchemy.orm import sessionmaker, relationship as orm_relationship

from services.crypto import SegredoCriptografado

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

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """
    Agora, em UTC e com fuso explícito.

    Todo timestamp do sistema nasce daqui e toda coluna de data é
    `DateTime(timezone=True)`. Misturar datetime com e sem fuso é o tipo de
    erro que só aparece em produção: no Postgres a data naive é interpretada
    no fuso da sessão e o follow-up cai na hora errada; no SQLite as duas
    formas viram strings de formatos diferentes e a comparação passa a
    ordenar texto, não tempo.
    """
    return datetime.now(UTC)


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
    quota_reset_at = Column(DateTime(timezone=True), nullable=True)  # próximo reset mensal
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StripeEvent(Base):
    __tablename__ = "stripe_events"

    id = Column(String(255), primary_key=True)  # Stripe event ID — garante idempotência
    processed_at = Column(DateTime(timezone=True), default=utcnow)


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
    # Que relação esta empresa tem com o usuário — ver RELATIONSHIPS.
    #
    # Separado de `stage` de propósito: `stage` é onde a negociação está e
    # muda o tempo todo; `relationship` é quem a empresa é, e é a trava
    # estrutural que impede a automação de abordar quem já é cliente ou pediu
    # para não ser contatado. Regra crítica não pode depender de um campo que
    # o vendedor arrasta num kanban.
    relationship = Column(String(20), nullable=False, default="LEAD")
    # Versão da lógica de coleta que gerou a ficha (services.enricher).
    # Ficha de versão antiga não é servida do cache — ver routers/enrichment.py.
    enrichment_version = Column(Integer, nullable=True)
    # Pipeline comercial
    stage = Column(String(20), nullable=False, default="novo")
    # Fase 5 — resumo executivo gerado por IA (cacheado)
    ai_summary = Column(Text, nullable=True)
    # Planilha importada: as células como vieram do arquivo, a linha de origem
    # e o lote. É o que permite reproduzir a planilha do usuário célula a
    # célula, com os rótulos e a ordem que ele já conhece.
    cells = Column(JSON, nullable=True)
    sheet_row = Column(Integer, nullable=True)
    import_batch_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    decision_makers = orm_relationship("DecisionMaker", back_populates="lead", cascade="all, delete-orphan")
    activities = orm_relationship("Activity", back_populates="lead", cascade="all, delete-orphan")
    conversations = orm_relationship("Conversation", back_populates="lead", cascade="all, delete-orphan")


#: Valores de `Lead.relationship`. Só `LEAD` autoriza automação de contato;
#: todo o resto é motivo de recusa no portão (services/wa/gate.py).
RELATIONSHIP_LEAD = "LEAD"                      # prospecto: pode receber
RELATIONSHIP_CUSTOMER = "CUSTOMER"              # cliente atual: nunca receber
RELATIONSHIP_DO_NOT_CONTACT = "DO_NOT_CONTACT"  # pediu para não receber
RELATIONSHIP_BLOCKED = "BLOCKED"                # erro ou suspeita de segurança
RELATIONSHIPS = (
    RELATIONSHIP_LEAD, RELATIONSHIP_CUSTOMER,
    RELATIONSHIP_DO_NOT_CONTACT, RELATIONSHIP_BLOCKED,
)


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
    created_at = Column(DateTime(timezone=True), default=utcnow)

    lead = orm_relationship("Lead", back_populates="decision_makers")


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
    due_at = Column(DateTime(timezone=True), nullable=True)       # follow-ups e reuniões
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    lead = orm_relationship("Lead", back_populates="activities")


# ─────────────────────────────────────────────────────────────────────────────
# Contato por WhatsApp
#
# Dois eixos separados, e a separação é a proteção:
#   Lead.relationship        — quem a empresa é (estrutural, muda raramente)
#   Conversation.ai_status   — se a automação pode falar agora (operacional)
#
# Juntar os dois num campo só faria "pausar a IA nesta conversa" e "esta
# empresa virou cliente" serem a mesma escrita, e um retomar desfaria o outro.
# ─────────────────────────────────────────────────────────────────────────────

#: Valores de `Conversation.ai_status`. Só `AI_ACTIVE` autoriza envio.
AI_ACTIVE = "AI_ACTIVE"          # automação respondendo
AI_PAUSED = "AI_PAUSED"          # o usuário pausou à mão
HUMAN_HANDOFF = "HUMAN_HANDOFF"  # o usuário assumiu, ou um gatilho transferiu
STOPPED = "STOPPED"              # encerrada
AI_STATUSES = (AI_ACTIVE, AI_PAUSED, HUMAN_HANDOFF, STOPPED)


class Conversation(Base):
    """Uma conversa de WhatsApp com um lead."""
    __tablename__ = "conversations"
    __table_args__ = (
        # A tela de conversas lista sempre "as minhas, mais recentes primeiro".
        Index("ix_conversations_user_updated", "user_id", "updated_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    decision_maker_id = Column(Integer, ForeignKey("decision_makers.id"), nullable=True)
    user_id = Column(String(36), nullable=False, index=True)
    phone_e164 = Column(String(20), nullable=False, index=True)
    channel = Column(String(20), nullable=False, default="whatsapp")
    ai_status = Column(String(20), nullable=False, default=AI_ACTIVE)
    # Fim da janela de 24 h aberta pela última mensagem do lead. Fora dela, a
    # Meta só aceita template pago — e template não é conversa.
    window_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_inbound_at = Column(DateTime(timezone=True), nullable=True)
    last_outbound_at = Column(DateTime(timezone=True), nullable=True)
    # Prévia para a lista de conversas não precisar carregar as mensagens.
    last_message_body = Column(Text, nullable=True)
    # Por que saiu do automático. É o que a tela mostra para o usuário saber o
    # que aconteceu sem abrir a conversa.
    handoff_reason = Column(String(500), nullable=True)
    after_hours = Column(Boolean, nullable=False, default=False)
    # Quantas respostas automáticas já saíram na faixa de fora-do-expediente.
    # Zera quando um turno acontece dentro do horário comercial: o teto é por
    # noite/fim de semana, não para a vida da conversa.
    after_hours_turns = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    lead = orm_relationship("Lead", back_populates="conversations")
    messages = orm_relationship(
        "WaMessage", back_populates="conversation",
        cascade="all, delete-orphan", order_by="WaMessage.id",
    )


class WaMessage(Base):
    """Cada mensagem trocada, na ordem em que aconteceu."""
    __tablename__ = "wa_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"),
                             nullable=False, index=True)
    direction = Column(String(10), nullable=False)   # in | out
    # ID da Meta. Único porque o webhook reentrega a mesma mensagem quando não
    # recebe 200 a tempo: sem esta restrição, uma lentidão nossa vira mensagem
    # duplicada na conversa e uma resposta repetida para o lead.
    wa_message_id = Column(String(128), nullable=True, unique=True, index=True)
    type = Column(String(20), nullable=False, default="text")  # text|template|image|button
    body = Column(Text, nullable=True)
    template_name = Column(String(120), nullable=True)
    status = Column(String(20), nullable=True)       # sent|delivered|read|failed
    sent_by = Column(String(10), nullable=True)      # ai | human
    intent_detected = Column(String(40), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    conversation = orm_relationship("Conversation", back_populates="messages")


class AuditLog(Base):
    """
    Trilha de decisões: quem mudou o quê, quando e por quê.

    **Não guarda mensagens.** O conteúdo trocado já vive em `wa_messages`, com
    direção, autor e horário; copiar isso aqui só criaria uma segunda cópia de
    dado pessoal para manter em dia. O que falta em qualquer outro lugar são as
    *decisões*: quem pausou, quem assumiu, quando a automação levantou a mão e
    quando uma empresa deixou de ser lead.

    A pergunta que esta tabela existe para responder é "por que essa conversa
    continuou depois de eu ter pausado?" — e `Conversation.handoff_reason`
    guarda só o último motivo, o que não conta história nenhuma.
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"),
                             nullable=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    acao = Column(String(40), nullable=False)
    # Quem fez: "humano" (o dono da conta), "ia" (a automação) ou "sistema"
    # (o portão, um cron). Sem isto, "conversa encerrada" não distingue um
    # clique de um gatilho automático — que é justamente o que se quer saber.
    ator = Column(String(20), nullable=False, default="sistema")
    detalhe = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)


#: Vocabulário de `AuditLog.acao`. Curto de propósito: um vocabulário que
#: cresce a cada funcionalidade vira log que ninguém consegue ler.
AUDIT_CONVERSA_ABERTA = "conversa_aberta"      # template enviado (ação paga)
AUDIT_PAUSADA = "pausada"
AUDIT_ASSUMIDA = "assumida"
AUDIT_RETOMADA = "retomada"
AUDIT_ENCERRADA = "encerrada"
AUDIT_RELACIONAMENTO = "relacionamento"        # LEAD → CUSTOMER / DO_NOT_CONTACT
# Qualidade do número não entra aqui: ela é do WABA, não de um usuário, e
# atribuí-la a alguém seria inventar um dono. Vai para o log do servidor e
# aparece ao vivo em /api/wa/status.


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
    enriched_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    persons = orm_relationship("Person", back_populates="company")


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
    last_seen_at = Column(DateTime(timezone=True), default=utcnow)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    company = orm_relationship("Company", back_populates="persons")
    emails = orm_relationship("PersonEmail", back_populates="person", cascade="all, delete-orphan")
    phones = orm_relationship("PersonPhone", back_populates="person", cascade="all, delete-orphan")


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
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    person = orm_relationship("Person", back_populates="emails")


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
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    person = orm_relationship("Person", back_populates="phones")


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
    last_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ProviderCall(Base):
    """Auditoria de custo/latência por provedor externo."""
    __tablename__ = "provider_calls"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(40), nullable=False, index=True)
    operation = Column(String(40))
    hit = Column(Boolean, default=False)
    cost_usd = Column(Float, default=0.0)
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class OptOut(Base):
    """
    LGPD: bloqueio permanente. Guardamos apenas o HASH do valor — o pedido de
    remoção não pode virar mais uma base de dados pessoais.

    Pedido feito pelo formulário público nasce `pending` e só vira `confirmed`
    quando o link enviado por e-mail é aberto. Sem essa etapa, qualquer pessoa
    apagaria contatos alheios em massa — o direito do titular viraria uma
    ferramenta de sabotagem da base.
    """
    __tablename__ = "opt_outs"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(20), nullable=False)      # email|phone|linkedin
    value_hash = Column(String(64), nullable=False, unique=True, index=True)
    reason = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="confirmed")  # pending|confirmed
    # Só o hash do token de confirmação — o link vale uma vez e expira.
    token_hash = Column(String(64), nullable=True, index=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    # Hash do e-mail que pediu a remoção: auditoria sem guardar o e-mail.
    requested_by_hash = Column(String(64), nullable=True)
    # Valor a remover, em claro, APENAS enquanto o pedido está pendente: a
    # exclusão em `persons`/`person_emails` casa por valor, não por hash, e
    # não dá para reconstruí-lo do digest. Apagado no instante da confirmação
    # — depois disso resta só o hash.
    pending_value = Column(String(320), nullable=True)
    source = Column(String(30), nullable=True)     # form_publico|extensao|interno
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """
    Fila de trabalho. Hoje só enriquecimento de domínio, que é a operação lenta
    do produto (~15-30 s por empresa).

    Existe porque a função serverless morre em 60 s: pedir 200 domínios de uma
    vez em requisição síncrona não é lento, é impossível. Com a fila, o pedido
    responde na hora e o processamento acontece em rodadas curtas.
    """
    __tablename__ = "jobs"
    __table_args__ = (
        # A varredura do worker é sempre "próximos da fila": sem este índice
        # ela vira scan da tabela inteira à medida que o histórico cresce.
        Index("ix_jobs_status_id", "status", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    batch_id = Column(String(36), nullable=True, index=True)
    kind = Column(String(20), nullable=False, default="enrich")
    payload = Column(JSON)                       # {"domain": "acme.com.br"}
    status = Column(String(20), nullable=False, default="queued", index=True)
    # queued | running | done | failed | skipped (cache/cota)
    attempts = Column(Integer, nullable=False, default=0)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True)
    result = Column(String(40), nullable=True)   # enriched | partial | cached | quota | failed
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class ExtensionToken(Base):
    """Pareamento navegador ↔ conta, via código curto exibido no /app."""
    __tablename__ = "extension_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    code = Column(String(16), nullable=True, index=True)      # código de pareamento
    code_expires_at = Column(DateTime(timezone=True), nullable=True)
    token_hash = Column(String(64), nullable=True, index=True)
    device_label = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


# Fichas que não devem aparecer para o usuário:
#   failed  — a coleta rodou e não achou nada aproveitável
#   pending — a busca foi cobrada e a coleta não chegou a terminar (timeout da
#             função serverless, por exemplo). Serve de recibo: a próxima
#             tentativa do mesmo domínio não cobra de novo.
HIDDEN_LEAD_STATUSES = ("failed", "pending")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _missing_columns() -> dict:
    """
    Colunas que existem no modelo e faltam no banco, por tabela.

    Comparação automática contra os modelos — antes isto era uma lista escrita
    à mão, que só protegia as colunas de que alguém lembrou de escrever nela.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    missing: dict = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        absent = [c for c in table.columns if c.name not in present]
        if absent:
            missing[table.name] = absent
    return missing


def _column_ddl(column) -> str:
    """
    DDL de um ADD COLUMN, sempre nullable.

    Coluna nova NOT NULL em tabela com linhas é rejeitada pelo banco; o
    DEFAULT do modelo preenche as próximas gravações e o backfill de linhas
    antigas, quando necessário, é decisão de migração — não de boot.
    """
    ddl_type = column.type.compile(dialect=engine.dialect)
    default = getattr(column, "default", None)
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        literal = f"'{value}'" if isinstance(value, str) else str(int(value) if isinstance(value, bool) else value)
        return f"{column.name} {ddl_type} DEFAULT {literal}"
    return f"{column.name} {ddl_type}"


def _sync_schema() -> None:
    """
    Rede de segurança: aplica os ADD COLUMN que faltarem.

    Com o Alembic adotado, chegar aqui com colunas faltando significa que
    alguém mudou o modelo sem gerar a migração — por isso o log é WARNING, não
    INFO. Continua existindo porque um schema incompleto quebra o produto
    inteiro, e preferimos consertar e avisar a recusar o boot.
    """
    from sqlalchemy import text

    missing = _missing_columns()
    if not missing:
        return

    with engine.begin() as conn:
        for table, columns in missing.items():
            for column in columns:
                ddl = _column_ddl(column)
                logger.warning(
                    "Coluna ausente no banco — aplicando ALTER TABLE %s ADD COLUMN %s. "
                    "Gere a migração correspondente (alembic revision --autogenerate).",
                    table, ddl,
                )
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def schema_status() -> dict:
    """
    O banco atende ao que o código espera? Serve ao /health: sem isto, um
    schema incompleto só aparece como 500 no clique do usuário.
    """
    from sqlalchemy import inspect

    try:
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        expected = {t.name for t in Base.metadata.sorted_tables}
        missing_tables = sorted(expected - existing)
        missing_columns = {
            table: [c.name for c in columns]
            for table, columns in _missing_columns().items()
        }
        return {
            "ok": not missing_tables and not missing_columns,
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
        }
    except Exception as e:  # banco fora do ar
        return {"ok": False, "error": str(e), "missing_tables": [], "missing_columns": {}}


class ImportBatch(Base):
    """
    Uma planilha importada. Guarda a ordem e os rótulos originais das colunas
    (é isso que faz a view de planilha reproduzir o arquivo do usuário) e,
    enquanto está em rascunho, as linhas lidas — assim o preview não precisa
    devolver 1.000 linhas para o navegador e recebê-las de volta no commit.
    """
    __tablename__ = "import_batches"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), nullable=False, index=True)
    filename = Column(String(500))
    sheet_name = Column(String(255))
    columns = Column(JSON)          # [{label, field, kind}] na ordem do arquivo
    row_count = Column(Integer, default=0)
    status = Column(String(20), default="draft")   # draft | committed
    draft_rows = Column(JSON)       # linhas lidas; limpo após o commit
    created_at = Column(DateTime(timezone=True), default=utcnow)
    committed_at = Column(DateTime(timezone=True), nullable=True)


class CRMConnection(Base):
    """
    Configuração de integração CRM por usuário.

    Os três campos de credencial usam `SegredoCriptografado`: no banco eles
    aparecem como `enc:v1:...` e só voltam legíveis dentro do processo, com a
    `SECRETS_KEY`. `webhook_url` e `account_id` ficam em claro de propósito —
    o motivo está em services/crypto.py.
    """
    __tablename__ = "crm_connections"

    user_id = Column(String, primary_key=True)
    provider = Column(String, primary_key=True)  # webhook, dynamics, hubspot, pipedrive
    webhook_url = Column(String, nullable=True)
    webhook_secret = Column(SegredoCriptografado, nullable=True)
    access_token = Column(SegredoCriptografado, nullable=True)
    refresh_token = Column(SegredoCriptografado, nullable=True)
    account_id = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


#: Revisão mais recente em alembic/versions. Precisa acompanhar a última
#: migração criada — o teste tests/test_migracoes.py falha se divergir.
ALEMBIC_HEAD = "e91b4c2a6f18"


def _stamp_alembic_head() -> None:
    """
    Marca o banco como já estando na revisão mais recente.

    Schema criado por `create_all` (desenvolvimento e testes) é idêntico ao que
    as migrações produzem, mas o Alembic não sabe disso: sem o carimbo, um
    `alembic upgrade head` tentaria criar tudo de novo e falharia com "tabela
    já existe". Feito por SQL direto de propósito — o Alembic é ferramenta de
    desenvolvimento e não precisa estar instalado em produção.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tabelas = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "alembic_version" not in tabelas:
            conn.execute(text(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            ))
        atual = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        if atual is None:
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": ALEMBIC_HEAD},
            )


def init_db():
    """
    Garante o schema no ambiente local.

    Em produção a fonte da verdade é o Alembic (`alembic upgrade head`, ver
    docs/MIGRACOES.md). Aqui o create_all continua servindo desenvolvimento e
    testes — onde criar do zero é mais rápido do que aplicar o histórico — e o
    carimbo mantém os dois mundos compatíveis.
    """
    Base.metadata.create_all(bind=engine)
    _sync_schema()
    try:
        _stamp_alembic_head()
    except Exception:
        # Carimbo é conveniência: banco sem permissão de DDL não pode impedir
        # a aplicação de subir por causa disso.
        logger.warning("Não foi possível carimbar a revisão do Alembic.", exc_info=True)
