"""
Encontra decisores em uma empresa filtrando por cargo, com VERIFICAÇÃO.

Estratégia (em ordem de prioridade):
  0. Quadro societário da Receita (CNPJ) — associação oficial empresa→pessoa
  1. Aba People/Pessoas do LinkedIn (linkedin.com/company/{slug}/people/)
     — fonte direta; associação empresa→pessoa é garantida pela página
  2. Fallback: Multi-engine search (SearXNG → DDG → Bing → Google)
     com query: site:linkedin.com/in "{cargo}" "{empresa}"

Com uma sessão de banco (`db`), os e-mails deixam de ser palpite fixo e passam
a sair do padrão aprendido do domínio, e cada decisor encontrado é gravado no
banco global de pessoas — alimentando as próximas buscas de graça.

Cargos de decisão reconhecidos e priorizados por TITLE_PRIORITY.
Sem dependência de APIs pagas.
"""
import logging
import re
import requests
from typing import List, Optional
from html import unescape
from urllib.parse import unquote
from bs4 import BeautifulSoup

from ._utils import normalize_domain, HEADERS, LINKEDIN_COMPANY_RE
from ._ddg import search_multi
from .email_verifier import verify_batch, verify_emails

logger = logging.getLogger(__name__)


LINKEDIN_PROFILE_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([a-zA-Z0-9\-._%]+)",
    re.IGNORECASE,
)

# Cargos que indicam poder de decisão
DECISION_MAKER_TITLES = [
    "Founder", "Co-Founder", "Co-founder",
    "CEO", "Owner",
    "Chief", "VP", "Vice President", "Partner",
    "Director", "Head of",
    "CTO", "CFO", "COO", "CMO", "CRO", "CPO",
    "Presidente", "Sócio", "Diretor", "Gerente Geral",
]

# Menor número = maior prioridade
TITLE_PRIORITY: dict = {
    "founder": 1, "co-founder": 1, "ceo": 1, "owner": 1, "presidente": 1,
    "sócio": 1, "socio": 1,
    "chief": 2, "cto": 2, "cfo": 2, "coo": 2, "cmo": 2, "cro": 2, "cpo": 2,
    "vp": 2, "vice president": 2, "partner": 2,
    "director": 3, "diretor": 3, "head of": 3,
    "gerente geral": 4,
}

_CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def _title_priority(title: str) -> int:
    """Retorna prioridade numérica para um título (menor = mais relevante)."""
    t = title.lower()
    for keyword, prio in TITLE_PRIORITY.items():
        if keyword in t:
            return prio
    return 99


def _is_decision_maker_title(title: str) -> bool:
    """Verifica se o título é de um decisor reconhecido."""
    t = title.lower()
    return any(dm.lower() in t for dm in DECISION_MAKER_TITLES)


def _slug_to_name(slug: str) -> str:
    slug = unquote(slug)
    parts = re.split(r"[\-_]", slug)
    parts = [p for p in parts if not p.isdigit() and not re.fullmatch(r"[a-f0-9]{6,}", p)]
    return " ".join(p.capitalize() for p in parts[:4]) if parts else slug.title()


def _extract_name(title: str, slug: str) -> str:
    """
    Extrai nome do decisor do título do resultado de busca.
    Padrões: "João Silva - CTO - Acme Corp | LinkedIn"
    """
    if not title:
        return _slug_to_name(slug)
    title = unescape(title)
    title = re.sub(r"^\(\d+\+?\)\s*", "", title)
    title = re.sub(r"\s*[\|·]\s*LinkedIn.*$", "", title, flags=re.IGNORECASE)
    name_part = re.split(r"\s+[\-–—|·]\s+", title)[0].strip()
    if 3 <= len(name_part) <= 80 and not name_part.lower().startswith(
        ("cto", "ceo", "cfo", "vp ", "diretor", "head", "chief")
    ):
        return name_part
    return _slug_to_name(slug)


def _generate_emails(name: str, domain: str) -> List[str]:
    """Gera padrões prováveis de email corporativo."""
    if not name or not domain:
        return []
    parts = [p for p in re.split(r"\s+", name.lower()) if p.isalpha()]
    if not parts:
        return []
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else None
    emails = [f"{first}@{domain}"]
    if last and last != first:
        emails.extend([
            f"{first}.{last}@{domain}",
            f"{first}{last}@{domain}",
            f"{first[0]}{last}@{domain}",
        ])
    return list(dict.fromkeys(emails))[:4]


def _match_confidence(snippet: str, title: str, role: str, company: str) -> str:
    """Avalia se snippet+título confirmam cargo e empresa."""
    text = (snippet + " " + title).lower()
    has_role = bool(role) and role.lower() in text
    has_company = bool(company) and company.lower() in text
    if has_role and has_company:
        return "high"
    if has_role or has_company:
        return "medium"
    return "low"


def _probable_emails(name: str, domain: str, verify: bool, db=None) -> List[dict]:
    """
    Palpites de e-mail para a pessoa.

    Com `db`, usa o padrão aprendido do domínio (alta precisão) e verifica os
    candidatos em uma única rodada paralela. Sem `db`, cai na heurística fixa.
    """
    if db is not None:
        from .people import email_patterns as ep

        candidates = ep.candidates(db, domain, name, limit=4)
        emails_raw = [c["email"] for c in candidates]
        confidence_by_email = {c["email"]: c["confidence"] for c in candidates}
        pattern_by_email = {c["email"]: c["pattern"] for c in candidates}
    else:
        emails_raw = _generate_emails(name, domain)
        confidence_by_email = {}
        pattern_by_email = {}

    if not emails_raw:
        return []

    if verify:
        results = verify_batch(emails_raw, budget_seconds=6.0)
    else:
        results = [{"email": e, "status": "unknown"} for e in emails_raw]

    out = []
    for item in results:
        email = item["email"]
        status = item["status"]
        base = confidence_by_email.get(email, 30)
        if status == "valid":
            confidence = 97
        elif status == "invalid":
            continue
        elif status == "catch_all":
            confidence = min(base, 70)
        else:
            confidence = base
        out.append({
            "email": email,
            "status": status,
            "confidence": confidence,
            "pattern": pattern_by_email.get(email),
        })

    out.sort(key=lambda e: -e["confidence"])
    return out


def _build_decisor(name: str, title_found: str, slug: str, domain: str,
                   source_role: str, snippet: str, confidence: str,
                   verify_emails_smtp: bool, db=None,
                   linkedin_url: Optional[str] = None) -> dict:
    probable_emails = _probable_emails(name, domain, verify_emails_smtp, db=db)
    return {
        "name": name,
        "title_searched": source_role,
        "title_found": title_found,
        "snippet": snippet,
        "linkedin_url": linkedin_url or (f"https://www.linkedin.com/in/{slug}" if slug else None),
        "probable_emails": probable_emails,
        "match_confidence": confidence,
        "phone": None,
    }


# ---------------------------------------------------------------------------
# Fonte 1 — Aba People do LinkedIn
# ---------------------------------------------------------------------------

def _fetch_people_tab_decisors(
    linkedin_url: str,
    company_name: str,
    roles: List[str],
    domain: str,
    verify_emails_smtp: bool,
    db=None,
) -> List[dict]:
    """
    Extrai decisores diretamente da aba People/Pessoas do LinkedIn.
    Retorna lista de dicts no schema padrão.
    Requer que o LinkedIn retorne HTML sem autenticação (melhor esforço).
    """
    slug_match = LINKEDIN_COMPANY_RE.search(linkedin_url)
    if not slug_match:
        return []

    company_slug = slug_match.group(1).rstrip("/")
    people_url = f"https://www.linkedin.com/company/{company_slug}/people/"

    try:
        resp = requests.get(people_url, headers=HEADERS, timeout=12, allow_redirects=True)
        if resp.status_code != 200 or len(resp.text) < 2000:
            return []
        html = resp.text
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    found: List[dict] = []
    seen_slugs: set = set()

    # Estratégia A: cartões de perfil visíveis na página
    # LinkedIn renderiza alguns perfis em <li> ou <div> com nome e cargo
    for card in soup.find_all(["li", "div"], class_=re.compile(r"(profile|member|result|person|employee)", re.I)):
        name_tag = card.find(["h3", "h4", "span", "a"], string=re.compile(r"[A-ZÀ-Ü][a-zà-ü]"))
        title_tag = card.find(["p", "span", "div"], string=re.compile(r"[A-Za-z]{3,}"))

        if not name_tag:
            continue

        name = name_tag.get_text(strip=True)
        title_found = title_tag.get_text(strip=True) if title_tag else ""

        if not _is_decision_maker_title(title_found):
            continue

        # Tenta extrair LinkedIn URL do cartão
        a_tag = card.find("a", href=re.compile(r"linkedin\.com/in/"))
        if a_tag:
            m = LINKEDIN_PROFILE_RE.search(a_tag["href"])
            slug = m.group(1).lower() if m else _slug_to_name(name).replace(" ", "-").lower()
        else:
            slug = name.lower().replace(" ", "-")

        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        # Determina qual role do pedido melhor casa com o título encontrado
        matched_role = next(
            (r for r in roles if r.lower() in title_found.lower()),
            title_found or roles[0],
        )

        found.append(_build_decisor(
            name=name,
            title_found=title_found,
            slug=slug,
            domain=domain,
            source_role=matched_role,
            snippet=f"Encontrado na aba People de {company_name} no LinkedIn.",
            confidence="high",
            verify_emails_smtp=verify_emails_smtp,
            db=db,
        ))

    # Estratégia B: JSON embutido na página (LinkedIn injeta dados em scripts)
    if not found:
        for script in soup.find_all("script", type="application/json"):
            try:
                import json
                data = json.loads(script.string or "")
                _extract_from_json(data, roles, company_name, domain, seen_slugs, found,
                                   verify_emails_smtp, db)
            except Exception:
                continue

    return found


def _extract_from_json(data, roles: List[str], company_name: str,
                       domain: str, seen_slugs: set, found: List[dict],
                       verify_emails_smtp: bool, db=None):
    """Extrai recursivamente perfis de estruturas JSON do LinkedIn."""
    if isinstance(data, dict):
        name = data.get("firstName", "") or data.get("name", "")
        title = data.get("headline", "") or data.get("title", "") or data.get("occupation", "")
        if name and title and _is_decision_maker_title(title):
            slug = data.get("publicIdentifier", name.lower().replace(" ", "-"))
            if slug not in seen_slugs:
                seen_slugs.add(slug)
                matched_role = next(
                    (r for r in roles if r.lower() in title.lower()),
                    title or roles[0],
                )
                found.append(_build_decisor(
                    name=name,
                    title_found=title,
                    slug=slug,
                    domain=domain,
                    source_role=matched_role,
                    snippet=f"Encontrado via dados estruturados da página People de {company_name}.",
                    confidence="high",
                    verify_emails_smtp=verify_emails_smtp,
                    db=db,
                ))
        for v in data.values():
            if isinstance(v, (dict, list)):
                _extract_from_json(v, roles, company_name, domain, seen_slugs, found,
                                   verify_emails_smtp, db)
    elif isinstance(data, list):
        for item in data:
            _extract_from_json(item, roles, company_name, domain, seen_slugs, found,
                               verify_emails_smtp, db)


# ---------------------------------------------------------------------------
# Fonte 2 — Fallback: busca em motores de pesquisa
# ---------------------------------------------------------------------------

def _search_one_role(role: str, company_term: str) -> List[dict]:
    """Busca multi-engine (SearXNG → DDG → Bing → Google) com fallback."""
    query = f'site:linkedin.com/in "{role}" "{company_term}"'
    results = search_multi(query, pattern=r"linkedin\.com/in/[a-zA-Z0-9\-._%]+")
    if not results:
        query2 = f'site:linkedin.com/in {role} {company_term}'
        results = search_multi(query2, pattern=r"linkedin\.com/in/[a-zA-Z0-9\-._%]+")
    return results


# ---------------------------------------------------------------------------
# Ponto de entrada público
# ---------------------------------------------------------------------------

def _qsa_decisors(db, domain: str, company_name: str, roles: List[str],
                  verify_emails_smtp: bool) -> List[dict]:
    """
    Fonte 0 — sócios administradores registrados na Receita Federal.

    É a única fonte em que a ligação empresa→pessoa é oficial (não inferida),
    e cobre justamente a PME brasileira, onde o LinkedIn é fraco.
    """
    if db is None:
        return []
    from .people import repository as repo
    from .providers import cnpj_receita

    company = repo.get_company(db, domain)
    if not company or not company.cnpj_data:
        return []

    socios = cnpj_receita.decision_makers_from_qsa(
        {"qsa": (company.cnpj_data or {}).get("socios") or []}
    )
    out = []
    for socio in socios:
        out.append(_build_decisor(
            name=socio["name"],
            title_found=socio["title"],
            slug="",
            domain=domain,
            source_role=roles[0] if roles else socio["title"],
            snippet=(
                f"Sócio administrador de {company_name} no registro público da "
                f"Receita Federal (CNPJ {company.cnpj})."
            ),
            confidence="high",
            verify_emails_smtp=verify_emails_smtp,
            db=db,
            linkedin_url=None,
        ))
    return out


def _persist_decisors(db, decisors: List[dict], domain: str, company_name: Optional[str]) -> None:
    """Grava os decisores no banco global de pessoas (alimenta o cache futuro)."""
    if db is None or not decisors:
        return
    from .people import repository as repo
    from .people import email_patterns as ep

    company = repo.get_company(db, domain) or repo.upsert_company(db, domain, name=company_name)
    for item in decisors:
        person = repo.upsert_person(
            db,
            full_name=item.get("name"),
            linkedin_url=item.get("linkedin_url"),
            title=item.get("title_found") or item.get("title_searched"),
            company_domain=domain,
            company_name=company_name,
            company=company,
            source="search" if item.get("linkedin_url") else "cnpj_qsa",
        )
        if not person:
            continue
        for email in item.get("probable_emails") or []:
            row = repo.add_email(
                db, person, email["email"],
                status=email.get("status", "unknown"),
                confidence=int(email.get("confidence") or 0),
                source="pattern",
                pattern=email.get("pattern"),
            )
            if row and email.get("status") == "valid":
                ep.learn_from_email(db, domain, email["email"], person.full_name, source="smtp")


def find_decision_makers(
    domain: str,
    company_name: Optional[str],
    roles: List[str],
    limit: int = 5,
    verify_emails_smtp: bool = False,
    linkedin_url: Optional[str] = None,
    db=None,
) -> List[dict]:
    """
    Busca decisores e devolve lista com:
      name, title_searched, title_found, snippet, linkedin_url,
      probable_emails (lista de {email, status, confidence}), match_confidence, phone

    Estratégia:
      0. Quadro societário da Receita (se o CNPJ da empresa já é conhecido)
      1. Aba People do LinkedIn (se linkedin_url disponível)
      2. Fallback: busca por motor de pesquisa por cargo
    Sem dependência de APIs pagas.
    """
    domain = normalize_domain(domain)
    company_term = company_name or domain.split(".")[0]
    found: List[dict] = []
    seen_slugs: set = set()
    seen_names: set = set()

    # Fonte 0: registro público da Receita — associação oficial
    for r in _qsa_decisors(db, domain, company_term, roles, verify_emails_smtp):
        name_key = (r.get("name") or "").lower()
        if name_key and name_key not in seen_names:
            seen_names.add(name_key)
            found.append(r)
        if len(found) >= limit:
            break

    # Fonte 1: aba People — acesso direto, confiança alta
    if linkedin_url and len(found) < limit:
        people_results = _fetch_people_tab_decisors(
            linkedin_url=linkedin_url,
            company_name=company_term,
            roles=roles,
            domain=domain,
            verify_emails_smtp=verify_emails_smtp,
            db=db,
        )
        for r in people_results:
            slug = LINKEDIN_PROFILE_RE.search(r.get("linkedin_url") or "")
            slug_key = slug.group(1).lower() if slug else (r.get("name") or "").lower()
            name_key = (r.get("name") or "").lower()
            if slug_key not in seen_slugs and name_key not in seen_names:
                seen_slugs.add(slug_key)
                seen_names.add(name_key)
                found.append(r)
            if len(found) >= limit:
                break

    # Fonte 2: fallback por busca — complementa se necessário
    if len(found) < limit:
        for role in roles[:3]:
            if len(found) >= limit:
                break
            results = _search_one_role(role, company_term)
            for r in results:
                url = r.get("url", "")
                m = LINKEDIN_PROFILE_RE.search(url)
                if not m:
                    continue
                slug = m.group(1).lower()
                if slug in seen_slugs:
                    continue
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                name = _extract_name(title, slug)
                if (name or "").lower() in seen_names:
                    continue
                seen_slugs.add(slug)
                seen_names.add((name or "").lower())
                confidence = _match_confidence(snippet, title, role, company_term)

                found.append(_build_decisor(
                    name=name,
                    title_found=title,
                    slug=slug,
                    domain=domain,
                    source_role=role,
                    snippet=snippet,
                    confidence=confidence,
                    verify_emails_smtp=verify_emails_smtp,
                    db=db,
                ))
                if len(found) >= limit:
                    break

    # Ordena: primeiro por prioridade de cargo, depois por confiança
    found.sort(key=lambda x: (
        _title_priority(x.get("title_found", "") or x.get("title_searched", "")),
        _CONFIDENCE_ORDER.get(x.get("match_confidence", "low"), 2),
    ))

    result = found[:limit]
    _persist_decisors(db, result, domain, company_name)

    logger.info(
        "Decision makers found=%d domain=%s roles=%s",
        len(result), domain, roles,
    )
    return result
