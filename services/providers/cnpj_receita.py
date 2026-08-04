"""
Dados públicos da Receita Federal por CNPJ — gratuito, legal e exclusivo do BR.

Entrega, sem custo:
  - razão social, nome fantasia, porte, CNAE, situação cadastral
  - telefone(s) e e-mail cadastrados da empresa
  - QSA (quadro societário): os NOMES dos sócios administradores, que em PME
    são exatamente os decisores que o vendedor quer

Fontes (cascata, todas gratuitas e sem cadastro). Se uma cair ou bater limite,
a próxima assume — nenhuma delas tem SLA, então redundância aqui é o que
garante que o dado chegue:
  1. BrasilAPI       — sem limite rígido publicado
  2. CNPJá Open      — payload mais limpo (marca pessoa física vs. jurídica)
  3. publica.cnpj.ws — ~3 req/min
  4. ReceitaWS       — ~3 req/min

LGPD: guardamos apenas nome e qualificação do sócio. CPF (mesmo mascarado),
faixa etária e data de entrada são descartados — são dados pessoais que não
servem ao propósito comercial e só aumentariam o passivo.
"""
import logging
import re
from functools import lru_cache
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

_BRASILAPI_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
_CNPJA_URL = "https://open.cnpja.com/office/{cnpj}"
_CNPJWS_URL = "https://publica.cnpj.ws/cnpj/{cnpj}"
_RECEITAWS_URL = "https://receitaws.com.br/v1/cnpj/{cnpj}"
_TIMEOUT = 8

CNPJ_RE = re.compile(r"\b(\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2})\b")

# Qualificações do QSA que indicam poder de decisão
_DECISION_QUALIFICATIONS = (
    "administrador", "sócio-administrador", "socio-administrador",
    "diretor", "presidente", "sócio", "socio", "titular", "proprietário",
    "proprietario", "gerente-delegado",
)

# Marcadores de pessoa jurídica no nome do sócio (não é gente)
_LEGAL_ENTITY_MARKERS = (
    " ltda", " s.a", " s/a", " sa", " eireli", " me", " epp", " mei",
    "participacoes", "participações", "holding", "empreendimentos",
    " s.s", "sociedade", " inc", " llc", " corp",
)


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def is_valid_cnpj(cnpj: str) -> bool:
    """Valida os dois dígitos verificadores. Evita gastar request com lixo."""
    digits = _only_digits(cnpj)
    if len(digits) != 14 or digits == digits[0] * 14:
        return False

    def check_digit(base: str) -> str:
        weights = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-len(base):]
        total = sum(int(d) * w for d, w in zip(base, weights))
        rest = total % 11
        return "0" if rest < 2 else str(11 - rest)

    return digits[12] == check_digit(digits[:12]) and digits[13] == check_digit(digits[:13])


def extract_cnpj(text: str) -> Optional[str]:
    """Encontra o primeiro CNPJ válido em um texto (rodapé de site, etc.)."""
    if not text:
        return None
    for match in CNPJ_RE.finditer(text):
        candidate = _only_digits(match.group(1))
        if is_valid_cnpj(candidate):
            return candidate
    return None


def _clean_socio_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())


def _is_person(name: str) -> bool:
    low = f" {(name or '').lower()} "
    if any(marker in low for marker in _LEGAL_ENTITY_MARKERS):
        return False
    return len(_clean_socio_name(name).split()) >= 2


def _normalize_brasilapi(data: dict, cnpj: str) -> dict:
    phones = []
    for key in ("ddd_telefone_1", "ddd_telefone_2"):
        raw = (data.get(key) or "").strip()
        if raw:
            phones.append(raw)

    qsa = []
    for socio in data.get("qsa") or []:
        nome = _clean_socio_name(socio.get("nome_socio") or "")
        if not nome:
            continue
        qsa.append({
            "nome": nome,
            "qualificacao": (socio.get("qualificacao_socio") or "").strip(),
        })

    return {
        "cnpj": cnpj,
        "razao_social": data.get("razao_social"),
        "nome_fantasia": data.get("nome_fantasia") or None,
        "situacao": data.get("descricao_situacao_cadastral"),
        "porte": data.get("porte"),
        "cnae": data.get("cnae_fiscal_descricao"),
        "abertura": data.get("data_inicio_atividade"),
        "capital_social": data.get("capital_social"),
        "municipio": data.get("municipio"),
        "uf": data.get("uf"),
        "bairro": data.get("bairro"),
        "cep": data.get("cep"),
        "phones": phones,
        "email": (data.get("email") or "").strip().lower() or None,
        "qsa": qsa,
        "source": "brasilapi",
    }


def _normalize_cnpja(data: dict, cnpj: str) -> dict:
    """CNPJá Open — o único que marca explicitamente pessoa física (NATURAL)."""
    company = data.get("company") or {}
    phones = []
    for phone in data.get("phones") or []:
        area, number = (phone.get("area") or "").strip(), (phone.get("number") or "").strip()
        if number:
            phones.append(f"{area}{number}")

    emails = [e.get("address") for e in (data.get("emails") or []) if e.get("address")]

    qsa = []
    for member in company.get("members") or []:
        person = member.get("person") or {}
        nome = _clean_socio_name(person.get("name") or "")
        if not nome:
            continue
        qsa.append({
            "nome": nome,
            "qualificacao": ((member.get("role") or {}).get("text") or "").strip(),
            # Marcação nativa da fonte: melhor que adivinhar pelo nome
            "pessoa_fisica": person.get("type") == "NATURAL",
        })

    address = data.get("address") or {}
    return {
        "cnpj": cnpj,
        "razao_social": company.get("name"),
        "nome_fantasia": data.get("alias") or None,
        "situacao": (data.get("status") or {}).get("text"),
        "porte": ((company.get("size") or {}).get("text")),
        "cnae": (data.get("mainActivity") or {}).get("text"),
        "abertura": data.get("founded"),
        "capital_social": company.get("equity"),
        "municipio": address.get("city"),
        "uf": address.get("state"),
        "bairro": address.get("district"),
        "cep": address.get("zip"),
        "phones": phones,
        "email": (emails[0].lower() if emails else None),
        "emails": [e.lower() for e in emails],
        "qsa": qsa,
        "source": "cnpja",
    }


def _normalize_cnpjws(data: dict, cnpj: str) -> dict:
    est = data.get("estabelecimento") or {}
    phones = []
    for ddd_key, tel_key in (("ddd1", "telefone1"), ("ddd2", "telefone2")):
        ddd, tel = (est.get(ddd_key) or "").strip(), (est.get(tel_key) or "").strip()
        if tel:
            phones.append(f"{ddd}{tel}")

    qsa = []
    for socio in data.get("socios") or []:
        nome = _clean_socio_name(socio.get("nome") or "")
        if not nome:
            continue
        qualificacao = socio.get("qualificacao_socio")
        if isinstance(qualificacao, dict):
            qualificacao = qualificacao.get("descricao")
        qsa.append({
            "nome": nome,
            "qualificacao": (qualificacao or "").strip(),
            "pessoa_fisica": "física" in (socio.get("tipo") or "").lower()
                             or "fisica" in (socio.get("tipo") or "").lower(),
        })

    return {
        "cnpj": cnpj,
        "razao_social": data.get("razao_social"),
        "nome_fantasia": est.get("nome_fantasia") or None,
        "situacao": est.get("situacao_cadastral"),
        "porte": (data.get("porte") or {}).get("descricao"),
        "cnae": (est.get("atividade_principal") or {}).get("descricao"),
        "abertura": est.get("data_inicio_atividade"),
        "capital_social": data.get("capital_social"),
        "municipio": (est.get("cidade") or {}).get("nome"),
        "uf": (est.get("estado") or {}).get("sigla"),
        "bairro": est.get("bairro"),
        "cep": est.get("cep"),
        "phones": phones,
        "email": (est.get("email") or "").strip().lower() or None,
        "qsa": qsa,
        "source": "cnpjws",
    }


def _normalize_receitaws(data: dict, cnpj: str) -> dict:
    phones = [p.strip() for p in re.split(r"[/;]", data.get("telefone") or "") if p.strip()]
    qsa = []
    for socio in data.get("qsa") or []:
        nome = _clean_socio_name(socio.get("nome") or "")
        if nome:
            qsa.append({"nome": nome, "qualificacao": (socio.get("qual") or "").strip()})

    atividade = (data.get("atividade_principal") or [{}])
    return {
        "cnpj": cnpj,
        "razao_social": data.get("nome"),
        "nome_fantasia": data.get("fantasia") or None,
        "situacao": data.get("situacao"),
        "porte": data.get("porte"),
        "cnae": atividade[0].get("text") if atividade else None,
        "abertura": data.get("abertura"),
        "capital_social": data.get("capital_social"),
        "municipio": data.get("municipio"),
        "uf": data.get("uf"),
        "bairro": data.get("bairro"),
        "cep": data.get("cep"),
        "phones": phones,
        "email": (data.get("email") or "").strip().lower() or None,
        "qsa": qsa,
        "source": "receitaws",
    }


def _fetch_receitaws(url: str, digits: int, timeout: int):
    """ReceitaWS devolve 200 com status=ERROR quando o CNPJ não existe."""
    resp = requests.get(url, timeout=timeout, headers={"Accept": "application/json"})
    if resp.status_code != 200:
        return None
    data = resp.json()
    return None if data.get("status") == "ERROR" else data


# (nome, url, parser, precisa de tratamento especial)
_SOURCES = [
    ("brasilapi", _BRASILAPI_URL, _normalize_brasilapi, None),
    ("cnpja", _CNPJA_URL, _normalize_cnpja, None),
    ("cnpjws", _CNPJWS_URL, _normalize_cnpjws, None),
    ("receitaws", _RECEITAWS_URL, _normalize_receitaws, _fetch_receitaws),
]


@lru_cache(maxsize=256)
def lookup_cnpj(cnpj: str, timeout: int = _TIMEOUT) -> Optional[dict]:
    """
    Consulta o CNPJ na cascata gratuita, parando na primeira que responder.
    Cacheado em memória — o mesmo CNPJ não é consultado duas vezes por processo.
    """
    digits = _only_digits(cnpj)
    if not is_valid_cnpj(digits):
        return None

    for name, url_template, parser, custom_fetch in _SOURCES:
        url = url_template.format(cnpj=digits)
        try:
            if custom_fetch:
                data = custom_fetch(url, digits, timeout)
            else:
                resp = requests.get(url, timeout=timeout, headers={"Accept": "application/json"})
                data = resp.json() if resp.status_code == 200 else None
            if data:
                result = parser(data, digits)
                logger.info("CNPJ %s resolvido via %s", digits, name)
                return result
        except (requests.RequestException, ValueError) as e:
            logger.debug("Fonte %s falhou cnpj=%s: %s", name, digits, e)
            continue

    logger.info("CNPJ %s não resolvido em nenhuma fonte gratuita", digits)
    return None


def clean_qualification(value: str) -> str:
    """'16-Presidente' → 'Presidente' (a Receita prefixa o código da qualificação)."""
    return re.sub(r"^\s*\d{1,3}\s*[-–]\s*", "", value or "").strip()


def decision_makers_from_qsa(cnpj_data: dict, limit: int = 6) -> List[dict]:
    """
    Converte o quadro societário em decisores utilizáveis.
    Filtra pessoas jurídicas e ordena administradores antes de sócios passivos.
    """
    if not cnpj_data:
        return []

    out = []
    for socio in cnpj_data.get("qsa") or []:
        nome = socio.get("nome") or ""
        qualificacao = clean_qualification(socio.get("qualificacao") or "")
        # A fonte, quando informa o tipo, é mais confiável que a heurística
        pessoa_fisica = socio.get("pessoa_fisica")
        if pessoa_fisica is False:
            continue
        if pessoa_fisica is None and not _is_person(nome):
            continue
        qual_low = qualificacao.lower()
        if qual_low and not any(q in qual_low for q in _DECISION_QUALIFICATIONS):
            continue
        rank = 0 if any(k in qual_low for k in ("administrador", "presidente", "diretor")) else 1
        out.append({
            "name": nome.title(),
            "title": qualificacao or "Sócio",
            "rank": rank,
            "source": "cnpj_qsa",
            # Registro público da Receita: associação empresa→pessoa é oficial
            "confidence": 95,
        })

    out.sort(key=lambda s: s["rank"])
    for item in out:
        item.pop("rank", None)
    return out[:limit]


def company_phones(cnpj_data: dict) -> List[str]:
    """Telefones cadastrados na Receita (formato bruto, ex.: '1132654000')."""
    return list(cnpj_data.get("phones") or []) if cnpj_data else []


def location_from_cnpj(cnpj_data: dict) -> Optional[str]:
    """
    'Município, UF' a partir do endereço registrado na Receita.

    A Receita devolve o município em caixa alta ("BARUERI"); na ficha do lead
    isso lê como erro, então volta para caixa de título.
    """
    if not cnpj_data:
        return None
    municipio = (cnpj_data.get("municipio") or "").strip()
    uf = (cnpj_data.get("uf") or "").strip().upper()
    if municipio.isupper():
        municipio = municipio.title()
    if municipio and uf:
        return f"{municipio}, {uf}"
    return municipio or uf or None


def sector_from_cnpj(cnpj_data: dict) -> Optional[str]:
    """Setor a partir do CNAE principal declarado na Receita."""
    return (cnpj_data or {}).get("cnae") or None


# Porte da Receita -> faixa aproximada de funcionários. O porte classifica
# FATURAMENTO (LC 123/2006), não pessoal, então isto já é aproximação: os
# tetos vêm do critério de pessoal ocupado do SEBRAE, deliberadamente largos.
# "DEMAIS" não entra: não tem teto superior — cabe ali tanto uma empresa de
# 100 quanto uma de 50.000 funcionários, e um número desses seria invenção.
_PORTE_BANDS = (
    ("micro", "1-19 (estimado pelo porte)"),
    ("pequeno", "10-99 (estimado pelo porte)"),
)


def employee_band_from_cnpj(cnpj_data: dict) -> Optional[dict]:
    """
    Faixa grosseira de funcionários a partir do porte fiscal da Receita.

    Último recurso, quando o LinkedIn não deu contagem nenhuma. Nunca
    preenche min/max/exact de propósito: a tela e o Excel mostram esses
    campos como número puro, sem a ressalva de que é estimativa — só `band`
    e `raw` chegam ao usuário com o texto que se autodeclara aproximado.
    """
    porte = ((cnpj_data or {}).get("porte") or "").strip()
    if not porte:
        return None
    lowered = porte.lower()
    for marker, label in _PORTE_BANDS:
        if marker in lowered:
            return {
                "raw": f"Porte na Receita Federal: {porte}",
                "min": None, "max": None, "exact": None,
                "band": label,
                "source": "cnpj_porte",
            }
    return None
