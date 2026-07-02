"""
Orquestrador principal: recebe domínio, executa coletas em paralelo e consolida.

Etapa 1 (paralela): scraping do site, DNS report completo
Etapa 2 (depende do scraping): LinkedIn search com company_name conhecido
Etapa 3 (depende do LinkedIn): employee_count via cascata multi-fonte
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from .scraper import scrape_website
from .dns_lookup import get_dns_report
from .linkedin_search import find_company_linkedin, validate_linkedin
from .employee_count import fetch_employee_count, normalize_employee_count
from ._utils import normalize_domain

logger = logging.getLogger(__name__)


def enrich_company(domain_input: str) -> dict:
    """
    Recebe um domínio (ex: 'nubank.com.br') e retorna dict consolidado.
    """
    t0 = time.monotonic()
    domain = normalize_domain(domain_input)
    website_url = f"https://{domain}"
    logger.info("Starting enrichment domain=%s", domain)

    result = {
        "raw_input_domain": domain_input,
        "domain": domain,
        "website": website_url,
        "linkedin_url": None,
        "linkedin_confidence": None,
        "company_name": None,
        "description": None,
        "location": None,
        "sector": None,
        "corporate_email": None,
        "phone": None,
        "mx_provider": None,
        "mx_provider_confidence": None,
        "mx_records": [],
        "dns_report": None,
        "hosting_provider": None,
        "employee_count": None,
        "employee_count_linkedin": None,
        "status": "enriched",
    }

    # Etapa 1: scraping + DNS em paralelo
    site_data = None
    dns_data = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(scrape_website, website_url): "site",
            pool.submit(get_dns_report, domain): "dns",
        }
        for fut in as_completed(futures):
            kind = futures[fut]
            try:
                data = fut.result()
            except Exception as e:
                logger.warning("Stage1 %s failed for domain=%s: %s", kind, domain, e)
                continue
            if kind == "site":
                site_data = data
            elif kind == "dns":
                dns_data = data

    # Consolida site
    if site_data and isinstance(site_data, dict):
        for key in ("company_name", "description", "location", "sector",
                    "corporate_email", "phone", "linkedin_url"):
            if site_data.get(key):
                result[key] = site_data[key]

    # Consolida DNS
    if dns_data and isinstance(dns_data, dict):
        result["mx_provider"] = dns_data.get("mx_provider")
        result["mx_provider_confidence"] = dns_data.get("mx_provider_confidence")
        result["mx_records"] = dns_data.get("mx", [])
        result["hosting_provider"] = dns_data.get("hosting_provider")
        result["dns_report"] = dns_data

    # Etapa 2: LinkedIn search (precisa de company_name idealmente)
    company_name = result.get("company_name")
    linkedin_data = find_company_linkedin(domain, company_name)
    if linkedin_data and linkedin_data.get("url"):
        # Se já tinha do site e bate o slug, mantém o do site (mais confiável);
        # caso contrário, usa o da busca
        if not result.get("linkedin_url"):
            result["linkedin_url"] = linkedin_data["url"]
            result["linkedin_confidence"] = linkedin_data["confidence"]
        else:
            # Já tinha — valida o que veio do site
            result["linkedin_confidence"] = validate_linkedin(result["linkedin_url"], domain)
    else:
        if result.get("linkedin_url"):
            result["linkedin_confidence"] = validate_linkedin(result["linkedin_url"], domain)

    # Etapa 3: employee count (cascata multi-fonte; aba People tem prioridade)
    emp_data = fetch_employee_count(result.get("linkedin_url"), website_url)
    if emp_data:
        result["employee_count"] = emp_data
        # Contagem exata da aba People — armazenada separadamente
        if emp_data.get("source") == "linkedin_people" and emp_data.get("exact"):
            result["employee_count_linkedin"] = emp_data["exact"]
        elif emp_data.get("exact"):
            result["employee_count_linkedin"] = emp_data["exact"]
    elif site_data and site_data.get("employee_count"):
        # fallback: número cru extraído do JSON-LD do site
        normalized = normalize_employee_count(site_data["employee_count"])
        if normalized:
            normalized["source"] = "site_jsonld"
            result["employee_count"] = normalized
            if normalized.get("exact"):
                result["employee_count_linkedin"] = normalized["exact"]

    # Status final
    coletados = sum(1 for k in ("company_name", "linkedin_url", "mx_provider", "description") if result.get(k))
    if coletados == 0:
        result["status"] = "failed"
    elif coletados < 2:
        result["status"] = "partial"

    elapsed = time.monotonic() - t0
    logger.info("Enrichment done domain=%s status=%s elapsed=%.2fs", domain, result["status"], elapsed)

    return result
