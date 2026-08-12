from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, ConfigDict, Field, computed_field

from services.phone_normalizer import normalize_input


def _telefone_e_celular(phone: Optional[str]) -> Optional[bool]:
    """
    None quando não há telefone ou o formato não permite dizer.

    Serve para a tela avisar que o número guardado atende numa central: é o
    telefone da empresa que a coleta encontra, e mandar mensagem para ele não
    alcança o decisor.
    """
    if not phone:
        return None
    dados = normalize_input(phone)
    return dados["is_mobile"] if dados else None


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

    @computed_field
    @property
    def phone_is_mobile(self) -> Optional[bool]:
        return _telefone_e_celular(self.phone)


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

    @computed_field
    @property
    def phone_is_mobile(self) -> Optional[bool]:
        return _telefone_e_celular(self.phone)


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


class LeadUpdate(BaseModel):
    """
    Correção manual da ficha.

    Só o telefone por enquanto — é o campo que a coleta erra com mais
    frequência e o único que bloqueia o contato por WhatsApp. String vazia
    apaga o valor; campo ausente não é tocado.
    """
    phone: Optional[str] = None


class DecisionMakerUpdate(BaseModel):
    """Celular do decisor, confirmado por quem está prospectando."""
    phone: Optional[str] = None


# ── WhatsApp ─────────────────────────────────────────────────────────────────

class WaStartRequest(BaseModel):
    """
    Pedido de primeiro contato.

    `phone` só é necessário quando o número certo não está guardado em lugar
    nenhum ainda; sem ele, o destino sai do decisor escolhido e, por último, do
    telefone da empresa.
    """
    lead_id: int
    phone: Optional[str] = None
    decision_maker_id: Optional[int] = None
    # O template aprovado pode ter uma variável no corpo (o nome da empresa).
    # Falso quando o template é fixo — mandar parâmetro a mais faz a Meta
    # recusar a mensagem.
    usar_nome_da_empresa: bool = True


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    decision_maker_id: Optional[int] = None
    phone_e164: str
    channel: str
    ai_status: str
    window_expires_at: Optional[datetime] = None
    last_inbound_at: Optional[datetime] = None
    last_outbound_at: Optional[datetime] = None
    last_message_body: Optional[str] = None
    handoff_reason: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class WaStartResponse(BaseModel):
    success: bool
    message: str
    conversation: ConversationOut


class ConversationSeal(BaseModel):
    """
    O selo do card. Vem pronto do servidor porque o estado da conversa é uma
    regra de negócio, não uma decisão de tela: dois lugares calculando isso
    acabam discordando, e o usuário confia no que está vendo.
    """
    tom: str          # ativa | aguardando | assumida | pausada | encerrada
    rotulo: str       # o que aparece no selo
    explicacao: str   # o que isso significa, em uma frase


class ConversationCard(BaseModel):
    """Uma conversa na lista, com o que o card precisa mostrar."""
    id: int
    lead_id: int
    company_name: Optional[str] = None
    contato: Optional[str] = None       # nome do decisor, quando há
    phone_e164: str
    ai_status: str
    selo: ConversationSeal
    last_message_body: Optional[str] = None
    last_inbound_at: Optional[datetime] = None
    last_outbound_at: Optional[datetime] = None
    handoff_reason: Optional[str] = None
    janela_aberta: bool = False
    aguardando_voce: bool = False
    updated_at: Optional[datetime] = None


class WaMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    direction: str
    type: str
    body: Optional[str] = None
    template_name: Optional[str] = None
    status: Optional[str] = None
    sent_by: Optional[str] = None
    intent_detected: Optional[str] = None
    created_at: datetime


class ConversationDetail(BaseModel):
    card: ConversationCard
    messages: List[WaMessageOut] = []


class ConversationAction(BaseModel):
    """
    Ação do usuário sobre a automação.

    Só quatro verbos, e nenhum deles escreve `ai_status` diretamente: quem
    traduz verbo em estado é o serviço, para a tela não poder inventar um
    estado que o portão não conhece.
    """
    acao: str          # pausar | assumir | retomar | encerrar
    motivo: Optional[str] = None


class WaReplyRequest(BaseModel):
    """Resposta escrita pelo próprio usuário, com a IA fora do caminho."""
    texto: str


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: Optional[int] = None
    lead_id: Optional[int] = None
    acao: str
    ator: str
    detalhe: Optional[str] = None
    created_at: datetime


class WaMetrics(BaseModel):
    """
    Números da operação de WhatsApp no período.

    Sem estimativa de custo em reais: o preço do template muda por país e por
    categoria, e um valor inventado na tela vira uma decisão de negócio errada.
    O que aparece é a **contagem de convites**, que é o que se multiplica pela
    tabela atual da Meta.
    """
    dias: int
    conversas_iniciadas: int = 0
    convites_enviados: int = 0
    leads_que_responderam: int = 0
    taxa_de_resposta: float = 0.0        # 0 a 1
    respostas_da_ia: int = 0
    respostas_suas: int = 0
    handoffs: int = 0
    taxa_de_handoff: float = 0.0         # sobre as conversas com resposta
    motivos_de_handoff: List[Dict[str, Any]] = []
    viraram_cliente: int = 0
    pediram_para_parar: int = 0
    aguardando_voce: int = 0
    # Como a Meta classifica o número agora. None quando não dá para consultar.
    qualidade_do_numero: Optional[Dict[str, Any]] = None


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


# ── Importação de planilha e visão de grade ──────────────────────────────────
# A planilha importada é reproduzida célula a célula na tela; estes schemas
# carregam a ordem e o rótulo original de cada coluna, para o arquivo do
# usuário chegar igual do outro lado.

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
