"""
Tier comercial de um lead — o fluxograma "Captura e Priorização" em código.

O tier responde "esta empresa é alvo para mim?". É ele que
decide em que lote de agendamento a empresa entra (40 Tier 1, 5 Tier 2, 5
Tier 3) e se ela entra em algum.

O fluxo, na ordem em que as perguntas são feitas:

    Já é cliente Skymail ou de revenda? ── sim ─► Tier 4
    Outro executivo está tratando?      ── sim ─► Tier 4
    Tem menos de 50 funcionários?       ── sim ─► Tier 4
    Tem mais de 1.000 funcionários?     ── sim ─► Tier 3
    Usa Microsoft ou Google?            ── sim ─► Tier 2
                                        ── não ─► Tier 1 (documenta o serviço)

Tier 1 é o melhor alvo: empresa de porte médio com e-mail fora dos dois
grandes — é quem mais tem a ganhar trocando de provedor.

Três decisões de projeto:

1. **Função pura.** `classify_tier()` não toca banco nem rede. Os dois fatos
   que dependem de banco (cliente? outro dono?) chegam como argumento — quem
   os busca é `apply_tier()`.
2. **O caminho é gravado.** `tier_path` guarda cada pergunta e a resposta
   dada, para a tela responder "por que Tier 2?" sem ninguém abrir o código.
3. **Versão junto com o resultado.** Mudou regra? Suba `TIER_VERSION`.
"""
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

#: "51-200", "1.001 - 5.000", "1000+" → (mínimo, máximo ou None).
_FAIXA_TEXTO = re.compile(r"(\d[\d.]*)\s*(?:[-–a]\s*(\d[\d.]*)|\+)")

#: Régua vigente. Ver o cabeçalho do módulo antes de mudar.
TIER_VERSION = "TIER_V1"

TIERS = (1, 2, 3, 4)

#: Limites do fluxograma. "Menos de 50" e "mais de 1.000" são estritos.
MIN_EMPLOYEES = 50
MAX_EMPLOYEES = 1000

TIER_LABELS = {
    1: "Tier 1 — alvo prioritário",
    2: "Tier 2 — usa Microsoft/Google",
    3: "Tier 3 — grande porte",
    4: "Tier 4 — fora da prospecção",
}

#: Gateways de segurança que ficam NA FRENTE do servidor de e-mail. Um MX da
#: Proofpoint não diz qual caixa a empresa usa; o SPF costuma dizer.
_GATEWAYS = (
    "proofpoint", "mimecast", "barracuda", "ironport", "cisco", "messagelabs",
    "symantec", "sophos", "trend micro", "antispamcloud", "mxlogic",
    "mailfilter", "spamh", "gatefy", "cloudflare",
)

#: Trechos de SPF que entregam o provedor real por trás de um gateway.
_SPF_MICROSOFT = ("spf.protection.outlook.com", "outlook.com")
_SPF_GOOGLE = ("_spf.google.com", "google.com")


def _passo(pergunta: str, resposta: str, detalhe: Optional[str] = None) -> Dict[str, str]:
    p = {"pergunta": pergunta, "resposta": resposta}
    if detalhe:
        p["detalhe"] = detalhe
    return p


# ── Funcionários ─────────────────────────────────────────────────────────────

def employee_range(lead: Any) -> Optional[Dict[str, Any]]:
    """
    Quantos funcionários, como intervalo `{min, max, exact, fonte}`.

    A contagem exata da aba People do LinkedIn vence a faixa da página da
    empresa: "51-200" é a autodeclaração de quem preencheu o perfil um dia, a
    contagem é quem está lá hoje.
    """
    exato = getattr(lead, "employee_count_linkedin", None)
    if isinstance(exato, int) and exato > 0:
        return {"min": exato, "max": exato, "exact": exato, "fonte": "linkedin_people"}

    ec = getattr(lead, "employee_count", None)
    if not isinstance(ec, dict):
        return None
    exact = ec.get("exact")
    if isinstance(exact, int) and exact > 0:
        return {"min": exact, "max": exact, "exact": exact, "fonte": ec.get("source")}
    lo, hi = ec.get("min"), ec.get("max")
    if not isinstance(lo, int):
        # Faixa só em texto — é como chega o porte estimado pela Receita
        # ("1-19 (estimado pelo porte)") e a planilha importada ("1000+").
        m = _FAIXA_TEXTO.search(str(ec.get("band") or ec.get("raw") or ""))
        if not m:
            return None
        lo = int(m.group(1).replace(".", ""))
        hi = int(m.group(2).replace(".", "")) if m.group(2) else None
    return {
        "min": lo,
        "max": hi if isinstance(hi, int) else None,
        "exact": None,
        "fonte": ec.get("source"),
    }


def _menos_de_50(faixa: Dict[str, Any]) -> bool:
    """
    "11-50" conta como menos de 50: o teto da faixa é o próprio limite, e a
    pergunta do fluxograma é "é empresa pequena?", não "tem exatamente 50?".
    """
    if faixa["exact"] is not None:
        return faixa["exact"] < MIN_EMPLOYEES
    return faixa["max"] is not None and faixa["max"] <= MIN_EMPLOYEES


def _mais_de_1000(faixa: Dict[str, Any]) -> bool:
    if faixa["exact"] is not None:
        return faixa["exact"] > MAX_EMPLOYEES
    return faixa["min"] > MAX_EMPLOYEES


def _texto_faixa(faixa: Dict[str, Any]) -> str:
    if faixa["exact"] is not None:
        return f"{faixa['exact']} funcionários"
    if faixa["max"] is None:
        return f"{faixa['min']}+ funcionários"
    return f"{faixa['min']}–{faixa['max']} funcionários"


# ── Serviço de e-mail ────────────────────────────────────────────────────────

def email_service(lead: Any) -> Dict[str, Any]:
    """
    Qual serviço de e-mail a empresa usa: `{familia, rotulo, evidencia}`.

    `familia` é "microsoft", "google", "skymail", "outro" ou None (sem MX).
    Quando o MX é um gateway de segurança, o SPF decide — sem isso, toda
    empresa grande atrás de uma Proofpoint viraria Tier 1 por engano.
    """
    report = getattr(lead, "dns_report", None)
    report = report if isinstance(report, dict) else {}
    spf = report["spf"].lower() if isinstance(report.get("spf"), str) else ""

    # `mx_provider` só é gravado com confiança alta; o palpite de confiança
    # média/baixa (é por onde o Skymail aparece) fica no relatório.
    rotulo = (getattr(lead, "mx_provider", None) or report.get("mx_provider") or "").strip()
    baixo = rotulo.lower()

    # Rastro bruto de cada MX: hostname, PTR e dono do ASN. É a evidência que
    # sobra quando nenhum padrão conhecido casou com o hostname.
    rastro = []
    for mx in getattr(lead, "mx_records", None) or report.get("mx") or []:
        if isinstance(mx, dict):
            rastro.extend(str(mx.get(k) or "") for k in ("host", "ptr", "asn_org"))
        elif isinstance(mx, str):
            rastro.append(mx)
    rastro_txt = " ".join(rastro).lower()

    if "skymail" in baixo or "skymail" in rastro_txt:
        return {"familia": "skymail", "rotulo": "Skymail", "evidencia": "mx"}
    if baixo.startswith("microsoft") or "protection.outlook.com" in rastro_txt:
        return {"familia": "microsoft", "rotulo": rotulo or "Microsoft 365", "evidencia": "mx"}
    if baixo.startswith("google") or "google.com" in rastro_txt:
        return {"familia": "google", "rotulo": rotulo or "Google Workspace", "evidencia": "mx"}

    if not rotulo:
        primeiro = next((h for h in rastro if h), None)
        if not primeiro:
            return {"familia": None, "rotulo": None, "evidencia": None}
        rotulo, baixo = primeiro, primeiro.lower()

    if any(g in baixo for g in _GATEWAYS) and spf:
        if any(s in spf for s in _SPF_MICROSOFT):
            return {"familia": "microsoft", "rotulo": f"Microsoft 365 (via {rotulo})",
                    "evidencia": "spf"}
        if any(s in spf for s in _SPF_GOOGLE):
            return {"familia": "google", "rotulo": f"Google Workspace (via {rotulo})",
                    "evidencia": "spf"}

    return {"familia": "outro", "rotulo": rotulo, "evidencia": "mx"}


# ── Classificação ────────────────────────────────────────────────────────────

def _sem_contagem(lead: Any, servico: Dict[str, Any]) -> Dict[str, Any]:
    """
    Porte desconhecido: o fluxograma não tem ramo para isso, mas a coleta às
    vezes não acha a contagem. Devolve `{tier, needs_review, motivo}`.

    Política: otimista pelo MX. Sem porte, assume porte médio e aplica a regra
    do e-mail (Microsoft/Google → Tier 2, outro → Tier 1), para a empresa já
    entrar no próximo lote em vez de parar à espera de alguém. Continua
    marcada para revisão: o badge avisa que o porte é suposto, e informar o
    porte na ficha refaz a classificação com o dado real.
    """
    familia = servico.get("familia")
    if familia in ("microsoft", "google"):
        return {"tier": 2, "needs_review": True,
                "motivo": f"Porte não encontrado — suposto médio; usa {servico['rotulo']}."}
    if familia is None:
        return {"tier": 1, "needs_review": True,
                "motivo": "Porte não encontrado e sem e-mail publicado — confira na ficha."}
    return {"tier": 1, "needs_review": True,
            "motivo": f"Porte não encontrado — suposto médio; usa {servico['rotulo']}."}


def classify_tier(
    lead: Any,
    *,
    is_customer: bool = False,
    customer_kind: Optional[str] = None,
    other_owner: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Classifica uma ficha. Sem banco, sem rede.

    `is_customer` / `customer_kind` vêm da base de clientes do time (ou do MX
    Skymail, detectado aqui). `other_owner` é o e-mail do executivo que já
    trata o domínio, quando existe. Devolve
    `{tier, reason, path, email_service, employees, needs_review, version}`.
    """
    path: List[Dict[str, str]] = []
    servico = email_service(lead)
    faixa = employee_range(lead)

    def fim(tier: Optional[int], reason: str, needs_review: bool = False) -> Dict[str, Any]:
        return {
            "tier": tier,
            "reason": reason,
            "path": path,
            "email_service": servico,
            "employees": faixa,
            "needs_review": needs_review,
            "version": TIER_VERSION,
        }

    # 1. Já é cliente?
    relacionamento = getattr(lead, "relationship", None)
    cliente_por_mx = servico["familia"] == "skymail"
    if is_customer or cliente_por_mx or relacionamento == "CUSTOMER":
        if cliente_por_mx:
            detalhe = f"MX aponta para {servico['rotulo']}"
        elif customer_kind == "revenda":
            detalhe = "Na base de clientes de revenda"
        elif is_customer:
            detalhe = "Na base de clientes Skymail"
        else:
            detalhe = "Marcado como cliente na ficha"
        path.append(_passo("Já é cliente Skymail ou de revenda?", "sim", detalhe))
        return fim(4, f"Já é cliente — {detalhe.lower()}.")
    path.append(_passo("Já é cliente Skymail ou de revenda?", "não"))

    # 2. Outro executivo tratando?
    if other_owner:
        path.append(_passo("Algum outro executivo está tratando?", "sim", other_owner))
        return fim(4, f"Já está com outro executivo ({other_owner}).")
    path.append(_passo("Algum outro executivo está tratando?", "não"))

    # 3–4. Porte. Faixa que atravessa um dos limites ("10-99" da Receita)
    # não responde a pergunta — vale o mesmo que não saber.
    if faixa is not None and faixa["exact"] is None and faixa["max"] is not None and (
        faixa["min"] < MIN_EMPLOYEES < faixa["max"]
        or faixa["min"] <= MAX_EMPLOYEES < faixa["max"]
    ):
        faixa = None
    if faixa is None:
        decisao = _sem_contagem(lead, servico)
        path.append(_passo("Quantidade de funcionários", "desconhecida", decisao["motivo"]))
        return fim(decisao["tier"], decisao["motivo"], decisao["needs_review"])

    texto = _texto_faixa(faixa)
    if _menos_de_50(faixa):
        path.append(_passo("Tem menos de 50 funcionários?", "sim", texto))
        return fim(4, f"Empresa pequena ({texto}).")
    path.append(_passo("Tem menos de 50 funcionários?", "não", texto))

    if _mais_de_1000(faixa):
        path.append(_passo("Tem mais de 1.000 funcionários?", "sim", texto))
        rot = servico["rotulo"] or "MX não encontrado"
        path.append(_passo("Apontamento MX", rot))
        return fim(3, f"Grande porte ({texto}) — e-mail: {rot}.")
    path.append(_passo("Tem mais de 1.000 funcionários?", "não", texto))

    # 5. Microsoft ou Google?
    if servico["familia"] is None:
        path.append(_passo("Usa Microsoft ou Google?", "sem MX",
                           "Domínio sem servidor de e-mail publicado"))
        return fim(1, f"Porte médio ({texto}) e sem e-mail corporativo publicado.",
                   needs_review=True)
    if servico["familia"] in ("microsoft", "google"):
        via = " (pelo SPF)" if servico["evidencia"] == "spf" else ""
        path.append(_passo("Usa Microsoft ou Google?", "sim", servico["rotulo"] + via))
        return fim(2, f"Porte médio ({texto}) e usa {servico['rotulo']}.")

    path.append(_passo("Usa Microsoft ou Google?", "não", f"Usa {servico['rotulo']}"))
    return fim(1, f"Porte médio ({texto}) e usa {servico['rotulo']} — alvo prioritário.")


# ── Gravação ─────────────────────────────────────────────────────────────────

def team_of(profile_email: Optional[str], user_id: str) -> str:
    """
    O "time" de um usuário: o domínio do e-mail dele.

    O produto não tem organização cadastrada. Dois vendedores com
    @empresa.com.br são do mesmo time; um usuário de outra empresa não é —
    e é isso que impede a base de clientes de um time vazar para outro, e
    "outro executivo está tratando" de revelar a carteira de um concorrente.
    Sem e-mail, o usuário é um time de um só.
    """
    if profile_email and "@" in profile_email:
        return profile_email.rsplit("@", 1)[1].strip().lower()
    return f"user:{user_id}"


def _team_user_ids(db: Session, team: str, user_id: str) -> Dict[str, Optional[str]]:
    """{user_id: email} de quem é do mesmo time, sem incluir o próprio usuário."""
    from models.database import Profile

    if team.startswith("user:"):
        return {}
    perfis = (
        db.query(Profile.id, Profile.email)
        .filter(Profile.email.ilike(f"%@{team}"), Profile.id != user_id)
        .all()
    )
    return {pid: email for pid, email in perfis}


def find_other_owner(db: Session, lead: Any, user_id: str, team: str) -> Optional[str]:
    """
    E-mail do colega de time que já trabalha este domínio, se houver.

    "Trabalhando" é ficha viva: não perdida e não escondida. Ficha que o colega
    só pesquisou e largou em "novo" também conta — o fluxograma pergunta se
    alguém está tratando, e quem pesquisou primeiro tem a vez.
    """
    from models.database import Lead, HIDDEN_LEAD_STATUSES

    domain = getattr(lead, "domain", None)
    if not domain:
        return None
    colegas = _team_user_ids(db, team, user_id)
    if not colegas:
        return None
    outro = (
        db.query(Lead.user_id)
        .filter(
            Lead.domain == domain,
            Lead.user_id.in_(list(colegas)),
            Lead.stage != "perdido",
            Lead.status.notin_(HIDDEN_LEAD_STATUSES),
        )
        .order_by(Lead.created_at.asc())
        .first()
    )
    if not outro:
        return None
    return colegas.get(outro[0]) or "outro executivo"


def find_customer(db: Session, domain: Optional[str], team: str) -> Optional[str]:
    """`"skymail"` / `"revenda"` se o domínio está na base de clientes do time."""
    from models.database import CustomerDomain

    if not domain:
        return None
    row = (
        db.query(CustomerDomain.kind)
        .filter(CustomerDomain.team == team, CustomerDomain.domain == domain.lower())
        .first()
    )
    return row[0] if row else None


def apply_tier(db: Session, lead: Any, user_id: str,
               profile_email: Optional[str] = None) -> Dict[str, Any]:
    """
    Busca os fatos de banco, classifica e grava nas colunas da ficha.
    NÃO commita. Tier definido à mão (`tier_manual`) é respeitado: o
    automático continua sendo calculado e gravado no caminho, mas `tier`
    reflete a escolha do vendedor.
    """
    team = team_of(profile_email, user_id)
    kind = find_customer(db, getattr(lead, "domain", None), team)
    owner = find_other_owner(db, lead, user_id, team)
    resultado = classify_tier(
        lead, is_customer=kind is not None, customer_kind=kind, other_owner=owner,
    )
    manual = getattr(lead, "tier_manual", None)
    lead.tier = manual if manual in TIERS else resultado["tier"]
    lead.tier_auto = resultado["tier"]
    lead.tier_reason = resultado["reason"][:500]
    lead.tier_details = {
        "path": resultado["path"],
        "email_service": resultado["email_service"],
        "employees": resultado["employees"],
        "needs_review": resultado["needs_review"],
        "version": resultado["version"],
    }
    return resultado
