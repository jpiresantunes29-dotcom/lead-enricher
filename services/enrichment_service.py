"""
Enriquecimento de um domínio para um usuário — cache e persistência.

Vive fora do router porque existem dois caminhos até aqui: a busca avulsa
(`POST /api/enrich`, síncrona) e a fila de lote (`services/jobs.py`). Regra
duplicada em dois lugares é regra que vai divergir; aqui ela é escrita uma vez.

Ordem das decisões (a mesma nos dois caminhos):
  1. ficha recente da versão atual  → serve do cache, sem tocar a rede
  2. tentativa recente do domínio   → reaproveita a ficha (inclui a que morreu
                                      no meio e a de versão antiga)
  3. grava a ficha "pending" e só então coleta
"""
import logging
from dataclasses import dataclass
from datetime import datetime, UTC, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models.database import Lead, Profile, HIDDEN_LEAD_STATUSES
from services._utils import normalize_domain
from services.domain_finder import find_domain
from services.importer import looks_like_domain
from services.enricher import enrich_company, ENRICHMENT_VERSION
from services.lead_scorer import apply_score
from services.people.waterfall import ingest_enrichment

logger = logging.getLogger(__name__)

# Fichas com menos de 7 dias são servidas do cache
CACHE_TTL_DAYS = 7

# Resultados possíveis de uma tentativa de enriquecimento.
RESULT_CACHED = "cached"       # já tínhamos a ficha
RESULT_ENRICHED = "enriched"   # coleta completa
RESULT_PARTIAL = "partial"     # coletou parte dos campos-alvo
RESULT_FAILED = "failed"       # coleta não trouxe nada aproveitável
RESULT_ERROR = "error"         # exceção durante a coleta


@dataclass
class EnrichOutcome:
    """O que aconteceu com uma tentativa — quem chama decide como apresentar."""
    result: str
    lead: Optional[Lead] = None
    message: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.result in (RESULT_CACHED, RESULT_ENRICHED, RESULT_PARTIAL)


def _recent_leads(db: Session, domain: str, user_id: str, include_hidden: bool = False):
    """Fichas do domínio dentro da janela de cache, mais recente primeiro."""
    cutoff = datetime.now(UTC) - timedelta(days=CACHE_TTL_DAYS)
    query = db.query(Lead).filter(
        Lead.user_id == user_id,
        Lead.domain == domain,
        Lead.created_at >= cutoff,
    )
    if not include_hidden:
        query = query.filter(Lead.status.notin_(HIDDEN_LEAD_STATUSES))
    return query.order_by(Lead.created_at.desc())


def find_cached_lead(db: Session, domain: str, user_id: str) -> Optional[Lead]:
    """
    Ficha recente do mesmo domínio para o mesmo usuário, se existir.

    Ficha gerada por uma versão anterior da coleta não conta como cache: a
    correção que a tornou obsoleta precisa chegar ao usuário na próxima busca,
    não daqui a 7 dias.
    """
    return _recent_leads(db, domain, user_id).filter(
        Lead.enrichment_version == ENRICHMENT_VERSION
    ).first()


def find_previous_attempt(db: Session, domain: str, user_id: str) -> Optional[Lead]:
    """
    Tentativa recente deste domínio — inclusive as que não terminaram (status
    "pending", tipicamente uma função serverless morta no meio da coleta).
    Reaproveitá-la é o que impede a mesma empresa virar duas fichas.
    """
    return _recent_leads(db, domain, user_id, include_hidden=True).first()


def enrich_existing_lead(db: Session, profile: Profile, lead: Lead) -> EnrichOutcome:
    """
    Enriquece uma ficha que já existe — na prática, as que vieram de planilha.

    Planilha de prospecção quase sempre traz nome e LinkedIn, não o site.
    Quando falta o domínio, ele é descoberto pelo nome antes da coleta; sem
    isso a linha inteira seria inaproveitável. Depois disso o caminho é o
    mesmo de qualquer busca.
    """
    domain = lead.domain or ""
    if not domain and looks_like_domain(lead.raw_input_domain or ""):
        domain = normalize_domain(lead.raw_input_domain)

    if not domain and lead.company_name:
        found = find_domain(lead.company_name, lead.linkedin_url, lead.location)
        if found.get("domain") and found.get("confidence") in ("high", "medium"):
            domain = found["domain"]
            lead.domain = domain
            lead.website = f"https://{domain}"
            if not lead.raw_input_domain:
                lead.raw_input_domain = domain
            db.commit()
            logger.info(
                "Domínio descoberto lead=%s domain=%s conf=%s",
                lead.id, domain, found.get("confidence"),
            )

    if not domain:
        lead.status = "failed"
        db.commit()
        return EnrichOutcome(
            result=RESULT_ERROR,
            lead=lead,
            message="Não encontrei o site desta empresa. Preencha a coluna Domínio para enriquecer.",
            error="dominio_desconhecido",
        )

    outcome = enrich_for_user(db, profile, domain, existing_lead=lead)
    return outcome


@dataclass
class PreparedEnrichment:
    """
    O que dá para decidir sem rede: se já existe resposta pronta (cache, ou
    erro de domínio inválido) ou, senão, a ficha reservada e à espera da
    coleta.

    Usado por `services/jobs.py` para coletar vários domínios em paralelo:
    esta parte (banco, rápida) roda sequencial antes de disparar a coleta, e
    `finish_enrichment` (também banco, rápida) fecha o ciclo depois — o que
    fica em paralelo é só o meio, que é rede pura.
    """
    outcome: Optional[EnrichOutcome] = None   # pronto: cache ou erro, nada mais a fazer
    domain: Optional[str] = None
    lead: Optional[Lead] = None               # reservado; falta coletar e chamar finish_enrichment


def reserve_lead(db: Session, lead: Lead) -> Lead:
    """Grava o placeholder da ficha, se ainda não existir. Só banco, sem rede."""
    if lead.id is None:
        db.add(lead)
        db.commit()
        db.refresh(lead)
    return lead


def prepare_enrichment(db: Session, profile: Profile, raw_domain: str,
                       existing_lead: Optional[Lead] = None) -> PreparedEnrichment:
    """A parte de `enrich_for_user` que só toca banco: decide cache ou reserva a ficha."""
    domain = normalize_domain(raw_domain)
    if not domain:
        return PreparedEnrichment(outcome=EnrichOutcome(
            result=RESULT_ERROR, message="Domínio inválido.", error="dominio_vazio",
        ))

    if existing_lead is not None:
        return PreparedEnrichment(domain=domain, lead=reserve_lead(db, existing_lead))

    cached = find_cached_lead(db, domain, profile.id)
    if cached:
        logger.info("Cache hit for domain=%s user=%s", domain, profile.id)
        return PreparedEnrichment(outcome=EnrichOutcome(
            result=RESULT_CACHED, lead=cached, message="Dados carregados do cache.",
        ))

    # A ficha nasce ANTES da coleta: se o processo morrer no meio, a próxima
    # tentativa continua nesta mesma linha em vez de abrir outra.
    previous = find_previous_attempt(db, domain, profile.id)
    lead = previous if previous is not None else Lead(
        user_id=profile.id, raw_input_domain=raw_domain, domain=domain, status="pending",
    )
    return PreparedEnrichment(domain=domain, lead=reserve_lead(db, lead))


def enrich_for_user(db: Session, profile: Profile, raw_domain: str,
                    existing_lead: Optional[Lead] = None) -> EnrichOutcome:
    """
    Enriquece um domínio para o dono do `profile`. Commita o que precisa
    commitar e nunca levanta exceção de coleta.

    `existing_lead` reaproveita uma ficha já criada (linha de planilha) em vez
    de abrir outra — senão a mesma empresa apareceria duas vezes no histórico,
    uma com as células originais e outra com os dados coletados.
    """
    prepared = prepare_enrichment(db, profile, raw_domain, existing_lead=existing_lead)
    if prepared.outcome is not None:
        return prepared.outcome

    try:
        data = enrich_company(raw_domain)
        error = None
    except Exception as e:
        data, error = None, e
    return finish_enrichment(db, profile, prepared.domain, prepared.lead, data, error)


# Campos que só a coleta preenche. Uma coleta que volta vazia significa "desta
# vez não deu" — DNS fora do ar, site bloqueando robô, orçamento de tempo
# estourado — e não "esta empresa não tem mais MX". Sobrescrever com vazio
# apagava dados bons de uma ficha que já estava certa: foi assim que uma ficha
# com MX do Google e status "partial" virou uma ficha vazia com status
# "failed" 22 segundos depois, num refresh que pegou o DNS mudo.
_CAMPOS_COLETADOS = (
    "company_name", "website", "linkedin_url", "linkedin_confidence",
    "mx_provider", "mx_provider_confidence", "mx_records", "dns_report",
    "hosting_provider", "employee_count", "employee_count_linkedin",
    "sector", "location", "description", "corporate_email", "phone",
)

# Do pior para o melhor. "pending" é o placeholder de uma ficha que ainda não
# coletou nada, então vale menos que qualquer resultado real.
_ORDEM_STATUS = {"failed": 0, "pending": 0, "partial": 1, "enriched": 2}

# Os mesmos campos que `enricher.enrich_company` usa para decidir o status. Se
# nenhum deles sobreviveu na ficha, não há o que proteger e o status novo vale.
_CAMPOS_ALVO = ("linkedin_url", "mx_provider", "employee_count", "location", "sector")


def _vazio(valor) -> bool:
    """None, string em branco, lista/dict vazios. `0` e `False` são valores."""
    if valor is None:
        return True
    if isinstance(valor, str):
        return not valor.strip()
    if isinstance(valor, (list, dict, tuple, set)):
        return len(valor) == 0
    return False


def _relatorio_dns_vazio(rel) -> bool:
    """
    Um relatório de DNS que não achou nada não vem vazio: vem com a estrutura
    inteira montada e todas as listas em branco (`{"mx": [], "a": [], ...}`).
    Pelo tamanho do dict ele parece um dado; pelo conteúdo não é nada — e era
    assim que um relatório bom era substituído por um relatório mudo.
    """
    if not isinstance(rel, dict):
        return _vazio(rel)
    return not any(
        not _vazio(v) for k, v in rel.items() if k != "domain"
    )


def merge_collected(lead: Lead, data: dict) -> None:
    """
    Grava a coleta na ficha sem apagar o que já estava lá.

    Valor novo preenchido substitui o antigo (a coleta é a fonte da verdade
    para esses campos). Valor novo vazio preserva o antigo. O `status` é o
    melhor entre o antigo e o novo — se a ficha continua com MX e setor de uma
    coleta anterior, ela não é "failed" só porque esta tentativa não trouxe
    nada.
    """
    for key, value in data.items():
        if not hasattr(Lead, key):
            continue
        if key == "status":
            continue
        if key in _CAMPOS_COLETADOS:
            checar = _relatorio_dns_vazio if key == "dns_report" else _vazio
            if checar(value) and not checar(getattr(lead, key, None)):
                continue
        setattr(lead, key, value)

    novo = data.get("status")
    if novo:
        atual = lead.status
        preservou = any(not _vazio(getattr(lead, k, None)) for k in _CAMPOS_ALVO)
        rebaixa = _ORDEM_STATUS.get(novo, 0) < _ORDEM_STATUS.get(atual, 0)
        if rebaixa and preservou:
            logger.info(
                "Coleta voltou %s mas a ficha mantém %s: dados anteriores preservados domain=%s",
                novo, atual, lead.domain,
            )
        else:
            lead.status = novo


def finish_enrichment(db: Session, profile: Profile, domain: str, lead: Lead,
                      data: Optional[dict], error: Optional[Exception] = None) -> EnrichOutcome:
    """
    Grava o resultado de UMA coleta já feita. Só banco — nenhuma chamada de
    rede acontece aqui.

    Separada da coleta para permitir buscar vários domínios em paralelo
    (services/jobs.py) sem que threads diferentes toquem o banco ao mesmo
    tempo: a coleta roda solta, e só a gravação — rápida — acontece na sessão
    principal, uma de cada vez.
    """
    if error is not None:
        lead.status = "failed"
        db.commit()
        logger.exception("Enrichment failed for domain=%s: %s", domain, error)
        return EnrichOutcome(
            result=RESULT_ERROR, lead=lead,
            message=f"Erro ao enriquecer: {error}", error=str(error)[:500],
        )

    merge_collected(lead, data)
    lead.user_id = profile.id
    lead.refreshed_at = datetime.now(UTC)

    # Pontua com o que acabou de ser coletado. Aqui a ficha normalmente ainda
    # não tem decisores (a busca é um passo separado, que o usuário dispara),
    # então esta é a nota "só com dados do domínio" — `POST /api/decisores`
    # repontua depois e a nota sobe. Pontuar mesmo assim é o que faz a ficha
    # nascer ordenável: sem isso ela entraria no histórico sem nota e ficaria
    # invisível em qualquer lista ordenada por prioridade.
    apply_score(lead, decision_makers=[])

    db.commit()
    db.refresh(lead)

    # Alimenta o banco global de contatos: empresa, padrão de e-mail do domínio
    # e dados públicos do CNPJ. Roda DEPOIS do commit da ficha, em transação
    # própria — uma falha aqui não pode desfazer a coleta que já deu certo.
    try:
        ingest_enrichment(db, data)
        db.commit()
    except Exception:
        logger.exception("ingest_enrichment falhou domain=%s", domain)
        db.rollback()

    # O que vale para o usuário é o estado da ficha depois do merge, não o
    # resultado isolado desta tentativa: uma coleta que voltou vazia sobre uma
    # ficha já enriquecida não é "não conseguimos coletar nada".
    status = lead.status
    logger.info("Enriched domain=%s status=%s (coleta=%s) user=%s",
                domain, status, data.get("status"), profile.id)

    if status == "failed":
        return EnrichOutcome(
            result=RESULT_FAILED, lead=lead,
            message="Não conseguimos coletar dados deste domínio.",
        )
    return EnrichOutcome(
        result=RESULT_PARTIAL if status == "partial" else RESULT_ENRICHED,
        lead=lead, message="Enriquecimento concluído.",
    )
