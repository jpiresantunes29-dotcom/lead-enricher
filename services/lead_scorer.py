"""
Lead Scoring explicável (v3 — docs/PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md §4).

score_lead() é uma função pura: recebe os dados do lead + decisores e devolve
{score, priority, breakdown, version}. Cada ponto carrega a evidência que o
justificou — é o que torna o score auditável na UI.

Aceita tanto objetos ORM (Lead/DecisionMaker) quanto dicts, para poder ser
chamada do router (ORM) e dos testes (dicts) sem conversão.
"""
from typing import Any, List, Optional

SCORING_VERSION = "v1"

# Faixas da proposta original, mantidas (§4.3)
PRIORITY_BANDS = [
    (41, "alta"),
    (21, "media"),
    (0, "baixa"),
]

# Provedores que indicam maturidade digital (critério original: +10)
_MODERN_MAIL = ("microsoft 365", "google workspace")

# Gateways de segurança de email ⇒ orçamento de TI (+12)
_SECURITY_GATEWAYS = (
    "proofpoint", "mimecast", "barracuda", "ironport", "cisco",
    "sophos", "trend micro", "symantec", "messagelabs", "gatefy",
)

# Hosting em cloud moderna (+5)
_CLOUD_HOSTS = (
    "amazon", "aws", "microsoft", "azure", "google", "cloudflare",
    "fastly", "akamai", "digitalocean", "hetzner",
)

# Cargos C-level / founder (prioridade ≤ 2 no decision_finder)
_TOP_TITLES = (
    "founder", "ceo", "owner", "presidente", "sócio", "socio",
    "chief", "cto", "cfo", "coo", "cmo", "cro", "cpo",
    "vp", "vice president", "partner",
)


def _get(obj: Any, key: str, default=None):
    """Lê atributo (ORM) ou chave (dict)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _employee_minimum(employee_count: Optional[dict]) -> Optional[int]:
    """Extrai o número mínimo confiável de funcionários do JSON consolidado."""
    if not isinstance(employee_count, dict):
        return None
    for key in ("exact", "min"):
        v = employee_count.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


def score_lead(lead: Any, decision_makers: Optional[List[Any]] = None) -> dict:
    """
    Calcula o score do lead. Retorna:
      {score: int, priority: str, breakdown: [{criterion, points, evidence}],
       version: str}
    """
    decision_makers = decision_makers or []
    breakdown: List[dict] = []

    def add(criterion: str, points: int, evidence: str):
        breakdown.append({"criterion": criterion, "points": points, "evidence": evidence})

    # ── Maturidade digital (email) ────────────────────────────────────────
    mx = (_get(lead, "mx_provider") or "").lower()
    if any(p in mx for p in _MODERN_MAIL):
        add("Email corporativo moderno", 10, _get(lead, "mx_provider"))
    elif any(g in mx for g in _SECURITY_GATEWAYS):
        add("Gateway de segurança de email", 12, _get(lead, "mx_provider"))

    dns_report = _get(lead, "dns_report") or {}
    if isinstance(dns_report, dict):
        if dns_report.get("spf"):
            add("SPF publicado", 3, "registro SPF presente no DNS")
        if dns_report.get("dmarc"):
            add("DMARC publicado", 5, "política DMARC ativa")

    hosting = (_get(lead, "hosting_provider") or "").lower()
    if any(c in hosting for c in _CLOUD_HOSTS):
        add("Hosting em cloud", 5, _get(lead, "hosting_provider"))

    # ── Porte ─────────────────────────────────────────────────────────────
    emp = _employee_minimum(_get(lead, "employee_count"))
    if emp is not None:
        if emp > 200:
            add("Porte: 200+ funcionários", 20, f"~{emp} funcionários")
        elif emp > 50:
            add("Porte: 51–200 funcionários", 15, f"~{emp} funcionários")
        elif emp > 10:
            add("Porte: 11–50 funcionários", 8, f"~{emp} funcionários")

    # ── Presença digital ──────────────────────────────────────────────────
    if _get(lead, "linkedin_confidence") == "verified":
        add("LinkedIn verificado", 5, _get(lead, "linkedin_url") or "página confirmada")
    if _get(lead, "description") and _get(lead, "sector"):
        add("Site profissional", 3, f"setor: {_get(lead, 'sector')}")

    # ── Decisores (recalculado após /api/decisores) ──────────────────────
    if decision_makers:
        add("Decisor encontrado", 10, f"{len(decision_makers)} decisor(es) mapeado(s)")

        has_valid_email = False
        has_phone = False
        has_top_title = False
        for dm in decision_makers:
            for pe in (_get(dm, "probable_emails") or []):
                status = pe.get("status") if isinstance(pe, dict) else None
                if status == "valid":
                    has_valid_email = True
            if _get(dm, "phone"):
                has_phone = True
            title = (_get(dm, "title_found") or _get(dm, "title_searched") or "").lower()
            if any(t in title for t in _TOP_TITLES):
                has_top_title = True

        if has_valid_email:
            add("E-mail de decisor validado (SMTP)", 15, "ao menos 1 endereço confirmado")
        if has_phone:
            add("Telefone direto de decisor", 20, "contato telefônico disponível")
        if has_top_title:
            add("Acesso a C-level/founder", 5, "decisor de topo identificado")

    score = sum(item["points"] for item in breakdown)
    priority = next(label for floor, label in PRIORITY_BANDS if score >= floor)

    return {
        "score": score,
        "priority": priority,
        "breakdown": breakdown,
        "version": SCORING_VERSION,
    }


def apply_score(lead, decision_makers: Optional[List[Any]] = None) -> None:
    """Calcula e grava o score em um objeto ORM Lead (não faz commit)."""
    result = score_lead(lead, decision_makers)
    lead.score = result["score"]
    lead.priority = result["priority"]
    lead.score_breakdown = result["breakdown"]
    lead.score_version = result["version"]
