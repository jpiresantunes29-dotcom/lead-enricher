"""
Orquestrador principal: recebe domínio, executa coletas em paralelo e consolida.

Etapa 1 (paralela): scraping do site, DNS report completo
Etapa 2 (depende do scraping): LinkedIn search com company_name conhecido
Etapa 3 (depende do LinkedIn): employee_count via cascata multi-fonte
Etapa 2b (paralela às 2/3, depende do CNPJ achado na etapa 1): localização e
          setor oficiais via Receita Federal — cobre o caso comum de site
          institucional sem dados estruturados (JSON-LD, meta geo.*).
"""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from .scraper import scrape_website
from .dns_lookup import get_dns_report
from .linkedin_search import find_company_linkedin, inspect_company_page
from .employee_count import fetch_employee_count, normalize_employee_count
from .providers.cnpj_receita import (
    lookup_cnpj, location_from_cnpj, sector_from_cnpj, employee_band_from_cnpj,
)
from ._utils import normalize_domain

logger = logging.getLogger(__name__)

# Teto de tempo da busca inteira. A função serverless da Vercel morre em 60 s
# sem devolver nada — é melhor entregar a ficha parcial do que perder tudo.
ENRICH_BUDGET_SECONDS = int(os.getenv("ENRICH_BUDGET_SECONDS", "35"))

# Versão da lógica de coleta. INCREMENTE sempre que uma correção mudar o que
# seria coletado para o mesmo domínio: fichas gravadas por versões anteriores
# deixam de ser servidas do cache e são recoletadas (sem cobrar cota de novo).
# Sem isto, uma correção de precisão só chegaria ao usuário 7 dias depois.
#   2 — slug do LinkedIn com "&"; setor/sede do LinkedIn e da Receita
#   3 — descarta URL do LinkedIn que exige login (ID numérico, /admin) e
#       adivinha o slug público pelo nome da empresa; faixa de funcionários
#       pelo porte da Receita; CNPJ também nas páginas legais do site
#   4 — MX com PTR reverso, ASN, rede e país em todas as linhas (painel
#       "Infraestrutura de e-mail"); NS com IP
ENRICHMENT_VERSION = 4


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
        "enrichment_version": ENRICHMENT_VERSION,
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
        # Matéria-prima do banco de contatos (não persiste em Lead; alimenta
        # Company/EmailPattern via services.people.waterfall.ingest_enrichment)
        "cnpj": None,
        "site_emails": [],
        "site_phones": [],
        "status": "enriched",
    }

    deadline = t0 + ENRICH_BUDGET_SECONDS

    def _remaining(reserve: float = 0.0) -> float:
        """Quanto ainda podemos gastar sem estourar o orçamento da requisição."""
        return max(0.5, deadline - time.monotonic() - reserve)

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
                data = fut.result(timeout=_remaining(reserve=12))
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
                    "corporate_email", "phone", "linkedin_url", "cnpj"):
            if site_data.get(key):
                result[key] = site_data[key]
        result["site_emails"] = site_data.get("emails") or []
        result["site_phones"] = site_data.get("phones") or []

    # CNPJ (Receita Federal): fonte oficial de localização e setor. Muita
    # empresa grande não tem JSON-LD/meta geo.* na home (ex.: varejo), então
    # o site sozinho deixa esses dois campos vazios. Dispara em paralelo com
    # as etapas de LinkedIn/funcionários abaixo — só é aguardada no fim, então
    # não soma latência quando o site já respondeu tudo.
    # Dispara sempre que houver CNPJ, não só quando faltam localização/setor:
    # o porte também alimenta a faixa de funcionários, e a consulta já
    # acontecia de qualquer jeito logo depois (waterfall.ingest_enrichment),
    # com cache — antecipá-la não custa requisição nova.
    cnpj_pool = cnpj_future = None
    if result.get("cnpj"):
        cnpj_pool = ThreadPoolExecutor(max_workers=1)
        cnpj_future = cnpj_pool.submit(lookup_cnpj, result["cnpj"], timeout=6)

    # Consolida DNS
    if dns_data and isinstance(dns_data, dict):
        result["mx_provider"] = dns_data.get("mx_provider")
        result["mx_provider_confidence"] = dns_data.get("mx_provider_confidence")
        result["mx_records"] = dns_data.get("mx", [])
        result["hosting_provider"] = dns_data.get("hosting_provider")
        result["dns_report"] = dns_data

    # Etapa 2: LinkedIn.
    # O link do próprio site é a fonte mais confiável — quando o scraping já
    # trouxe, basta validar. Sair procurando de novo custava ~20 s por empresa
    # para chegar no mesmo lugar (ou em um ID numérico pior).
    #
    # A página pública da empresa é baixada UMA vez: dela saem tanto a
    # validação do vínculo com o domínio quanto a contagem exata de
    # funcionários.
    company_name = result.get("company_name")
    page = None
    if result.get("linkedin_url"):
        page = inspect_company_page(result["linkedin_url"], domain)
        result["linkedin_confidence"] = page["confidence"]
        logger.info("LinkedIn source=site domain=%s", domain)
    elif _remaining() > 8:
        linkedin_data = find_company_linkedin(domain, company_name)
        if linkedin_data and linkedin_data.get("url"):
            result["linkedin_url"] = linkedin_data["url"]
            result["linkedin_confidence"] = linkedin_data["confidence"]
            page = linkedin_data.get("page")
        # Por qual caminho o LinkedIn apareceu (ou não). É o número que decide
        # se vale contratar busca paga para o que sobrar — sem ele a decisão
        # seria chute.
        logger.info(
            "LinkedIn source=%s domain=%s",
            (linkedin_data or {}).get("source") or "none", domain,
        )

    # O bloco "Informações" da página do LinkedIn preenche setor e sede sem
    # nenhuma requisição extra — o HTML já está em mãos. É a fonte que cobre a
    # maioria dos casos: site institucional sem dados estruturados e sem CNPJ
    # no rodapé deixaria os dois campos vazios.
    page_html = page["html"] if page else None
    if page:
        result["sector"] = result.get("sector") or page["sector"]
        result["location"] = result.get("location") or page["location"]

    # Etapa 3: employee count (cascata multi-fonte; aba People tem prioridade).
    # Sem orçamento sobrando, ainda vale ler a página que já está em mãos — o
    # que não pode é entrar em rede e estourar o limite da função serverless.
    emp_data = fetch_employee_count(
        result.get("linkedin_url"), website_url,
        page_html=page_html, allow_network=_remaining() > 6,
    )
    if emp_data:
        result["employee_count"] = emp_data
        # Contagem exata (aba People ou face-pile) — armazenada separadamente
        if emp_data.get("exact"):
            result["employee_count_linkedin"] = emp_data["exact"]
    elif site_data and site_data.get("employee_count"):
        # fallback: número cru extraído do JSON-LD do site
        normalized = normalize_employee_count(site_data["employee_count"])
        if normalized:
            normalized["source"] = "site_jsonld"
            result["employee_count"] = normalized
            if normalized.get("exact"):
                result["employee_count_linkedin"] = normalized["exact"]

    # Resolve o lookup de CNPJ dado tempo para as etapas 2/3 (rodou em paralelo).
    if cnpj_future is not None:
        try:
            cnpj_data = cnpj_future.result(timeout=_remaining())
        except Exception:
            cnpj_data = None
        finally:
            cnpj_pool.shutdown(wait=False)
        if cnpj_data:
            result["location"] = result.get("location") or location_from_cnpj(cnpj_data)
            result["sector"] = result.get("sector") or sector_from_cnpj(cnpj_data)
            # Último recurso para funcionários: melhor uma faixa declarada
            # como estimativa do que campo vazio na tela do vendedor.
            if not result.get("employee_count"):
                result["employee_count"] = employee_band_from_cnpj(cnpj_data)

    # Site fora do ar ou bloqueando robôs: o domínio ainda dá um nome
    # utilizável na ficha — melhor que campo vazio na tela do vendedor.
    if not result.get("company_name"):
        root = domain.split(".")[0].replace("-", " ")
        result["company_name"] = root.title() if root else None

    # Status final — nos 5 campos-alvo do produto (LinkedIn, MX, funcionários,
    # localização, setor). company_name fica de fora: o fallback logo acima
    # sempre o preenche, então nunca fica vazio e não dizia nada sobre o
    # quanto a coleta realmente funcionou (uma ficha só com nome-do-domínio
    # e MX aparecia como "enriquecido").
    campos_alvo = ("linkedin_url", "mx_provider", "employee_count", "location", "sector")
    coletados = sum(1 for k in campos_alvo if result.get(k))
    if coletados == 0:
        result["status"] = "failed"
    elif coletados < 2:
        result["status"] = "partial"

    elapsed = time.monotonic() - t0
    logger.info("Enrichment done domain=%s status=%s elapsed=%.2fs", domain, result["status"], elapsed)

    return result
