"""
Lusha Prospecting — listar contatos de uma empresa e revelar sob demanda.

Separado de `lusha.py` de propósito: é outra API, com outro modelo de custo e
outro fluxo. O `lusha.py` revela UMA pessoa que já conhecemos pelo nome; aqui
descobrimos QUEM existe na empresa, e só depois — sob clique explícito — se
gasta crédito para revelar e-mail ou telefone.

## Por que duas etapas

    search   lista contatos (nome, cargo, LinkedIn, localização)   1 crédito / 25
    enrich   revela e-mail e telefone dos IDs escolhidos           1 / e-mail
                                                                   5 / telefone

Mostrar 25 contatos custa 1 crédito. Revelar o telefone dos mesmos 25 custaria
125. Por isso a tela popula pelo `search` e revela um contato por vez, nunca em
lote — o crédito é do usuário e ele precisa escolher gastar.

Essa é a mesma divisão que a extensão da Lusha faz: os cards já aparecem com
nome e cargo (baratos), e cada um tem um botão que dispara o enrich (caro).

## O erro que este módulo corrige

A implementação anterior (`lusha.find_company_contacts`) chamava `/v2/company`,
que enriquece dados firmográficos — setor, tamanho, receita — e não devolve
lista de pessoas. Como a extração era tolerante, a função devolvia None em
silêncio e parecia "não achou contatos" quando na verdade a chamada estava
conceitualmente errada. Daí a regra que este módulo segue: parser escrito
contra resposta real capturada em fixture, não contra formato suposto.

## Contrato

Nada aqui levanta exceção. Lusha fora do ar, sem crédito, em rate limit ou
respondendo algo que não reconhecemos → `None`, e a tela cai para o caminho
gratuito. Uma integração paga não pode derrubar a funcionalidade que existe
sem ela.
"""
import logging
from typing import Any, Dict, List, Optional

import requests

from services.providers.lusha import CONF_LUSHA, _digits_to_e164, _PHONE_TYPE_MAP

logger = logging.getLogger(__name__)

_BASE = "https://api.lusha.com"
_SEARCH_URL = f"{_BASE}/v3/contacts/prospecting"
_ENRICH_URL = f"{_BASE}/v3/contacts/enrich"
_USAGE_URL = f"{_BASE}/v3/account/usage"

_TIMEOUT = 12

#: Confirmado em 2026-09-07 contra o OpenAPI oficial (V3PaginationRequest):
#: size aceita 10..100, default 25. A tentativa anterior (10..50, default 20)
#: vinha da ferramenta MCP, não da API REST — não conferia.
PAGE_SIZE_MIN = 10
PAGE_SIZE_MAX = 100
PAGE_SIZE_DEFAULT = 25

#: Confirmado em 2026-09-07 contra o OpenAPI oficial (V3ContactsEnrichRequest):
#: minItems 1, maxItems 100. A constante anterior (50) vinha da ferramenta MCP,
#: que era o menor dos dois valores quando as fontes discordavam — a REST é
#: quem manda.
ENRICH_MAX_IDS = 100


# ── Vocabulário da API (verificado em 06/09/2026, conta premium) ────────────
#
# Estes valores vieram de `prospecting_contact_filters` na API real, não da
# documentação. Ficam aqui como constantes para a sidebar não precisar gastar
# requisição para desenhar os próprios filtros.

#: Senioridade: ID → rótulo. A ORDEM importa — é a ordem em que a extensão da
#: Lusha exibe, e os IDs não são sequenciais nessa ordem. Gerar por loop
#: produziria uma lista errada; por isso o mapa é literal.
SENIORITY: List[Dict[str, Any]] = [
    {"id": 10, "label": "founder",        "pt": "Fundador"},
    {"id": 7,  "label": "partner",        "pt": "Sócio"},
    {"id": 9,  "label": "c-suite",        "pt": "Diretoria executiva"},
    {"id": 8,  "label": "vice president", "pt": "Vice-presidente"},
    {"id": 6,  "label": "director",       "pt": "Diretor"},
    {"id": 5,  "label": "manager",        "pt": "Gerente"},
    {"id": 4,  "label": "senior",         "pt": "Sênior"},
    {"id": 3,  "label": "entry",          "pt": "Início de carreira"},
    {"id": 2,  "label": "intern",         "pt": "Estagiário"},
    {"id": 1,  "label": "other",          "pt": "Outros"},
]

_SENIORITY_BY_ID = {s["id"]: s for s in SENIORITY}

#: Departamentos: strings exatas, não IDs. Mandar uma variação ("Engineering"
#: em vez de "Engineering & Technical") não dá erro — dá zero resultado, que é
#: pior porque parece "a empresa não tem ninguém".
DEPARTMENTS: List[str] = [
    "Business Development",
    "Consulting",
    "Customer Service",
    "Engineering & Technical",
    "Finance",
    "General Management",
    "Health Care & Medical",
    "Human Resources",
    "Information Technology",
    "Legal",
    "Marketing",
    "Operations",
    "Other",
    "Product",
    "Research & Analytics",
    "Sales",
]

#: Pontos de dados: serve para filtrar ("só quem tem celular") e é a origem dos
#: badges de contagem no card.
DATA_POINTS: List[str] = [
    "phone",
    "direct_phone",
    "mobile_phone",
    "unknown_phone",
    "no_dnc_phone",
    "email",
    "work_email",
    "private_email",
]

#: Custo em créditos por ação, e a quantidade que esse custo cobre.
PRICING: Dict[str, Dict[str, int]] = {
    "contactSearch":       {"credits": 1, "per": 25},
    "revealEmail":         {"credits": 1, "per": 1},
    "revealPhone":         {"credits": 5, "per": 1},
    "companySearch":       {"credits": 1, "per": 25},
    "revealCompany":       {"credits": 1, "per": 1},
    "showSignalsContact":  {"credits": 1, "per": 1},
}


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "api_key": api_key.strip(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _classificar_falha(resp: requests.Response, contexto: str) -> None:
    """
    Loga o status de um jeito que diga ao operador o que fazer.

    A distinção entre 402 e 429 não é cosmética: sem crédito o usuário precisa
    comprar, em rate limit ele precisa esperar. Colapsar os dois em "erro da
    Lusha" faria a tela dar o conselho errado na metade dos casos.
    """
    if resp.status_code == 401:
        logger.warning("Lusha recusou a chave (401) em %s.", contexto)
    elif resp.status_code == 402:
        logger.warning("Lusha sem créditos (402) em %s — o usuário precisa comprar.", contexto)
    elif resp.status_code == 429:
        logger.warning("Lusha em rate limit (429) em %s — o usuário precisa esperar.", contexto)
    elif resp.status_code == 451:
        logger.info("Lusha bloqueou por GDPR (451) em %s.", contexto)
    else:
        logger.info("Lusha respondeu %s em %s.", resp.status_code, contexto)


def erro_legivel(status: Optional[int]) -> str:
    """Mensagem para a tela. Fica aqui para backend e front não divergirem."""
    return {
        401: "A Lusha recusou sua chave. Reconecte em Configurações.",
        402: "Sua conta Lusha está sem créditos.",
        429: "Limite de requisições da Lusha atingido. Tente em alguns minutos.",
        451: "Este contato não pode ser revelado por restrição de privacidade (GDPR).",
    }.get(status or 0, "A Lusha não respondeu agora. Tente novamente em instantes.")


def limites_da_resposta(resp: requests.Response) -> Dict[str, Any]:
    """
    Lê os limites de uso dos headers da resposta.

    Existe porque o plano da conta do usuário não é conhecido: premium tem
    300/min, mas Free/Starter tem 40/min. Assumir o teto alto faria a tela
    prometer uma folga que a conta dele não tem.
    """
    def _int(nome: str) -> Optional[int]:
        try:
            return int(resp.headers[nome])
        except (KeyError, TypeError, ValueError):
            return None

    return {
        "minuto":     {"limite": _int("x-rate-limit-minute"), "restante": _int("x-minute-requests-left")},
        "hora":       {"limite": _int("x-rate-limit-hourly"), "restante": _int("x-hourly-requests-left")},
        "dia":        {"limite": _int("x-rate-limit-daily"),  "restante": _int("x-daily-requests-left")},
    }


# ── Parser ──────────────────────────────────────────────────────────────────
#
# Um único ponto de tradução "resposta da Lusha → nosso formato". Centralizado
# porque foi a dispersão da extração que escondeu o erro anterior: quando cada
# campo é procurado num lugar diferente, uma resposta completamente errada
# ainda produz um dicionário plausível. Aqui, se o formato mudar, quebra a
# fixture de teste — que é o comportamento desejado.

def _nome(c: Dict[str, Any]) -> Optional[str]:
    for chave in ("fullName", "name"):
        v = c.get(chave)
        if isinstance(v, str) and v.strip():
            return v.strip()
    partes = [c.get("firstName"), c.get("lastName")]
    junto = " ".join(p.strip() for p in partes if isinstance(p, str) and p.strip())
    return junto or None


def _job_title_obj(c: Dict[str, Any]) -> Dict[str, Any]:
    """
    `jobTitle` é um objeto aninhado na resposta real — `{title, departments,
    seniority}` — não uma string solta na raiz do contato. Confirmado em
    2026-09-07 contra o OpenAPI oficial (V3ContactPreview / resposta do
    enrich). A suposição anterior (`jobTitle` como string) nunca foi testada
    contra uma resposta real.
    """
    v = c.get("jobTitle")
    return v if isinstance(v, dict) else {}


def _cargo(c: Dict[str, Any]) -> Optional[str]:
    v = _job_title_obj(c).get("title")
    if isinstance(v, str) and v.strip():
        return v.strip()
    # Fallback para formatos antigos/hipotéticos, caso um endpoint irmão
    # (ex.: search-and-enrich) devolva o cargo solto na raiz.
    for chave in ("currentTitle", "title", "position"):
        v = c.get(chave)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _empresa(c: Dict[str, Any]) -> Dict[str, Any]:
    bruto = c.get("company")
    if not isinstance(bruto, dict):
        bruto = {}
    nome = bruto.get("name") or c.get("currentCompany") or c.get("companyName")
    dominio = bruto.get("domain") or c.get("currentDomain") or c.get("companyDomain")
    # Campo real é `industry`, string singular — não `industries` (lista).
    # Aceita ambos: singular vira lista de um item, para o resto do código
    # (que trata setores como lista, para as tags "Finanças, Bancos +2") não
    # mudar de forma.
    setores = bruto.get("industry") or bruto.get("industries") or c.get("companyIndustries")
    if isinstance(setores, str):
        setores = [setores]
    if not isinstance(setores, list):
        setores = []
    return {
        "nome": nome if isinstance(nome, str) else None,
        "dominio": dominio if isinstance(dominio, str) else None,
        "setores": [s for s in setores if isinstance(s, str)],
    }


def _localizacao(c: Dict[str, Any]) -> Optional[str]:
    """
    Localizacao como texto pronto para a tela.

    Substitui o "Sao Paulo, Brazil" que estava chumbado no JS — um rotulo fixo
    que estava errado para todo contato fora de Sao Paulo, ou seja, para a
    maioria.
    """
    v = c.get("location")
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, dict):
        partes = [v.get("city"), v.get("state"), v.get("country")]
    else:
        partes = [c.get("city"), c.get("state"), c.get("country")]
    partes = [p.strip() for p in partes if isinstance(p, str) and p.strip()]
    # Sem cidade nem pais nao ha o que mostrar; devolver string vazia faria a
    # tela desenhar uma linha em branco.
    return ", ".join(dict.fromkeys(partes)) or None


def _linkedin_url(c: Dict[str, Any]) -> Optional[str]:
    # Campo real é socialLinks.linkedin (objeto aninhado) — não linkedinUrl
    # solto na raiz, que era suposição nunca verificada.
    social = c.get("socialLinks")
    if isinstance(social, dict):
        v = social.get("linkedin")
        if isinstance(v, str) and v.strip():
            return v.strip()
    for chave in ("linkedinUrl", "linkedin_url"):
        v = c.get(chave)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _can_reveal(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    `canReveal[]` e a fonte da verdade sobre o botao de revelar: diz quais
    campos aquele contato permite revelar e quanto cada um custa. Pedir no
    enrich um campo que nao esta aqui e 400 garantido.
    """
    bruto = c.get("canReveal")
    if not isinstance(bruto, list):
        return []
    out = []
    for item in bruto:
        if isinstance(item, dict) and isinstance(item.get("field"), str):
            out.append({
                "field": item["field"],
                "credits": item.get("credits") if isinstance(item.get("credits"), int) else None,
            })
        elif isinstance(item, str):
            out.append({"field": item, "credits": None})
    return out


def _data_points(c: Dict[str, Any]) -> Dict[str, int]:
    """
    Presença por campo, para os badges do card.

    Confirmado em 2026-09-07 contra o OpenAPI oficial (V3ContactPreview.has):
    é uma lista de NOMES DE CAMPO presentes no perfil (ex.: "firstName",
    "jobTitle", "emails", "phones") — não uma contagem por quantidade de
    telefone/e-mail. A suposição original ("📱² = dois celulares") não é o que
    a API garante; cada campo aparece no máximo uma vez em `has`. A função
    ainda devolve um mapa nome→contagem por compatibilidade com o card, mas na
    prática todo valor vem 1 (presente) — o badge de contagem por quantidade
    fica sem suporte confirmado até um teste com resposta real dizer o
    contrário. Mantém suporte a mapa nome→contagem para não regredir se algum
    dia a API mudar para isso.
    """
    bruto = c.get("has") or c.get("dataPoints") or c.get("existingDataPoints")
    if isinstance(bruto, dict):
        return {k: v for k, v in bruto.items() if isinstance(k, str) and isinstance(v, int)}
    if isinstance(bruto, list):
        contagem: Dict[str, int] = {}
        for item in bruto:
            if isinstance(item, str):
                contagem[item] = contagem.get(item, 0) + 1
            elif isinstance(item, dict):
                nome = item.get("name") or item.get("type") or item.get("field")
                if isinstance(nome, str):
                    qtd = item.get("count")
                    contagem[nome] = qtd if isinstance(qtd, int) else contagem.get(nome, 0) + 1
        return contagem
    return {}


def _senioridade(c: Dict[str, Any]) -> Optional[str]:
    # Campo real é jobTitle.seniority — já vem como rótulo em texto
    # (ex.: "Manager", "Vice President"), não como um dos IDs 1-10 da
    # constante SENIORITY (esses IDs só existem do lado do filtro de busca).
    v = _job_title_obj(c).get("seniority")
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, int):
        item = _SENIORITY_BY_ID.get(v)
        return item["label"] if item else None
    if isinstance(v, dict):
        nome = v.get("name") or v.get("label")
        return nome.strip() if isinstance(nome, str) and nome.strip() else None
    # Fallback: raiz do contato, caso outro endpoint devolva solto.
    v = c.get("seniority")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _departamento(c: Dict[str, Any]) -> Optional[str]:
    # Campo real é jobTitle.departments (lista) — mostramos o primeiro.
    v = _job_title_obj(c).get("departments")
    if isinstance(v, list) and v:
        primeiro = v[0]
        if isinstance(primeiro, str) and primeiro.strip():
            return primeiro.strip()
    # Fallback: raiz do contato, caso outro endpoint devolva solto.
    v = c.get("department") or c.get("departments")
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, dict):
        v = v.get("name") or v.get("label")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _emails_revelados(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    bruto = c.get("emails")
    if not isinstance(bruto, list):
        return []
    out, vistos = [], set()
    for item in bruto:
        endereco = item.get("email") if isinstance(item, dict) else item
        if not isinstance(endereco, str) or "@" not in endereco:
            continue
        endereco = endereco.strip().lower()
        if endereco in vistos:
            continue
        vistos.add(endereco)
        out.append({
            "email": endereco,
            "status": "unknown",
            "confidence": CONF_LUSHA,
            "type": (item.get("emailType") or item.get("type")) if isinstance(item, dict) else None,
            "dataSource": item.get("dataSource") if isinstance(item, dict) else None,
        })
    return out


def _telefones_revelados(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    bruto = c.get("phones")
    if not isinstance(bruto, list):
        return []
    out, vistos = [], set()
    for item in bruto:
        if isinstance(item, dict):
            numero = item.get("number") or item.get("phone")
        else:
            numero = item
        if not isinstance(numero, str):
            continue
        e164 = _digits_to_e164(numero)
        if not e164 or e164 in vistos:
            continue
        vistos.add(e164)
        tipo_bruto = ""
        if isinstance(item, dict):
            tipo_bruto = str(item.get("phoneType") or item.get("type") or "")
        out.append({
            "e164": e164,
            "formatted": numero,
            "type": _PHONE_TYPE_MAP.get(tipo_bruto.strip().lower(), "unknown"),
            "confidence": CONF_LUSHA,
            "dataSource": item.get("dataSource") if isinstance(item, dict) else None,
        })
    # Celular primeiro: e exatamente o que o caminho gratuito nunca entrega.
    out.sort(key=lambda p: 0 if p["type"] == "mobile" else 1)
    return out


def parse_contact(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Um contato da Lusha no nosso formato. `None` se nao der para identificar a
    pessoa — sem ID nao ha como revelar depois, e sem nome nao ha o que
    mostrar no card.
    """
    if not isinstance(c, dict):
        return None
    contact_id = c.get("id") or c.get("contactId")
    nome = _nome(c)
    if not isinstance(contact_id, str) or not contact_id.strip() or not nome:
        return None

    empresa = _empresa(c)
    return {
        "lusha_contact_id": contact_id.strip(),
        "name": nome,
        "title": _cargo(c),
        "linkedin_url": _linkedin_url(c),
        "location": _localizacao(c),
        "department": _departamento(c),
        "seniority": _senioridade(c),
        "company_name": empresa["nome"],
        "company_domain": empresa["dominio"],
        "company_industries": empresa["setores"],
        "can_reveal": _can_reveal(c),
        "data_points": _data_points(c),
        "emails": _emails_revelados(c),
        "phones": _telefones_revelados(c),
    }


def _lista_de_contatos(payload: Any) -> List[Dict[str, Any]]:
    """Onde a resposta guarda o array de pessoas."""
    if isinstance(payload, list):
        return [c for c in payload if isinstance(c, dict)]
    if not isinstance(payload, dict):
        return []
    for chave in ("data", "contacts", "results"):
        v = payload.get(chave)
        if isinstance(v, list):
            return [c for c in v if isinstance(c, dict)]
    return []


def _total(payload: Any, achados: int) -> int:
    """
    Total de contatos que a empresa tem na base da Lusha, para a paginacao.
    Sem ele a tela nao sabe se existe proxima pagina.
    """
    if isinstance(payload, dict):
        for chave in ("total", "totalResults", "totalCount"):
            v = payload.get(chave)
            if isinstance(v, int) and v >= 0:
                return v
        meta = payload.get("meta") or payload.get("pagination")
        if isinstance(meta, dict):
            for chave in ("total", "totalResults", "totalCount"):
                v = meta.get(chave)
                if isinstance(v, int) and v >= 0:
                    return v
    return achados


# ── Rede ────────────────────────────────────────────────────────────────────
#
# Sobre o parametro `erro_out`: as funcoes devolvem None em qualquer falha,
# como manda o contrato — a tela precisa degradar para o caminho gratuito sem
# saber de nada. Mas o endpoint de revelacao PRECISA distinguir 402 de 429,
# porque a acao do usuario e diferente (comprar credito vs esperar). Passar um
# dicionario em `erro_out` e o jeito de obter esse detalhe sem transformar o
# retorno em uniao de tipos, e sem guardar estado no modulo (que quebraria com
# duas requisicoes simultaneas).


def _registrar(erro_out: Optional[Dict[str, Any]], status: Optional[int]) -> None:
    if erro_out is None:
        return
    erro_out["status"] = status
    erro_out["mensagem"] = erro_legivel(status)


def search_contacts(
    api_key: str,
    company_domains: List[str],
    *,
    page: int = 0,
    page_size: int = PAGE_SIZE_DEFAULT,
    job_titles: Optional[List[str]] = None,
    seniority_ids: Optional[List[int]] = None,
    departments: Optional[List[str]] = None,
    countries: Optional[List[str]] = None,
    existing_data_points: Optional[List[str]] = None,
    erro_out: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Lista contatos de uma ou mais empresas. Custa 1 credito por 25 resultados.

    Devolve {"contacts": [...], "total": int, "page": int, "page_size": int,
    "limites": {...}} ou None em qualquer falha. Nunca levanta excecao.

    Nao revela e-mail nem telefone — isso e o `enrich_contacts`. Um contato aqui
    vem com `can_reveal`, dizendo o que da para revelar e por quanto.
    """
    if not api_key or not api_key.strip():
        return None

    dominios = [d.strip().lower() for d in (company_domains or []) if isinstance(d, str) and d.strip()]
    if not dominios:
        # A API exige ao menos um filtro. Chamar sem nenhum e 400 — e a
        # cobranca de credito acontece mesmo quando o resultado nao serve.
        logger.info("Lusha search sem dominio: nada a buscar.")
        return None

    # Validar ANTES da rede: page_size fora de 10..50 e 400 garantido, e um 400
    # e uma ida a Lusha que nao devolve nada e ainda pode cobrar.
    if not isinstance(page_size, int) or not (PAGE_SIZE_MIN <= page_size <= PAGE_SIZE_MAX):
        logger.warning(
            "Lusha search: page_size %r fora do intervalo %d..%d.",
            page_size, PAGE_SIZE_MIN, PAGE_SIZE_MAX,
        )
        return None
    if not isinstance(page, int) or page < 0:
        logger.warning("Lusha search: page %r invalida (deve ser >= 0).", page)
        return None

    # Confirmado em 2026-09-07 contra o OpenAPI oficial: o domínio de empresa
    # fica em filters.companies.include.domains (não filters.companies.domains
    # direto), e os filtros de contato ficam em filters.contacts.include.{...}
    # (não soltos em filters.contacts). A primeira tentativa, sem os níveis
    # "include", devolvia 400 "property X should not exist" da própria Lusha —
    # é exatamente o tipo de erro silencioso que a §11 do documento avisava.
    filtros: Dict[str, Any] = {"companies": {"include": {"domains": dominios}}}
    contatos: Dict[str, Any] = {}
    if job_titles:
        contatos["jobTitles"] = [t for t in job_titles if isinstance(t, str) and t.strip()]
    if seniority_ids:
        # IDs desconhecidos sao descartados em vez de repassados: a Lusha
        # rejeitaria a requisicao inteira por causa de um valor solto.
        # Campo real é `seniorityIds` — o nome `seniority` era suposição.
        contatos["seniorityIds"] = [s for s in seniority_ids if s in _SENIORITY_BY_ID]
    if departments:
        contatos["departments"] = [d for d in departments if d in DEPARTMENTS]
    if countries:
        contatos["countries"] = [c.strip().upper() for c in countries if isinstance(c, str) and c.strip()]
    if existing_data_points:
        validos = [p for p in existing_data_points if p in DATA_POINTS]
        if validos:
            # camelCase confirmado (`existingDataPoints`), não snake_case.
            contatos["existingDataPoints"] = validos
            contatos["existingDataPointsCondition"] = "or"
    if contatos:
        filtros["contacts"] = {"include": contatos}

    # Campo de paginação é `pagination`, não `pages` — a Lusha recusava com
    # 400 "property pages should not exist".
    corpo = {"filters": filtros, "pagination": {"page": page, "size": page_size}}

    try:
        resp = requests.post(
            _SEARCH_URL, json=corpo, headers=_headers(api_key), timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.info("Lusha indisponivel no search: %s", e)
        _registrar(erro_out, None)
        return None

    if resp.status_code != 200:
        _classificar_falha(resp, "search")
        _registrar(erro_out, resp.status_code)
        return None

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("Lusha search devolveu 200 com corpo que nao e JSON.")
        _registrar(erro_out, None)
        return None

    brutos = _lista_de_contatos(payload)
    contatos_ok = [p for p in (parse_contact(c) for c in brutos) if p]

    return {
        "contacts": contatos_ok,
        "total": _total(payload, len(contatos_ok)),
        "page": page,
        "page_size": page_size,
        "limites": limites_da_resposta(resp),
        "request_id": payload.get("requestId") if isinstance(payload, dict) else None,
    }


def enrich_contacts(
    api_key: str,
    contact_ids: List[str],
    *,
    reveal: Optional[List[str]] = None,
    waterfall_enabled: Optional[bool] = None,
    erro_out: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Revela e-mails/telefones dos IDs dados. Custa 1 credito por e-mail e 5 por
    telefone, por contato — e por isso nunca deve ser chamado em lote pela
    tela, so sob clique explicito de quem vai gastar o proprio credito.

    Devolve {"contacts": [...], "limites": {...}} ou None. Nunca levanta.

    `reveal` limita os campos (["emails"] | ["phones"]); omitir revela tudo que
    estiver em `canReveal`. Pedir um campo que nao estava la e 400.
    """
    if not api_key or not api_key.strip():
        return None

    ids = [i.strip() for i in (contact_ids or []) if isinstance(i, str) and i.strip()]
    if not ids:
        return None
    # Checado antes da rede: passar do teto e 400 certo, e um 400 depois de
    # montar a requisicao e ida perdida.
    if len(ids) > ENRICH_MAX_IDS:
        logger.warning(
            "Lusha enrich: %d IDs excede o teto de %d — recusado antes da rede.",
            len(ids), ENRICH_MAX_IDS,
        )
        return None

    if reveal is not None:
        reveal = [r for r in reveal if r in ("emails", "phones")]
        if not reveal:
            logger.warning("Lusha enrich: `reveal` sem campo valido — recusado antes da rede.")
            return None

    # Campo confirmado é `ids`, não `contactIds` — a documentação inicial (via
    # ferramenta MCP) dizia `contactIds`; o OpenAPI oficial (V3ContactsEnrichRequest)
    # diz `ids`. `requestId` também não existe nesse request (só em tableId,
    # reveal, waterfallEnabled) — foi removido daqui.
    corpo: Dict[str, Any] = {"ids": ids}
    if reveal:
        corpo["reveal"] = reveal
    if waterfall_enabled is not None:
        corpo["waterfallEnabled"] = bool(waterfall_enabled)

    try:
        resp = requests.post(
            _ENRICH_URL, json=corpo, headers=_headers(api_key), timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.info("Lusha indisponivel no enrich: %s", e)
        _registrar(erro_out, None)
        return None

    if resp.status_code != 200:
        _classificar_falha(resp, "enrich")
        _registrar(erro_out, resp.status_code)
        return None

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("Lusha enrich devolveu 200 com corpo que nao e JSON.")
        _registrar(erro_out, None)
        return None

    brutos = _lista_de_contatos(payload)
    contatos_ok = [p for p in (parse_contact(c) for c in brutos) if p]

    return {
        "contacts": contatos_ok,
        "limites": limites_da_resposta(resp),
    }


def get_account_usage(
    api_key: str,
    erro_out: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Saldo de creditos e uso do periodo. NAO consome credito — existe para a
    tela avisar antes de o usuario descobrir que acabou no meio de uma
    revelacao.
    """
    if not api_key or not api_key.strip():
        return None

    try:
        resp = requests.get(_USAGE_URL, headers=_headers(api_key), timeout=_TIMEOUT)
    except requests.RequestException as e:
        logger.info("Lusha indisponivel no usage: %s", e)
        _registrar(erro_out, None)
        return None

    if resp.status_code != 200:
        _classificar_falha(resp, "usage")
        _registrar(erro_out, resp.status_code)
        return None

    try:
        payload = resp.json()
    except ValueError:
        _registrar(erro_out, None)
        return None
    if not isinstance(payload, dict):
        return None

    # Formato real (AccountUsageResponse, confirmado 2026-09-07 contra o
    # OpenAPI oficial): créditos e limites vêm no CORPO da resposta —
    # `credits.{total,used,remaining}` e `rateLimits.{daily,hourly,minute}.
    # {limit,used,remaining,resetsAt}` — não nos headers `x-*-requests-left`
    # como a implementação original assumia (esses headers existem nas
    # respostas de search/enrich, não necessariamente aqui). Mantém
    # `limites_da_resposta()` como fallback caso o corpo não traga a seção.
    def _num(fonte: Any, *chaves: str) -> Optional[int]:
        if not isinstance(fonte, dict):
            return None
        for chave in chaves:
            v = fonte.get(chave)
            if isinstance(v, int):
                return v
        return None

    credits = payload.get("credits")
    rate_limits = payload.get("rateLimits")

    def _janela(nome: str) -> Dict[str, Optional[int]]:
        bruto = rate_limits.get(nome) if isinstance(rate_limits, dict) else None
        return {
            "limite": _num(bruto, "limit"),
            "usado": _num(bruto, "used"),
            "restante": _num(bruto, "remaining"),
        }

    limites = {
        "minuto": _janela("minute"),
        "hora": _janela("hourly"),
        "dia": _janela("daily"),
    }
    # Se o corpo não trouxe rateLimits (conta sem esse detalhamento), cai para
    # os headers da própria resposta — mesma ideia, fonte diferente.
    if not any(v.get("restante") is not None for v in limites.values()):
        limites = limites_da_resposta(resp)

    return {
        "creditos_restantes": _num(credits, "remaining"),
        "creditos_usados": _num(credits, "used"),
        "creditos_total": _num(credits, "total"),
        "plano": payload.get("plan") if isinstance(payload.get("plan"), dict) else None,
        "precos": payload.get("pricing") if isinstance(payload.get("pricing"), dict) else None,
        "limites": limites,
        "bruto": payload,
    }
