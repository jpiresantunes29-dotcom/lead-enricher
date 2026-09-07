"""
Normalização de identidade: nome, cargo, empresa.

Precisão aqui é o que impede duas coisas caras:
  - gerar e-mail errado (nome mal separado → palpite inútil)
  - duplicar a mesma pessoa no banco (dedupe_key estável)

Sem dependências externas e sem rede.
"""
import hashlib
import re
import unicodedata
from typing import Optional, Tuple

# Partículas que não fazem parte do sobrenome usado em e-mail corporativo
_PARTICLES = {
    "de", "da", "do", "das", "dos", "e", "del", "della", "di", "du",
    "van", "von", "der", "den", "la", "le", "el", "bin", "al",
}

# Sufixos honoríficos/ruído que aparecem colados no nome do LinkedIn
_NAME_NOISE = re.compile(
    r"\b(jr|junior|júnior|filho|neto|sobrinho|ii|iii|iv|phd|ph\.d|mba|msc|"
    r"m\.sc|bsc|b\.sc|cfa|cpa|pmp|mr|mrs|ms|dr|dra|prof)\b\.?",
    re.IGNORECASE,
)

# Emojis, badges e selos que o LinkedIn deixa o usuário colar no nome
_NAME_SYMBOLS = re.compile(r"[^\w\s\-'.]", re.UNICODE)

# Separadores entre "Nome" e o resto do título da página / headline
_HEADLINE_SPLIT = re.compile(r"\s+[\-–—|·•@]\s+|\s+\|\s+")

_AT_COMPANY = re.compile(
    r"\b(?:at|na|no|em|@|da|do)\s+([A-Z0-9][\w&.,'’\- ]{1,60})",
    re.IGNORECASE,
)

# ── Cargo → senioridade ──────────────────────────────────────────────────────
# Ordem importa: o primeiro match vence (mais sênior primeiro).
_SENIORITY_RULES = [
    ("founder", (
        "founder", "co-founder", "cofounder", "fundador", "fundadora",
        "co-fundador", "sócio-fundador", "socio-fundador", "owner", "proprietário",
        "proprietario", "dono", "titular",
    )),
    ("c_level", (
        "ceo", "cto", "cfo", "coo", "cmo", "cro", "cpo", "ciso", "cio", "cdo",
        "chief", "presidente", "president", "diretor-presidente", "sócio",
        "socio", "partner", "managing director", "diretor geral", "diretor-geral",
    )),
    ("vp", ("vp", "vice president", "vice-presidente", "vice presidente", "svp", "evp")),
    ("director", ("diretor", "diretora", "director", "superintendente")),
    ("head", ("head of", "head de", "head", "chefe de", "líder de", "lider de", "lead")),
    ("manager", (
        "gerente", "manager", "coordenador", "coordenadora", "supervisor",
        "supervisora", "gestor", "gestora",
    )),
]

_DEPARTMENT_RULES = [
    ("tech", (
        "cto", "cio", "ciso", "tecnologia", "technology", "engenharia", "engineering",
        "ti", "it ", "software", "dados", "data", "infraestrutura", "infrastructure",
        "desenvolvimento", "development", "produto", "product", "cpo", "digital",
        "sistemas", "segurança da informação", "seguranca da informacao",
    )),
    ("sales", (
        "comercial", "vendas", "sales", "cro", "revenue", "negócios", "negocios",
        "business development", "bd ", "account executive", "key account",
        "expansão", "expansao", "parcerias", "partnership",
    )),
    ("marketing", ("marketing", "cmo", "growth", "brand", "comunicação", "comunicacao", "conteúdo", "conteudo")),
    ("finance", (
        "financeiro", "finance", "cfo", "controladoria", "controller", "contábil",
        "contabil", "tesouraria", "fp&a", "administrativo-financeiro",
    )),
    ("hr", ("rh", "recursos humanos", "people", "human resources", "gente", "talent", "recrutamento", "chro")),
    ("ops", ("operações", "operacoes", "operations", "coo", "logística", "logistica", "supply", "produção", "producao")),
    ("legal", ("jurídico", "juridico", "legal", "compliance", "regulatório", "regulatorio")),
    ("procurement", ("compras", "procurement", "suprimentos", "sourcing")),
]

# Peso de decisão: usado para ordenar quem aparece primeiro no painel
SENIORITY_RANK = {
    "founder": 1, "c_level": 1, "vp": 2, "director": 3,
    "head": 4, "manager": 5, "other": 9,
}


def strip_accents(value: str) -> str:
    """'João Gonçalves' → 'Joao Goncalves' (base de tudo que vira e-mail)."""
    if not value:
        return ""
    nfkd = unicodedata.normalize("NFKD", value)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def clean_name(raw: str) -> str:
    """
    Limpa o nome como o LinkedIn entrega: emojis, credenciais, pronomes,
    '(He/Him)', '· 3rd', 'MBA', etc.
    """
    if not raw:
        return ""
    name = raw.strip()
    # Remove tudo entre parênteses/colchetes (pronomes, apelidos, cidade)
    name = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", name)
    # Corta o grau de conexão do LinkedIn ("· 2nd", "• 3rd+")
    name = re.sub(r"[·•]\s*\d(?:st|nd|rd|th)\+?.*$", " ", name, flags=re.IGNORECASE)
    # Corta credenciais depois de vírgula ("Ana Souza, MBA, PMP")
    name = name.split(",")[0]
    name = _NAME_SYMBOLS.sub(" ", name)
    name = _NAME_NOISE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" -.'")
    return name


def split_name(full_name: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Devolve (first, last) já sem acentos e em minúsculas, prontos para e-mail.
    Ignora partículas ('João da Silva' → ('joao', 'silva')).
    """
    cleaned = clean_name(full_name)
    ascii_name = strip_accents(cleaned).lower()
    tokens = [t for t in re.split(r"[^a-z]+", ascii_name) if len(t) > 1]
    if not tokens:
        return None, None
    meaningful = [t for t in tokens if t not in _PARTICLES]
    if not meaningful:
        meaningful = tokens
    first = meaningful[0]
    last = meaningful[-1] if len(meaningful) > 1 else None
    return first, last


def name_tokens(full_name: str) -> list:
    """Todos os tokens úteis do nome (para casar sobrenome composto)."""
    ascii_name = strip_accents(clean_name(full_name)).lower()
    return [t for t in re.split(r"[^a-z]+", ascii_name) if len(t) > 1 and t not in _PARTICLES]


def title_seniority(title: str) -> str:
    """Classifica o cargo em founder|c_level|vp|director|head|manager|other."""
    if not title:
        return "other"
    t = f" {strip_accents(title).lower()} "
    for level, keywords in _SENIORITY_RULES:
        for kw in keywords:
            kw_ascii = strip_accents(kw)
            # Siglas curtas exigem borda de palavra para não casar dentro de outra
            if len(kw_ascii) <= 4:
                if re.search(rf"\b{re.escape(kw_ascii)}\b", t):
                    return level
            elif kw_ascii in t:
                return level
    return "other"


def title_department(title: str) -> str:
    """Classifica a área do cargo (tech, sales, finance...)."""
    if not title:
        return "other"
    t = f" {strip_accents(title).lower()} "
    for dept, keywords in _DEPARTMENT_RULES:
        for kw in keywords:
            kw_ascii = strip_accents(kw)
            if len(kw_ascii) <= 3:
                if re.search(rf"\b{re.escape(kw_ascii)}\b", t):
                    return dept
            elif kw_ascii in t:
                return dept
    return "other"


def is_decision_maker(title: str) -> bool:
    """Cargo com poder de decisão (founder → manager)."""
    return title_seniority(title) != "other"


def parse_headline(headline: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Separa headline do LinkedIn em (cargo, empresa).

    'CTO at Acme Tecnologia'      → ('CTO', 'Acme Tecnologia')
    'Diretor Comercial na Acme'   → ('Diretor Comercial', 'Acme')
    'CEO | Acme | Investidor'     → ('CEO', 'Acme')
    """
    if not headline:
        return None, None
    text = re.sub(r"\s+", " ", headline).strip()

    m = _AT_COMPANY.search(text)
    if m:
        title = text[:m.start()].strip(" -–—|·•,")
        company = m.group(1).strip(" -–—|·•,.")
        company = re.split(r"\s+[|·•]\s+", company)[0].strip()
        return (title or None), (company or None)

    parts = [p.strip() for p in _HEADLINE_SPLIT.split(text) if p.strip()]
    if len(parts) >= 2:
        return parts[0] or None, parts[1] or None
    return (parts[0] if parts else None), None


def extract_title(headline: str, fallback: Optional[str] = None) -> Optional[str]:
    """Melhor palpite de cargo a partir da headline."""
    title, _ = parse_headline(headline)
    return title or fallback


def linkedin_slug(url_or_slug: str) -> Optional[str]:
    """Extrai/normaliza o identificador público do perfil."""
    if not url_or_slug:
        return None
    value = url_or_slug.strip()
    m = re.search(r"linkedin\.com/in/([a-zA-Z0-9\-._%]+)", value, re.IGNORECASE)
    slug = m.group(1) if m else value
    slug = slug.split("?")[0].split("/")[0].strip().lower()
    slug = re.sub(r"[^a-z0-9\-._%]", "", slug)
    return slug or None


def dedupe_key(linkedin_slug_value: Optional[str], full_name: Optional[str],
               company_domain: Optional[str]) -> Optional[str]:
    """
    Chave estável de identidade:
      1. slug do LinkedIn (não muda e é único)
      2. hash de nome normalizado + domínio da empresa
    Sem nenhum dos dois, a pessoa não é identificável — devolve None.
    """
    if linkedin_slug_value:
        return f"li:{linkedin_slug_value}"
    tokens = name_tokens(full_name or "")
    if not tokens or not company_domain:
        return None
    base = f"{'-'.join(tokens)}@{company_domain.lower()}"
    return "nm:" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def display_name(full_name: str) -> str:
    """Nome limpo em Title Case, preservando partículas em minúscula."""
    cleaned = clean_name(full_name)
    if not cleaned:
        return ""
    out = []
    for token in cleaned.split():
        low = strip_accents(token).lower()
        out.append(low if low in _PARTICLES and out else token[:1].upper() + token[1:])
    return " ".join(out)
