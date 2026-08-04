"""
Cascata de aquisição de contatos — da fonte mais barata para a mais cara.

Ordem (tudo abaixo é gratuito hoje):
  1. cache próprio            (person_emails / person_phones frescos)
  2. contexto do LinkedIn     (nome, cargo, empresa vindos do DOM da extensão)
  3. padrão do domínio        (email_patterns) + verificação SMTP em lote
  4. site da empresa          (e-mails nominais, telefone comercial)
  5. Receita Federal / CNPJ   (telefone e e-mail oficiais + sócios)
  6. provedores pagos         (hook pronto, desligado — ver services/providers)

Regras de tempo: TODA etapa tem orçamento. O reveal devolve o que conseguiu
dentro do prazo em vez de estourar o limite da função serverless.
"""
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
from typing import List, Optional

import phonenumbers
from sqlalchemy.orm import Session

from models.database import Company, Person
from services._utils import normalize_domain, tld_to_region, LINKEDIN_COMPANY_RE
from services.email_verifier import has_mx, smtp_probe_available, verify_batch
from services.providers import cnpj_receita, premium_find_contacts
from . import email_patterns as ep
from . import optout, repository as repo

logger = logging.getLogger(__name__)

# Empresa re-enriquecida a cada 30 dias (MX, telefone e site mudam devagar)
COMPANY_TTL_DAYS = 30

# Confiança final por resultado da verificação
CONF_VERIFIED = 97          # SMTP confirmou a caixa
CONF_CATCH_ALL_CAP = 70     # servidor aceita tudo: não dá para confirmar
CONF_UNVERIFIABLE_PENALTY = 10   # greylist/timeout com sondagem disponível
CONF_COMPANY_PHONE = 65     # telefone da empresa (não é o celular da pessoa)
CONF_CNPJ_PHONE = 75        # telefone declarado à Receita


# ── Utilidades ───────────────────────────────────────────────────────────────

def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    visible = local[0] if local else ""
    return f"{visible}{'•' * max(3, min(len(local) - 1, 6))}@{domain}"


def mask_phone(formatted: str) -> str:
    if not formatted:
        return ""
    digits = re.sub(r"\D", "", formatted)
    if len(digits) < 6:
        return "•" * len(formatted)
    prefix = formatted[:max(6, len(formatted) - 8)]
    return f"{prefix}••••-••{digits[-2:]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_phone(raw: str, region: str = "BR") -> Optional[dict]:
    """Telefone bruto (Receita, site) → dict normalizado, ou None se inválido."""
    if not raw:
        return None
    try:
        num = phonenumbers.parse(raw, region)
        if not phonenumbers.is_valid_number(num):
            return None
    except Exception:
        return None
    from services.phone_normalizer import _classify_type, _format_pretty
    return {
        "e164": phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164),
        "formatted": _format_pretty(num),
        "type": _classify_type(num),
    }


# ── Etapa: contexto da empresa ───────────────────────────────────────────────

def _apply_site_data(db: Session, company: Company, site: dict) -> None:
    """Consolida o scraping e — o mais importante — aprende o padrão de e-mail."""
    if not site or site.get("status") == "failed":
        return

    region = tld_to_region(company.domain)
    phones = []
    for item in site.get("phones") or []:
        phones.append({
            "e164": item.get("e164"),
            "formatted": item.get("formatted"),
            "type": item.get("type"),
            "source": "site",
            "confidence": CONF_COMPANY_PHONE,
        })
    if not phones and site.get("phone"):
        norm = _normalize_phone(site["phone"], region)
        if norm:
            phones.append({**norm, "source": "site", "confidence": CONF_COMPANY_PHONE})

    repo.upsert_company(
        db, company.domain,
        name=site.get("company_name"),
        sector=site.get("sector"),
        location=site.get("location"),
        phone=site.get("phone"),
        phones=phones or None,
        main_email=site.get("corporate_email"),
        emails=site.get("emails") or None,
        cnpj=site.get("cnpj"),
    )

    for email in site.get("emails") or []:
        ep.learn_from_email(db, company.domain, email, None, source="site")


def _apply_cnpj_data(db: Session, company: Company, data: dict) -> List[Person]:
    """Dados públicos da Receita → empresa + sócios como decisores."""
    if not data:
        return []

    phones = []
    for raw in cnpj_receita.company_phones(data):
        norm = _normalize_phone(raw, "BR")
        if norm:
            phones.append({**norm, "source": "cnpj", "confidence": CONF_CNPJ_PHONE})

    merged_phones = list(company.phones or [])
    for p in phones:
        if not any(existing.get("e164") == p["e164"] for existing in merged_phones):
            merged_phones.append(p)

    repo.upsert_company(
        db, company.domain,
        cnpj=data.get("cnpj"),
        name=company.name or data.get("nome_fantasia") or data.get("razao_social"),
        sector=company.sector or cnpj_receita.sector_from_cnpj(data),
        location=company.location or cnpj_receita.location_from_cnpj(data),
        phones=merged_phones or None,
        main_email=company.main_email or data.get("email"),
        cnpj_data={
            "razao_social": data.get("razao_social"),
            "nome_fantasia": data.get("nome_fantasia"),
            "situacao": data.get("situacao"),
            "porte": data.get("porte"),
            "cnae": data.get("cnae"),
            "abertura": data.get("abertura"),
            "capital_social": data.get("capital_social"),
            "municipio": data.get("municipio"),
            "uf": data.get("uf"),
            "socios": [
                {
                    "nome": s["nome"],
                    "qualificacao": cnpj_receita.clean_qualification(s.get("qualificacao") or ""),
                    "pessoa_fisica": s.get("pessoa_fisica"),
                }
                for s in (data.get("qsa") or [])
            ],
            "fonte": data.get("source"),
        },
    )

    # E-mails declarados à Receita também ensinam o padrão do domínio
    for email in ([data.get("email")] + list(data.get("emails") or [])):
        if email and email.endswith("@" + company.domain):
            ep.learn_from_email(db, company.domain, email, None, source="cnpj")

    created = []
    for socio in cnpj_receita.decision_makers_from_qsa(data):
        person = repo.upsert_person(
            db,
            full_name=socio["name"],
            title=socio["title"],
            company_domain=company.domain,
            company_name=company.name,
            company=company,
            source="cnpj_qsa",
        )
        if person:
            created.append(person)
    return created


def ingest_enrichment(db: Session, data: dict) -> Optional[Company]:
    """
    Converte o resultado de `enrich_company()` em conhecimento permanente:
    empresa global, padrão de e-mail do domínio e dados públicos do CNPJ.

    Chamado depois de cada busca do app — é o que faz o banco de contatos
    crescer sozinho, sem trabalho extra do usuário. Não commita.
    """
    domain = normalize_domain(data.get("domain") or "")
    if not domain:
        return None

    region = tld_to_region(domain)
    phones = []
    for item in data.get("site_phones") or []:
        if item.get("e164"):
            phones.append({
                "e164": item["e164"],
                "formatted": item.get("formatted"),
                "type": item.get("type"),
                "source": "site",
                "confidence": CONF_COMPANY_PHONE,
            })
    if not phones and data.get("phone"):
        norm = _normalize_phone(data["phone"], region)
        if norm:
            phones.append({**norm, "source": "site", "confidence": CONF_COMPANY_PHONE})

    linkedin_slug = None
    if data.get("linkedin_url"):
        match = LINKEDIN_COMPANY_RE.search(data["linkedin_url"])
        linkedin_slug = match.group(1).lower() if match else None

    company = repo.upsert_company(
        db, domain,
        name=data.get("company_name"),
        linkedin_slug=linkedin_slug,
        sector=data.get("sector"),
        location=data.get("location"),
        employee_count=data.get("employee_count"),
        phone=data.get("phone"),
        phones=phones or None,
        main_email=data.get("corporate_email"),
        emails=data.get("site_emails") or None,
        cnpj=data.get("cnpj"),
    )

    for email in data.get("site_emails") or []:
        ep.learn_from_email(db, domain, email, None, source="site")
    if data.get("corporate_email"):
        ep.learn_from_email(db, domain, data["corporate_email"], None, source="site")

    mx_provider = data.get("mx_provider")
    ep.record_domain_health(db, domain, mx_ok=bool(mx_provider) or has_mx(domain))

    if company and company.cnpj:
        try:
            cnpj_data = cnpj_receita.lookup_cnpj(company.cnpj)
        except Exception as e:
            logger.debug("Consulta de CNPJ falhou domain=%s: %s", domain, e)
            cnpj_data = None
        if cnpj_data:
            _apply_cnpj_data(db, company, cnpj_data)

    if company:
        company.enriched_at = _now()
    return company


def ensure_company_context(db: Session, domain: str, company_name: Optional[str] = None,
                           linkedin_slug: Optional[str] = None,
                           budget_seconds: float = 8.0,
                           allow_network: bool = True) -> Optional[Company]:
    """
    Garante que sabemos o suficiente sobre a empresa para revelar contatos:
    padrão de e-mail, MX, telefone comercial e CNPJ.

    Cacheado por COMPANY_TTL_DAYS. Fora do cache, roda site e Receita em
    paralelo dentro do orçamento — o que não voltar a tempo fica para a
    próxima chamada.
    """
    domain = normalize_domain(domain or "")
    if not domain:
        return None

    company = repo.upsert_company(db, domain, name=company_name, linkedin_slug=linkedin_slug)
    fresh = repo.is_fresh(company.enriched_at, days=COMPANY_TTL_DAYS)
    if fresh or not allow_network:
        return company

    deadline = time.monotonic() + budget_seconds
    site_data = None
    cnpj_data = None

    from services.scraper import scrape_website

    with ThreadPoolExecutor(max_workers=2) as pool:
        site_future = pool.submit(scrape_website, f"https://{domain}")
        cnpj_future = pool.submit(cnpj_receita.lookup_cnpj, company.cnpj) if company.cnpj else None

        try:
            site_data = site_future.result(timeout=max(0.5, deadline - time.monotonic()))
        except Exception as e:
            # Timeout ou falha de rede: seguimos com o que já temos.
            logger.debug("site scrape indisponível domain=%s: %s", domain, e)

        if cnpj_future is not None:
            try:
                cnpj_data = cnpj_future.result(timeout=max(0.5, deadline - time.monotonic()))
            except Exception:
                cnpj_data = None

    if site_data:
        _apply_site_data(db, company, site_data)

    # CNPJ descoberto agora no rodapé do site → consulta a Receita
    if not cnpj_data and company.cnpj and time.monotonic() < deadline:
        try:
            cnpj_data = cnpj_receita.lookup_cnpj(company.cnpj)
        except Exception:
            cnpj_data = None
    if cnpj_data:
        _apply_cnpj_data(db, company, cnpj_data)

    ep.record_domain_health(db, domain, mx_ok=has_mx(domain))
    company.enriched_at = _now()
    return company


# ── Etapa: e-mails da pessoa ─────────────────────────────────────────────────

def _score_email(candidate: dict, status: str, catch_all: bool) -> int:
    base = candidate["confidence"]
    if status == "valid":
        return CONF_VERIFIED
    if status == "invalid":
        return 0
    if status == "catch_all" or catch_all:
        return min(base, CONF_CATCH_ALL_CAP)
    # unknown: se nem dava para sondar, o padrão continua valendo integralmente
    return base if not smtp_probe_available() else max(10, base - CONF_UNVERIFIABLE_PENALTY)


def _resolve_emails(db: Session, person: Person, domain: str,
                    budget_seconds: float) -> List[dict]:
    """Gera candidatos pelo padrão do domínio e verifica em lote."""
    if not domain or not person.full_name:
        return []
    if not has_mx(domain):
        logger.info("Domínio sem MX, e-mail impossível domain=%s", domain)
        return []

    candidates = ep.candidates(db, domain, person.full_name, limit=4)
    if not candidates:
        return []

    # Candidato que já pediu remoção sai antes da verificação: não gastamos
    # sondagem SMTP com ele e ele nunca chega perto da gravação.
    permitidos = set(optout.filter_contacts(
        db, emails=[c["email"] for c in candidates], phones=[],
    )["emails"])
    candidates = [c for c in candidates if c["email"] in permitidos]
    if not candidates:
        logger.info("Todos os candidatos de e-mail estão bloqueados domain=%s", domain)
        return []

    pattern_row = ep.get_pattern(db, domain)
    catch_all = bool(pattern_row and pattern_row.catch_all)

    verified = {}
    if budget_seconds > 1 and smtp_probe_available():
        results = verify_batch([c["email"] for c in candidates], budget_seconds=budget_seconds)
        verified = {r["email"]: r["status"] for r in results}
        if all(v == "catch_all" for v in verified.values()) and verified:
            ep.record_domain_health(db, domain, catch_all=True)
            catch_all = True

    out = []
    for candidate in candidates:
        status = verified.get(candidate["email"], "unknown")
        confidence = _score_email(candidate, status, catch_all)
        row = repo.add_email(
            db, person, candidate["email"],
            status=status,
            confidence=confidence,
            source=candidate["source"],
            pattern=candidate["pattern"],
        )
        if status == "valid":
            # Confirmação real → ensina o domínio inteiro
            ep.learn_from_email(db, domain, candidate["email"], person.full_name, source="smtp")
        if row and confidence > 0 and status != "invalid":
            out.append(row)
    return out


# ── Etapa: telefones ─────────────────────────────────────────────────────────

def _resolve_phones(db: Session, person: Person, company: Optional[Company]) -> List[dict]:
    """
    Sem provedor pago não existe celular pessoal — e prometer isso seria mentir.
    O que entregamos é o telefone da EMPRESA, marcado como tal.
    """
    if not company:
        return []
    out = []
    for item in (company.phones or [])[:3]:
        if not item.get("e164"):
            continue
        source = item.get("source") or "site"
        row = repo.add_phone(
            db, person,
            e164=item["e164"],
            formatted=item.get("formatted"),
            type_="company",
            confidence=int(item.get("confidence") or CONF_COMPANY_PHONE),
            source=source,
        )
        if row:
            out.append(row)
    return out


# ── Ponto de entrada ─────────────────────────────────────────────────────────

def reveal(db: Session, person: Person, company: Optional[Company] = None,
           kind: str = "both", budget_seconds: float = 12.0) -> dict:
    """
    Revela contatos da pessoa. Devolve:
      {emails, phones, chain, from_cache, blocked, found_email, found_phone}

    Não commita e não cobra crédito — quem chama (router) decide isso.
    """
    started = time.monotonic()
    chain: List[str] = []
    domain = (person.company_domain or (company.domain if company else "") or "").lower()

    if optout.is_blocked(db, "linkedin", person.linkedin_slug or ""):
        return {"emails": [], "phones": [], "chain": ["optout"], "from_cache": False,
                "blocked": True, "found_email": False, "found_phone": False}

    # 1. Cache — o caminho normal depois que a base amadurece
    if repo.has_fresh_contacts(person):
        chain.append("cache")
        return _payload(db, person, chain, from_cache=True)

    # 2. Contexto da empresa (padrão de e-mail, telefone, CNPJ)
    if domain:
        remaining = budget_seconds - (time.monotonic() - started)
        company = ensure_company_context(
            db, domain,
            company_name=person.company_name_raw,
            budget_seconds=min(7.0, max(2.0, remaining - 5)),
        ) or company
        chain.append("company_context")

    # 3. E-mails: padrão + verificação
    if kind in ("email", "both") and domain:
        remaining = budget_seconds - (time.monotonic() - started)
        emails = _resolve_emails(db, person, domain, budget_seconds=max(0.0, min(8.0, remaining)))
        if emails:
            chain.append("pattern+smtp" if smtp_probe_available() else "pattern")

    # 4. Telefones (empresa)
    if kind in ("phone", "both"):
        phones = _resolve_phones(db, person, company)
        if phones:
            chain.append("company_phone")

    # 5. Provedores pagos — desligados enquanto não houver chave configurada
    if not repo.best_email(person) or not repo.best_phone(person):
        premium = premium_find_contacts(
            full_name=person.full_name,
            domain=domain,
            company_name=person.company_name_raw,
            linkedin_url=(f"https://www.linkedin.com/in/{person.linkedin_slug}"
                          if person.linkedin_slug else None),
        )
        if premium:
            chain.append(premium.get("provider", "premium"))
            for item in premium.get("emails", []):
                repo.add_email(db, person, item["email"], status=item.get("status", "unknown"),
                               confidence=int(item.get("confidence", 80)),
                               source=premium.get("provider", "premium"))
            for item in premium.get("phones", []):
                repo.add_phone(db, person, item["e164"], formatted=item.get("formatted"),
                               type_=item.get("type", "mobile"),
                               confidence=int(item.get("confidence", 80)),
                               source=premium.get("provider", "premium"))

    logger.info(
        "reveal person=%s domain=%s chain=%s elapsed=%.2fs",
        person.dedupe_key, domain, "→".join(chain), time.monotonic() - started,
    )
    return _payload(db, person, chain, from_cache=False)


def _payload(db: Session, person: Person, chain: List[str], from_cache: bool) -> dict:
    """Monta a resposta já filtrada pelo opt-out (LGPD)."""
    emails = [e for e in repo.sorted_emails(person) if (e.confidence or 0) > 0]
    phones = repo.sorted_phones(person)

    allowed = optout.filter_contacts(
        db,
        emails=[e.email for e in emails],
        phones=[p.e164 for p in phones],
        linkedin=person.linkedin_slug,
    )
    if allowed["person_blocked"]:
        return {"emails": [], "phones": [], "chain": chain + ["optout"], "from_cache": from_cache,
                "blocked": True, "found_email": False, "found_phone": False}

    email_out = [
        {
            "email": e.email,
            "status": e.status,
            "confidence": e.confidence or 0,
            "source": e.source,
            "pattern": e.pattern,
        }
        for e in emails if e.email in allowed["emails"]
    ]
    phone_out = [
        {
            "e164": p.e164,
            "formatted": p.formatted,
            "type": p.type,
            "confidence": p.confidence or 0,
            "source": p.source,
            "is_company_phone": p.type == "company",
        }
        for p in phones if p.e164 in allowed["phones"]
    ]
    return {
        "emails": email_out,
        "phones": phone_out,
        "chain": chain,
        "from_cache": from_cache,
        "blocked": False,
        "found_email": bool(email_out),
        "found_phone": bool(phone_out),
    }


def preview(db: Session, person: Person) -> dict:
    """
    Prévia SEM rede e SEM cobrar: o que já sabemos, mascarado.
    É o que o painel mostra antes do clique em "Revelar".
    """
    data = _payload(db, person, chain=["preview"], from_cache=True)
    email = data["emails"][0] if data["emails"] else None
    phone = data["phones"][0] if data["phones"] else None

    domain = person.company_domain
    pattern_row = ep.get_pattern(db, domain) if domain else None
    likely = bool(pattern_row and pattern_row.pattern) or bool(domain and has_mx(domain))

    return {
        "blocked": data["blocked"],
        "email": {
            "masked": mask_email(email["email"]) if email else None,
            "has": bool(email),
            "confidence": email["confidence"] if email else 0,
            "status": email["status"] if email else None,
        },
        "phone": {
            "masked": mask_phone(phone["formatted"] or phone["e164"]) if phone else None,
            "has": bool(phone),
            "confidence": phone["confidence"] if phone else 0,
            "is_company_phone": phone["is_company_phone"] if phone else None,
        },
        "likely_findable": likely,
        "known_pattern": bool(pattern_row and pattern_row.pattern),
    }


def find_company_decision_makers(db: Session, company: Company, limit: int = 10) -> List[Person]:
    """Decisores já conhecidos da empresa, ordenados por poder de decisão."""
    if not company:
        return []
    people = (
        db.query(Person)
        .filter(Person.company_domain == company.domain)
        .order_by(Person.last_seen_at.desc())
        .limit(60)
        .all()
    )
    ranked = sorted(
        people,
        key=lambda p: (repo.decision_rank(p), -(p.id or 0)),
    )
    return ranked[:limit]
