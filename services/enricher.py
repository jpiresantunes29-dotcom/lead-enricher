"""
Orquestrador principal: recebe domínio, executa coletas em paralelo e consolida.

Etapa 1 (paralela): scraping do site, DNS report completo
Etapa 2 (depende do scraping): LinkedIn search com company_name conhecido
Etapa 3 (depende do LinkedIn): employee_count via cascata multi-fonte
Etapa 2b (paralela às 2/3, depende do CNPJ achado na etapa 1): localização e
          setor oficiais via Receita Federal — cobre o caso comum de site
          institucional sem dados estruturados (JSON-LD, meta geo.*). O CNPJ
          vem do site quando ele o publica e, quando não, do titular do
          domínio no registro.br.
"""
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from .scraper import scrape_website
from .dns_lookup import get_dns_report
from .linkedin_search import find_company_linkedin, inspect_company_page
from .employee_count import fetch_employee_count, normalize_employee_count
from .providers.cnpj_receita import (
    lookup_cnpj, location_from_cnpj, sector_from_cnpj, employee_band_from_cnpj,
    is_valid_cnpj,
)
from ._utils import normalize_domain
from .dns_intel import fetch_rdap

logger = logging.getLogger(__name__)

# Teto de tempo da busca inteira. A função serverless da Vercel morre em 60 s
# sem devolver nada — é melhor entregar a ficha parcial do que perder tudo.
ENRICH_BUDGET_SECONDS = int(os.getenv("ENRICH_BUDGET_SECONDS", "50"))

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
#   5 — mx_provider só é aceito com confiança "high"; linkedin_url só com
#       confiança "verified" (site/sede/funcionários derivados da página só
#       entram junto com o LinkedIn verificado) — dado não confirmado agora
#       fica vazio em vez de aparecer como palpite
#   6 — LinkedIn "probable" passa a ser aceito (com a confiança real marcada
#       na ficha, não só "verified"); orçamento total e o gatilho de busca do
#       LinkedIn aumentados para caber mais tentativas antes do teto da Vercel
#   7 — páginas /school/ (universidade, faculdade, escola) passam a ser
#       reconhecidas, e o vínculo com o domínio deixa de aceitar substring:
#       "pucpr.br" não é mais confirmado por "hotmilk.pucpr.br". Juntas, as
#       duas coisas punham na ficha da PUCPR o LinkedIn do hub de inovação
#       enquanto o link certo, publicado na home, era descartado por não ser
#       /company/. Junto: a contagem vinda do site deixa de ler formulário
#       (o "19 colaboradores" da PUCPR saía de um <option> "Microempresa (até
#       19 colaboradores)") e passa a preferir o numberOfEmployees declarado
#       em JSON-LD. Ficha do v6 é recoletada.
#   8 — localização e setor oficiais deixam de depender de o site publicar o
#       próprio CNPJ: o registro.br publica o do TITULAR de qualquer domínio
#       .br, e ele entra como fallback. O scraper também passa a seguir os
#       links institucionais do próprio site em vez de só adivinhar caminhos
#       (/sobre, /contato), que não existem na maioria dos sites grandes.
#       A metatag geo.* do site cai para último recurso na localização:
#       boticario.com.br declara Simferopol (Crimeia) no template e isso
#       entrava por cima da sede correta da Receita e do LinkedIn.
#   9 — o setor deixa de sair por ordem fixa de fonte: rótulo que descreve a
#       administração (holding, sede, apoio administrativo) cede a vez para o
#       que descreve o negócio, venha ele do LinkedIn ou da Receita.
#  10 — o setor passa a sair primeiro do CNAE da Receita (atividade que a
#       empresa registra para operar) e só depois do rótulo do LinkedIn, que
#       é escolhido a dedo numa lista curta e erra muito: "apoio a edifícios"
#       para o Grupo Positivo, "bem-estar e condicionamento físico" para a
#       Unimed Curitiba.
ENRICHMENT_VERSION = 10


# Rótulos de setor que descrevem a ADMINISTRAÇÃO de uma empresa, não o que
# ela vende. Aparecem dos dois lados: no CNAE, quando o CNPJ consultado é o da
# holding ou da unidade administrativa; no LinkedIn, quando o perfil foi
# cadastrado por quem cuidava do prédio e não do negócio.
#
# Só entram aqui rótulos que NENHUMA empresa usaria para explicar seu mercado a
# um cliente. "Serviços combinados para apoio a edifícios" fica de fora de
# propósito: é genérico para o Grupo Positivo e exato para uma empresa de
# facilities — e cegar esse rótulo tiraria o setor certo de quem vive dele.
_SETORES_GENERICOS = (
    "sedes de empresas",
    "atividades de sedes",
    "unidades administrativas",
    "holdings de instituicoes",
    "holdings de instituições",
    "gestao de ativos intangiveis",
    "gestão de ativos intangíveis",
    "servicos combinados de escritorio",
    "serviços combinados de escritório",
    "apoio administrativo",
    "aluguel de imoveis proprios",
    "aluguel de imóveis próprios",
    "nao especificadas anteriormente",
    "não especificadas anteriormente",
)


def setor_generico(valor) -> bool:
    """
    O rótulo descreve a estrutura societária em vez do negócio?

    Serve para escolher ENTRE as fontes, não para apagar o campo: um setor
    genérico ainda é melhor que nenhum, e só perde quando existe alternativa
    que diga o que a empresa faz.
    """
    if not valor:
        return False
    texto = " ".join(str(valor).lower().split())
    return any(marcador in texto for marcador in _SETORES_GENERICOS)


def _melhor_setor(*candidatos) -> str:
    """
    Primeiro setor que descreve o negócio; se todos forem genéricos, o
    primeiro que existir.

    Os argumentos vêm na ordem de confiança, e ela é **Receita → LinkedIn →
    site**. O CNAE é a atividade que a empresa REGISTRA para operar; o
    "setor" do LinkedIn é escolhido numa lista curta por quem montou o perfil,
    e erra com frequência. Medido em 9 domínios reais:

        positivo.com.br   LinkedIn "apoio a edifícios"   CNAE "Ensino médio"
        unimedcuritiba    LinkedIn "bem-estar e fitness" CNAE "Planos de saúde"
        sanepar.com.br    LinkedIn "eletricidade, gás"   CNAE "Captação e
                                                          tratamento de água"

    Nos três o CNAE é o certo — e nos dois casos em que o CNAE é que era
    administrativo (Boticário, Nubank) quem corrige é a regra do genérico
    logo acima, devolvendo a vez ao LinkedIn. As duas travas juntas é que
    dão o setor certo em qualquer domínio.
    """
    validos = [c for c in candidatos if c]
    for candidato in validos:
        if not setor_generico(candidato):
            return candidato
    return validos[0] if validos else None


def _cnpj_do_titular(rdap_data) -> str:
    """
    CNPJ do titular do domínio, como o registro.br o publica ("76.659.820/0003-13").

    Devolve só dígitos e só se os verificadores baterem — o campo é texto livre
    do RDAP, e um CPF de titular pessoa física cairia aqui do mesmo jeito.
    """
    if not isinstance(rdap_data, dict):
        return ""
    digitos = re.sub(r"\D", "", rdap_data.get("owner_cnpj") or "")
    return digitos if is_valid_cnpj(digitos) else ""


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
        # Preenchido só quando o site recusou o robô (403/429/desafio
        # anti-bot). Campo vazio por bloqueio e campo vazio por ausência de
        # dado são coisas diferentes para quem vai ligar para a empresa.
        "site_block_reason": None,
        "status": "enriched",
    }

    deadline = t0 + ENRICH_BUDGET_SECONDS

    def _remaining(reserve: float = 0.0) -> float:
        """Quanto ainda podemos gastar sem estourar o orçamento da requisição."""
        return max(0.5, deadline - time.monotonic() - reserve)

    # Etapa 1: scraping + DNS + titular do domínio, em paralelo
    site_data = None
    dns_data = None
    rdap_data = None
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(scrape_website, website_url): "site",
            pool.submit(get_dns_report, domain): "dns",
            pool.submit(fetch_rdap, domain): "rdap",
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
            elif kind == "rdap":
                rdap_data = data

    # Consolida site.
    #
    # `location` fica de fora de propósito: no site ela sai das metatags
    # `geo.region`/`geo.placename`, que um site em cada tantos herda do
    # template e nunca corrige. Real e medido: boticario.com.br declara
    # "г.Симферополь, АР Крым" (Simferopol, Crimeia) — e como o site vinha
    # primeiro, isso entrava na ficha por cima da sede que o LinkedIn e a
    # Receita informam corretamente (Curitiba/São José dos Pinhais). A
    # metatag continua valendo, mas como ÚLTIMO recurso, lá embaixo.
    location_do_site = None
    if site_data and isinstance(site_data, dict):
        for key in ("company_name", "description", "sector",
                    "corporate_email", "phone", "linkedin_url", "cnpj"):
            if site_data.get(key):
                result[key] = site_data[key]
        location_do_site = site_data.get("location")
        result["site_emails"] = site_data.get("emails") or []
        result["site_phones"] = site_data.get("phones") or []
        result["site_block_reason"] = site_data.get("block_reason")

    # CNPJ (Receita Federal): fonte oficial de localização e setor. Muita
    # empresa grande não tem JSON-LD/meta geo.* na home (ex.: varejo), então
    # o site sozinho deixa esses dois campos vazios. Dispara em paralelo com
    # as etapas de LinkedIn/funcionários abaixo — só é aguardada no fim, então
    # não soma latência quando o site já respondeu tudo.
    # Dispara sempre que houver CNPJ, não só quando faltam localização/setor:
    # o porte também alimenta a faixa de funcionários, e a consulta já
    # acontecia de qualquer jeito logo depois (waterfall.ingest_enrichment),
    # com cache — antecipá-la não custa requisição nova.
    # Site que não publica o próprio CNPJ deixava localização e setor vazios
    # para sempre — e é a maioria fora do varejo. O registro.br publica o CNPJ
    # do TITULAR de todo domínio .br, então a ficha tem uma segunda porta para
    # o dado oficial. Medido: pucpr.br devolve 76.659.820/0003-13, a Associação
    # Paranaense de Cultura — a mantenedora da universidade.
    #
    # Fica como fallback, nunca por cima do CNPJ achado no site: o titular do
    # domínio é quase sempre a empresa, mas pode ser a holding do grupo ou a
    # agência que registrou. Com os dois disponíveis, o que a empresa publica
    # sobre si mesma vale mais.
    if not result.get("cnpj"):
        do_titular = _cnpj_do_titular(rdap_data)
        if do_titular:
            result["cnpj"] = do_titular
            logger.info("CNPJ obtido do titular do domínio domain=%s", domain)

    setor_da_receita = None
    cnpj_pool = cnpj_future = None
    if result.get("cnpj"):
        cnpj_pool = ThreadPoolExecutor(max_workers=1)
        cnpj_future = cnpj_pool.submit(lookup_cnpj, result["cnpj"], timeout=6)

    # Consolida DNS.
    # mx_provider só é aceito com confiança "high" (hostname MX bate com um
    # padrão conhecido — Google, Microsoft, Locaweb...): "medium" (ASN) e
    # "low" (nome chutado do hostname) são inferência, não certeza, e a ficha
    # do vendedor não pode afirmar um provedor que pode estar errado. Os
    # registros MX crus (mx_records) continuam aparecendo sempre — são fato
    # de DNS, não interpretação.
    if dns_data and isinstance(dns_data, dict):
        if dns_data.get("mx_provider_confidence") == "high":
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
    site_linkedin_url = result.get("linkedin_url")
    found_url = None
    try:
        if site_linkedin_url:
            page = inspect_company_page(site_linkedin_url, domain,
                                        declarado_pelo_site=True,
                                        company_name=company_name)
            found_url = site_linkedin_url
            logger.info("LinkedIn source=site domain=%s", domain)
        elif _remaining() > 5:
            linkedin_data = find_company_linkedin(domain, company_name)
            found_url = (linkedin_data or {}).get("url")
            page = (linkedin_data or {}).get("page")
            # Por qual caminho o LinkedIn apareceu (ou não). É o número que decide
            # se vale contratar busca paga para o que sobrar — sem ele a decisão
            # seria chute.
            logger.info(
                "LinkedIn source=%s domain=%s",
                (linkedin_data or {}).get("source") or "none", domain,
            )
    except Exception as e:
        logger.warning("Etapa LinkedIn falhou domain=%s: %s", domain, e)
        page, found_url = None, None

    # Afirmamos o LinkedIn da empresa com confiança "verified" (a própria
    # página declara o domínio buscado no bloco "Informações") ou "probable"
    # (nome da empresa bate, mas o site declarado não confirma — geralmente
    # porque a página não veio, ou a marca é do grupo controlador). Abaixo
    # disso ("unverified") é chute demais para mostrar: link errado na ficha
    # é pior do que campo vazio. A confiança de verdade fica gravada em
    # linkedin_confidence, então o vendedor sabe o que está olhando.
    if found_url and page and page.get("confidence") in ("verified", "probable"):
        result["linkedin_url"] = found_url
        result["linkedin_confidence"] = page["confidence"]
    else:
        result["linkedin_url"] = None
        result["linkedin_confidence"] = None
        page = None

    # O bloco "Informações" da página do LinkedIn preenche setor e sede sem
    # nenhuma requisição extra — o HTML já está em mãos. É a fonte que cobre a
    # maioria dos casos: site institucional sem dados estruturados e sem CNPJ
    # no rodapé deixaria os dois campos vazios.
    page_html = page["html"] if page else None
    setor_do_linkedin = page["sector"] if page else None
    if page:
        result["location"] = result.get("location") or page["location"]

    # Etapa 3: employee count (cascata multi-fonte; aba People tem prioridade).
    # Sem orçamento sobrando, ainda vale ler a página que já está em mãos — o
    # que não pode é entrar em rede e estourar o limite da função serverless.
    try:
        emp_data = fetch_employee_count(
            result.get("linkedin_url"), website_url,
            page_html=page_html, allow_network=_remaining() > 6,
        )
    except Exception as e:
        logger.warning("Contagem de funcionários falhou domain=%s: %s", domain, e)
        emp_data = None
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
            setor_da_receita = sector_from_cnpj(cnpj_data)
            # Último recurso para funcionários: melhor uma faixa declarada
            # como estimativa do que campo vazio na tela do vendedor.
            if not result.get("employee_count"):
                result["employee_count"] = employee_band_from_cnpj(cnpj_data)

    # Setor decidido com as três fontes em mãos: quem descreve o negócio ganha
    # de quem descreve a administração, seja qual for a origem.
    result["sector"] = _melhor_setor(
        setor_da_receita, setor_do_linkedin, result.get("sector")
    )

    # Nem a Receita nem o LinkedIn responderam: aí sim a metatag do site é
    # melhor que campo vazio — sabendo que ela pode ser lixo de template.
    if not result.get("location") and location_do_site:
        result["location"] = location_do_site

    # "curitiba, parana" está certo e lê como descuido na tela do vendedor.
    # Só mexe quando a string inteira veio em caixa baixa: "Curitiba, PR" tem
    # sigla de UF, e title() a estragaria ("Pr").
    localizacao = result.get("location")
    if localizacao and localizacao.islower():
        result["location"] = localizacao.title()

    # Site fora do ar ou bloqueando robôs: o domínio ainda dá um nome
    # utilizável na ficha — melhor que campo vazio na tela do vendedor.
    if not result.get("company_name"):
        root = domain.split(".")[0].replace("-", " ")
        result["company_name"] = root.title() if root else None

    # Bloqueio só vira aviso na tela quando custou alguma coisa. Se o CNPJ ou
    # outra fonte preencheu setor, localização e descrição assim mesmo, avisar
    # que "o site recusou o robô" seria alarme sobre um problema que a ficha
    # já contornou.
    if result.get("site_block_reason") and all(
        result.get(k) for k in ("sector", "location", "description")
    ):
        result["site_block_reason"] = None

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
