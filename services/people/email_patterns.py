"""
Padrão de e-mail por domínio — o motor de custo zero do produto.

Ideia: empresas usam UM formato para todo mundo. Quando um e-mail de
`acme.com.br` é confirmado como `joao.silva@`, aprendemos o formato da empresa
inteira. A partir daí, para qualquer funcionário da Acme, montamos o e-mail
localmente e só confirmamos a existência — sem consultar provedor pago.

Fluxo:
  learn_from_email()  ← alimenta o padrão (site, SMTP confirmado, provedor)
  candidates()        → palpites ordenados, já com confiança calibrada
"""
import logging
import re
from datetime import datetime, UTC
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from models.database import EmailPattern
from .identity import name_tokens, strip_accents

logger = logging.getLogger(__name__)

# ── Catálogo de padrões ──────────────────────────────────────────────────────
# Ordenado por frequência real de mercado (BR + geral). Usado quando o domínio
# ainda é desconhecido.
PATTERNS: List[str] = [
    "{first}.{last}",
    "{first}",
    "{f}{last}",
    "{first}{last}",
    "{first}_{last}",
    "{f}.{last}",
    "{first}{l}",
    "{first}.{l}",
    "{last}.{first}",
    "{first}-{last}",
    "{last}{first}",
    "{last}",
    "{f}{l}",
    "{last}.{f}",
]

# Confiança do palpite quando o domínio é desconhecido (antes de verificar).
_COLD_CONFIDENCE = [45, 38, 34, 32, 28, 26, 24, 22, 20, 18, 16, 15, 14, 12]

# Caixas funcionais: nunca representam uma pessoa e nunca ensinam padrão.
GENERIC_LOCALS = {
    "contato", "contact", "contacto", "info", "informacoes", "informações",
    "comercial", "vendas", "sales", "sac", "suporte", "support", "atendimento",
    "financeiro", "finance", "faturamento", "cobranca", "cobrança", "rh",
    "recrutamento", "vagas", "jobs", "trabalheconosco", "carreiras", "careers",
    "marketing", "mkt", "adm", "administrativo", "admin", "geral", "email",
    "mail", "hello", "hi", "ola", "olá", "faleconosco", "fale", "equipe", "team",
    "ouvidoria", "privacidade", "privacy", "dpo", "lgpd", "imprensa", "press",
    "compras", "juridico", "jurídico", "legal", "webmaster", "postmaster",
    "noreply", "no-reply", "naoresponda", "nao-responda", "newsletter",
    "notificacoes", "notifications", "billing", "help", "ajuda", "servicos",
    "atendimento2", "orcamento", "orçamento", "sistema", "root", "abuse",
}

_LOCAL_SAFE = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,62}$")


def local_part(email: str) -> str:
    return (email or "").split("@", 1)[0].strip().lower()


def domain_part(email: str) -> str:
    parts = (email or "").split("@", 1)
    return parts[1].strip().lower() if len(parts) == 2 else ""


def is_generic(email_or_local: str) -> bool:
    """True para caixas funcionais (contato@, vendas@...)."""
    local = local_part(email_or_local) if "@" in email_or_local else (email_or_local or "").lower()
    local = strip_accents(local)
    if not local:
        return True
    if local in {strip_accents(g) for g in GENERIC_LOCALS}:
        return True
    # contato1@, vendas.sp@, rh-brasil@
    base = re.split(r"[._\-]", local)[0]
    base = re.sub(r"\d+$", "", base)
    return base in {strip_accents(g) for g in GENERIC_LOCALS}


def render(pattern: str, first: str, last: Optional[str]) -> Optional[str]:
    """Aplica o padrão. Devolve a parte local ou None se faltar peça."""
    if not pattern or not first:
        return None
    if "{last}" in pattern or "{l}" in pattern:
        if not last:
            return None
    local = (
        pattern.replace("{first}", first)
        .replace("{last}", last or "")
        .replace("{f}", first[0])
        .replace("{l}", (last or " ")[0])
    )
    local = local.strip("._-")
    return local if _LOCAL_SAFE.match(local) else None


def name_variants(full_name: str) -> List[Tuple[str, Optional[str]]]:
    """
    Combinações plausíveis de (first, last) para nomes brasileiros.

    'João Pedro Silva Santos' →
        ('joao', 'santos')       último sobrenome
        ('joao', 'silva')        penúltimo (muito comum no Brasil)
        ('joao', 'silvasantos')  sobrenome composto colado
        ('joaopedro', 'santos')  nome composto
    """
    tokens = name_tokens(full_name)
    if not tokens:
        return []
    first = tokens[0]
    variants: List[Tuple[str, Optional[str]]] = []

    def add(f: Optional[str], l: Optional[str]):
        if f and (f, l) not in variants:
            variants.append((f, l))

    if len(tokens) == 1:
        add(first, None)
        return variants

    add(first, tokens[-1])
    if len(tokens) >= 3:
        add(first, tokens[-2])
        add(first, tokens[-2] + tokens[-1])
        add(tokens[0] + tokens[1], tokens[-1])
    add(first, None)
    return variants


def infer_pattern(email: str, full_name: str) -> Optional[str]:
    """
    Descobre qual padrão gerou um e-mail conhecido.
    'joao.silva@acme.com' + 'João da Silva' → '{first}.{last}'
    """
    local = strip_accents(local_part(email))
    if not local or is_generic(local):
        return None
    for first, last in name_variants(full_name):
        for pattern in PATTERNS:
            if render(pattern, first, last) == local:
                return pattern
    return None


def guess_pattern_from_local(local: str) -> Optional[str]:
    """
    Palpite estrutural quando não sabemos o nome do dono do e-mail
    (ex.: e-mail encontrado no site). Confiança baixa de propósito.
    """
    local = strip_accents(local_part(local) if "@" in local else local.lower())
    if not local or is_generic(local):
        return None
    for sep, pattern in ((".", "{first}.{last}"), ("_", "{first}_{last}"), ("-", "{first}-{last}")):
        if sep in local:
            left, _, right = local.partition(sep)
            if left.isalpha() and right.isalpha():
                if len(left) >= 3 and len(right) >= 3:
                    return pattern
                if len(left) == 1 and len(right) >= 3:
                    return "{f}.{last}" if sep == "." else pattern
    return None


# ── Persistência do aprendizado ──────────────────────────────────────────────

def get_pattern(db: Session, domain: str) -> Optional[EmailPattern]:
    if not domain:
        return None
    return db.query(EmailPattern).filter(EmailPattern.domain == domain.lower()).first()


def _get_or_create(db: Session, domain: str) -> EmailPattern:
    row = get_pattern(db, domain)
    if not row:
        row = EmailPattern(domain=domain.lower(), votes={}, evidence=[], samples_count=0, confidence=0)
        db.add(row)
        db.flush()
    return row


def _confidence_for(votes: dict, winner: str) -> int:
    """
    Confiança do padrão a partir do consenso das amostras.
    Discordância entre amostras derruba a nota — empresa com dois formatos
    é justamente onde o palpite cego erra.
    """
    total = sum(votes.values()) or 1
    top = votes.get(winner, 0)
    ratio = top / total
    if top >= 3:
        base = 90
    elif top == 2:
        base = 82
    else:
        base = 72
    if ratio < 0.6:
        base -= 20
    elif ratio < 0.9:
        base -= 8
    return max(40, min(95, base))


def learn_from_email(db: Session, domain: str, email: str,
                     full_name: Optional[str], source: str = "unknown") -> Optional[str]:
    """
    Ensina o padrão do domínio a partir de um e-mail real.
    Devolve o padrão inferido (ou None). Não commita — quem chama controla.
    """
    domain = (domain or domain_part(email)).lower()
    if not domain or not email or is_generic(email):
        return None

    pattern = infer_pattern(email, full_name) if full_name else None
    if not pattern:
        pattern = guess_pattern_from_local(email)
        weight_source = "guess"
    else:
        weight_source = "name"
    if not pattern:
        return None

    row = _get_or_create(db, domain)
    votes = dict(row.votes or {})
    # Palpite estrutural vale menos que inferência com nome confirmado
    votes[pattern] = votes.get(pattern, 0) + (1 if weight_source == "name" else 0.5)
    row.votes = votes

    evidence = list(row.evidence or [])
    if not any(e.get("email") == email for e in evidence):
        evidence.append({"email": email, "name": full_name, "source": source})
        row.evidence = evidence[-5:]

    winner = max(votes, key=lambda k: votes[k])
    row.pattern = winner
    row.samples_count = int(sum(votes.values()))
    row.confidence = _confidence_for(votes, winner)
    row.last_confirmed_at = datetime.now(UTC)
    logger.info(
        "email_pattern learned domain=%s pattern=%s confidence=%d samples=%d source=%s",
        domain, winner, row.confidence, row.samples_count, source,
    )
    return winner


def record_domain_health(db: Session, domain: str, mx_ok: Optional[bool] = None,
                         catch_all: Optional[bool] = None) -> None:
    """Guarda o que já descobrimos sobre o servidor de e-mail do domínio."""
    if not domain:
        return
    row = _get_or_create(db, domain)
    if mx_ok is not None:
        row.mx_ok = mx_ok
    if catch_all is not None:
        row.catch_all = catch_all


def candidates(db: Session, domain: str, full_name: str, limit: int = 4) -> List[dict]:
    """
    Palpites de e-mail ordenados por probabilidade.

    Cada item: {email, pattern, confidence, source}
      source = "pattern"  → padrão aprendido deste domínio (alta confiança)
      source = "common"   → ranking global de mercado (baixa, precisa verificar)
    """
    domain = (domain or "").lower().lstrip("@")
    if not domain or not full_name:
        return []

    variants = name_variants(full_name)
    if not variants:
        return []
    primary_first, primary_last = variants[0]

    out: List[dict] = []
    seen = set()

    def push(local: Optional[str], pattern: str, confidence: int, source: str):
        if not local:
            return
        email = f"{local}@{domain}"
        if email in seen:
            return
        seen.add(email)
        out.append({"email": email, "pattern": pattern, "confidence": confidence, "source": source})

    row = get_pattern(db, domain)
    learned = row.pattern if row else None

    if learned:
        conf = row.confidence or 70
        # Domínio catch-all aceita qualquer coisa: o padrão continua valendo,
        # mas a verificação SMTP não vai poder confirmar — sinalizamos já aqui.
        if row.catch_all:
            conf = min(conf, 70)
        push(render(learned, primary_first, primary_last), learned, conf, "pattern")
        # Sobrenome alternativo com o MESMO padrão aprendido é o segundo melhor
        for first, last in variants[1:3]:
            push(render(learned, first, last), learned, max(30, conf - 25), "pattern_alt")

    for pattern, cold in zip(PATTERNS, _COLD_CONFIDENCE):
        if pattern == learned:
            continue
        # Sem padrão aprendido, o ranking global manda; com padrão aprendido,
        # os demais entram só como rede de segurança e valem bem menos.
        conf = cold if not learned else max(10, cold - 15)
        push(render(pattern, primary_first, primary_last), pattern, conf, "common")
        if len(out) >= limit + 3:
            break

    out.sort(key=lambda c: -c["confidence"])
    return out[:limit]
