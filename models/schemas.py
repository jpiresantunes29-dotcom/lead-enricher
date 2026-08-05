from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, ConfigDict


class EnrichRequest(BaseModel):
    domain: str


class DecisoresRequest(BaseModel):
    lead_id: int
    roles: List[str]


class MXRecord(BaseModel):
    """Versão enriquecida do registro MX (estilo DNS Dumpster)."""
    priority: int
    host: str
    ip: Optional[str] = None
    asn: Optional[str] = None
    asn_org: Optional[str] = None
    country: Optional[str] = None


class DNSReport(BaseModel):
    domain: str
    mx: List[MXRecord] = []
    a: List[str] = []
    aaaa: List[str] = []
    ns: List[str] = []
    txt: List[str] = []
    spf: Optional[str] = None
    dmarc: Optional[str] = None
    dkim_records: List[str] = []
    verifications: List[Dict[str, Any]] = []
    soa: Optional[Dict[str, Any]] = None
    mx_provider: Optional[str] = None
    mx_provider_confidence: str = "none"
    hosting_provider: Optional[str] = None
    hosting_confidence: str = "none"


class EmployeeCount(BaseModel):
    raw: Optional[str] = None
    min: Optional[int] = None
    max: Optional[int] = None
    band: Optional[str] = None
    exact: Optional[int] = None
    source: Optional[str] = None


class ProbableEmail(BaseModel):
    email: str
    status: str  # valid | catch_all | invalid | unknown


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
    score: Optional[int] = None
    priority: Optional[str] = None
    score_breakdown: Optional[List[Dict[str, Any]]] = None
    score_version: Optional[str] = None
    stage: Optional[str] = "novo"
    ai_summary: Optional[str] = None
    created_at: datetime


class EnrichResponse(BaseModel):
    success: bool
    message: str
    data: Optional[LeadOut] = None


class DecisoresResponse(BaseModel):
    success: bool
    message: str
    decisores: List[DecisionMakerOut] = []
    # Score recalculado após a busca (sinais de decisor mudam o score)
    lead_score: Optional[int] = None
    lead_priority: Optional[str] = None


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


# ── Importação de planilha ────────────────────────────────────────────────────

class SheetColumn(BaseModel):
    """Coluna da planilha, na ordem original do arquivo."""
    label: str                        # rótulo exato do cabeçalho ("📩 Email")
    field: Optional[str] = None       # campo do Lead que ela alimenta, se houver
    kind: str = "text"                # text | number | date | bool


class SheetSummary(BaseModel):
    name: str
    rows: int = 0
    columns: int = 0
    mapped_fields: List[str] = []
    score: int = 0


class ImportRowOut(BaseModel):
    """Linha lida do arquivo, com diagnóstico e todas as células preservadas."""
    row_number: int
    status: str                       # ok | invalid | duplicate_file | duplicate_db
    reason: Optional[str] = None
    cells: Dict[str, Any] = {}
    lead: Dict[str, Any] = {}


class ImportPreviewResponse(BaseModel):
    success: bool
    message: str
    batch_id: str
    filename: str
    sheet: str
    sheets: List[SheetSummary] = []
    columns: List[SheetColumn] = []
    total_rows: int = 0
    importable: int = 0
    counts: Dict[str, int] = {}
    truncated: bool = False
    rows: List[ImportRowOut] = []     # só as primeiras linhas, para conferência


class ImportCommitRequest(BaseModel):
    skip_existing: bool = True        # pula empresas que já estão no histórico
    skip_duplicates: bool = False     # pula linhas repetidas dentro do arquivo


class ImportCommitResponse(BaseModel):
    success: bool
    message: str
    batch_id: str
    created: int = 0
    skipped: int = 0


class ImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: Optional[str] = None
    sheet_name: Optional[str] = None
    row_count: int = 0
    enriched_count: int = 0
    created_at: datetime


# ── Planilha (grid dentro do sistema) ────────────────────────────────────────

class SheetRowOut(BaseModel):
    """Uma linha do grid: id do lead + valores por rótulo de coluna."""
    id: int
    sheet_row: Optional[int] = None
    status: str
    values: Dict[str, Any] = {}


class SheetResponse(BaseModel):
    batch: Optional[ImportBatchOut] = None
    batches: List[ImportBatchOut] = []
    columns: List[SheetColumn] = []
    system_columns: List[SheetColumn] = []
    rows: List[SheetRowOut] = []
    total: int = 0
    page: int = 1
    per_page: int = 100
    enrichable: int = 0               # linhas ainda não enriquecidas
    pending_ids: List[int] = []       # ids da fila, respeitando busca/filtro


class CellUpdate(BaseModel):
    lead_id: int
    column: str
    value: Optional[Any] = None


class SheetRowCreate(BaseModel):
    batch_id: Optional[str] = None
    values: Dict[str, Any] = {}


class SheetRowsDelete(BaseModel):
    ids: List[int]
