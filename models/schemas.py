from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, ConfigDict, Field


class EnrichRequest(BaseModel):
    domain: str


class DecisoresRequest(BaseModel):
    lead_id: int
    roles: List[str]


class DecisionMakerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    name: Optional[str] = None
    title_searched: Optional[str] = None
    title_found: Optional[str] = None
    snippet: Optional[str] = None
    linkedin_url: Optional[str] = None
    probable_emails: Optional[List[Any]] = None  # aceita lista de dicts
    match_confidence: Optional[str] = None
    phone: Optional[str] = None


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_input_domain: str
    domain: Optional[str] = None
    company_name: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None
    linkedin_confidence: Optional[str] = None
    mx_provider: Optional[str] = None
    mx_provider_confidence: Optional[str] = None
    mx_records: Optional[List[Any]] = None
    dns_report: Optional[Dict[str, Any]] = None
    hosting_provider: Optional[str] = None
    employee_count: Optional[Any] = None
    employee_count_linkedin: Optional[int] = None
    sector: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    corporate_email: Optional[str] = None
    phone: Optional[str] = None
    status: str
    stage: Optional[str] = "novo"
    ai_summary: Optional[str] = None
    created_at: datetime


class LeadListOut(LeadOut):
    """
    Ficha na listagem: sem o relatório DNS.

    O relatório completo (hosts, TXT, registros) fica guardado dentro de
    `dns_report` e chega a dezenas de KB por lead — cem deles na tela do
    Histórico seriam megabytes que nenhuma coluna da tabela usa. A ficha
    individual (`GET /api/leads/{id}`) continua devolvendo tudo.
    """
    dns_report: Optional[Dict[str, Any]] = Field(default=None, exclude=True)
    mx_records: Optional[List[Any]] = Field(default=None, exclude=True)


class DnsReportResponse(BaseModel):
    success: bool
    cached: bool = False
    report: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class EnrichResponse(BaseModel):
    success: bool
    message: str
    data: Optional[LeadOut] = None


class DecisoresResponse(BaseModel):
    success: bool
    message: str
    decisores: List[DecisionMakerOut] = []


# ── Execução comercial ────────────────────────────────────────────────────────

class ActivityCreate(BaseModel):
    type: str                          # call | email | meeting | note | task
    outcome: Optional[str] = None      # para type=call
    notes: Optional[str] = None
    due_at: Optional[datetime] = None  # para type=task/meeting manuais
    meeting_at: Optional[datetime] = None  # quando outcome=meeting_scheduled


class ActivityUpdate(BaseModel):
    completed: Optional[bool] = None
    due_at: Optional[datetime] = None
    notes: Optional[str] = None


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    type: str
    outcome: Optional[str] = None
    notes: Optional[str] = None
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime


class ActivityResponse(BaseModel):
    success: bool
    message: str
    activity: ActivityOut
    derived: List[ActivityOut] = []    # follow-ups/reuniões criados por regra
    lead_stage: Optional[str] = None


class StageUpdate(BaseModel):
    stage: str


# ── Extensão / Contact Intelligence ──────────────────────────────────────────

class ExtensionPairRequest(BaseModel):
    code: str
    device_label: Optional[str] = None


class ResolveRequest(BaseModel):
    """Contexto capturado do DOM da página que o usuário abriu no LinkedIn."""
    linkedin_url: Optional[str] = None
    linkedin_slug: Optional[str] = None
    full_name: Optional[str] = None
    headline: Optional[str] = None
    title: Optional[str] = None
    company_name: Optional[str] = None
    company_domain: Optional[str] = None
    company_linkedin_slug: Optional[str] = None
    location: Optional[str] = None
    photo_url: Optional[str] = None
    # true = pode usar rede para descobrir o domínio da empresa (mais lento)
    deep: bool = False


class ContactPreview(BaseModel):
    masked: Optional[str] = None
    has: bool = False
    confidence: int = 0
    status: Optional[str] = None
    is_company_phone: Optional[bool] = None


class ResolveResponse(BaseModel):
    person_id: Optional[int] = None
    full_name: Optional[str] = None
    title: Optional[str] = None
    seniority: Optional[str] = None
    department: Optional[str] = None
    company_name: Optional[str] = None
    company_domain: Optional[str] = None
    domain_confidence: Optional[int] = None
    linkedin_url: Optional[str] = None
    email: ContactPreview = ContactPreview()
    phone: ContactPreview = ContactPreview()
    known_pattern: bool = False
    likely_findable: bool = False
    already_revealed: bool = False
    credits_cost: int = 1
    credits_left: Optional[int] = None
    blocked: bool = False
    needs_domain: bool = False


class RevealRequest(BaseModel):
    person_id: Optional[int] = None
    linkedin_slug: Optional[str] = None
    kind: str = "both"        # email | phone | both
    company_domain: Optional[str] = None


class RevealedEmail(BaseModel):
    email: str
    status: Optional[str] = None
    confidence: int = 0
    source: Optional[str] = None
    pattern: Optional[str] = None


class RevealedPhone(BaseModel):
    e164: str
    formatted: Optional[str] = None
    type: Optional[str] = None
    confidence: int = 0
    source: Optional[str] = None
    is_company_phone: bool = False


class RevealResponse(BaseModel):
    success: bool
    message: str
    person_id: Optional[int] = None
    full_name: Optional[str] = None
    title: Optional[str] = None
    company_name: Optional[str] = None
    company_domain: Optional[str] = None
    emails: List[RevealedEmail] = []
    phones: List[RevealedPhone] = []
    company_phone: Optional[str] = None
    credits_charged: int = 0
    credits_left: Optional[int] = None
    from_cache: bool = False
    chain: List[str] = []


class CompanyContextRequest(BaseModel):
    domain: Optional[str] = None
    company_name: Optional[str] = None
    linkedin_slug: Optional[str] = None
    deep: bool = False


class CompanyPersonOut(BaseModel):
    person_id: int
    full_name: Optional[str] = None
    title: Optional[str] = None
    seniority: Optional[str] = None
    linkedin_url: Optional[str] = None
    source: Optional[str] = None
    email_preview: Optional[str] = None
    has_email: bool = False
    has_phone: bool = False


class CompanyContextResponse(BaseModel):
    domain: Optional[str] = None
    name: Optional[str] = None
    sector: Optional[str] = None
    location: Optional[str] = None
    cnpj: Optional[str] = None
    razao_social: Optional[str] = None
    situacao: Optional[str] = None
    porte: Optional[str] = None
    phones: List[Dict[str, Any]] = []
    main_email: Optional[str] = None
    email_pattern: Optional[str] = None
    pattern_confidence: int = 0
    employee_count: Optional[Any] = None
    people: List[CompanyPersonOut] = []
    lead_id: Optional[int] = None


class SaveRequest(BaseModel):
    person_id: int
    lead_id: Optional[int] = None
    note: Optional[str] = None


class ReportRequest(BaseModel):
    person_id: Optional[int] = None
    kind: str                      # email | phone | linkedin
    value: Optional[str] = None
    reason: Optional[str] = None


# ── Enriquecimento em lote ───────────────────────────────────────────────────

class BatchCreateRequest(BaseModel):
    """Aceita a lista pronta ou o texto cru (CSV colado, uma coluna, URLs)."""
    domains: Optional[List[str]] = None
    text: Optional[str] = None


class BatchCreateResponse(BaseModel):
    batch_id: str
    total: int
    ignorados: int = 0
    quota_restante: Optional[int] = None
    cabe_na_quota: bool = True
    message: str


class BatchItemOut(BaseModel):
    domain: Optional[str] = None
    status: str
    result: Optional[str] = None
    lead_id: Optional[int] = None
    error: Optional[str] = None


class BatchProgressOut(BaseModel):
    batch_id: str
    total: int
    concluidos: int
    na_fila: int
    rodando: int
    com_erro: int
    finalizado: bool
    itens: List[BatchItemOut] = []


class BatchRunResponse(BaseModel):
    processed: int
    done: int
    failed: int
    quota_reached: bool
    remaining: int
    elapsed_ms: int
    progresso: BatchProgressOut


class OptOutRequest(BaseModel):
    """Pedido público de remoção (LGPD art. 18). Não exige conta."""
    kind: str                      # email | phone | linkedin
    value: str
    # Canal de confirmação. Para kind="email" é o próprio valor; para telefone
    # e LinkedIn é obrigatório, porque nenhum pedido vale sem confirmação.
    contact_email: Optional[str] = None
    reason: Optional[str] = None
