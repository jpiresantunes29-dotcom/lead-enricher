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
