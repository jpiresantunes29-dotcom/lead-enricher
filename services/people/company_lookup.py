"""
Nome da empresa → domínio oficial.

No perfil de uma PESSOA o LinkedIn quase nunca mostra o site da empresa, mas
sem domínio não há e-mail corporativo. Este módulo faz a ponte — com validação,
porque domínio errado gera e-mail errado, que é pior do que não entregar nada.

Ordem:
  1. banco próprio (Company / Lead já enriquecidos)   → instantâneo
  2. busca web + validação por token do nome + MX      → só quando pedido
"""
import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.database import Company, Lead
from services._ddg import search_multi
from services._utils import normalize_domain
from services.email_verifier import has_mx
from .identity import strip_accents

logger = logging.getLogger(__name__)

# Domínios que nunca são o site oficial de uma empresa
_BLOCKLIST = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "wikipedia.org", "glassdoor.com", "glassdoor.com.br",
    "indeed.com", "crunchbase.com", "bloomberg.com", "reclameaqui.com.br",
    "jusbrasil.com.br", "econodata.com.br", "cnpj.biz", "empresascnpj.com",
    "consultacnpj.com", "solutudo.com.br", "apontador.com.br", "telelistas.net",
    "google.com", "bing.com", "duckduckgo.com", "medium.com", "github.com",
    "gov.br", "sintese.com", "casadosdados.com.br", "cnpja.com", "serasa.com.br",
}

# Palavras do nome fantasia que não ajudam a identificar o domínio
_STOPWORDS = {
    "ltda", "sa", "s", "me", "epp", "eireli", "group", "grupo", "holding",
    "company", "companhia", "cia", "inc", "corp", "corporation", "tecnologia",
    "technologies", "solutions", "solucoes", "servicos", "services", "brasil",
    "brazil", "do", "da", "de", "dos", "das", "e", "the", "and", "consultoria",
    "industria", "comercio", "distribuidora", "participacoes",
}


def _tokens(name: str) -> List[str]:
    ascii_name = strip_accents(name or "").lower()
    return [t for t in re.split(r"[^a-z0-9]+", ascii_name) if len(t) > 2 and t not in _STOPWORDS]


def _domain_root(domain: str) -> str:
    """'acme.com.br' → 'acme' (parte que carrega a marca)."""
    parts = (domain or "").split(".")
    return parts[0] if parts else ""


def _matches_name(domain: str, company_name: str) -> bool:
    """
    O domínio precisa carregar a marca. 'acmetech.com.br' para 'Acme Tech' passa;
    'noticiasdomercado.com.br' não. É o que impede e-mail inventado.
    """
    root = _domain_root(domain)
    if not root:
        return False
    tokens = _tokens(company_name)
    if not tokens:
        return False
    joined = "".join(tokens)
    if root == joined or joined.startswith(root) or root.startswith(joined):
        return True
    return any(t in root or root in t for t in tokens if len(t) >= 4)


def from_database(db: Session, company_name: Optional[str] = None,
                  linkedin_slug: Optional[str] = None) -> Optional[str]:
    """Resolve pelo que já está no banco — sem rede, milissegundos."""
    if linkedin_slug:
        row = db.query(Company).filter(Company.linkedin_slug == linkedin_slug).first()
        if row:
            return row.domain
        lead = (
            db.query(Lead)
            .filter(Lead.linkedin_url.ilike(f"%/company/{linkedin_slug}%"), Lead.domain.isnot(None))
            .first()
        )
        if lead:
            return lead.domain

    if company_name:
        name = company_name.strip()
        row = (
            db.query(Company)
            .filter(func.lower(Company.name) == name.lower())
            .first()
        )
        if row:
            return row.domain
        lead = (
            db.query(Lead)
            .filter(func.lower(Lead.company_name) == name.lower(), Lead.domain.isnot(None))
            .order_by(Lead.created_at.desc())
            .first()
        )
        if lead:
            return lead.domain
    return None


def from_web(company_name: str, location: Optional[str] = None) -> Optional[dict]:
    """
    Busca o site oficial na web e valida antes de aceitar.
    Devolve {domain, confidence} ou None. Custa uma busca — chame sob demanda.
    """
    if not company_name or len(company_name.strip()) < 3:
        return None

    query = f'"{company_name}" site oficial'
    if location:
        query += f" {location}"

    results = search_multi(query, pattern=r"[a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(?:\.[a-z]{2})?")
    counts: dict = {}
    for item in results[:20]:
        url = item.get("url") or ""
        if not url.startswith("http"):
            url = "https://" + url
        host = normalize_domain(urlparse(url).netloc or url)
        if not host or "." not in host:
            continue
        if any(host == b or host.endswith("." + b) for b in _BLOCKLIST):
            continue
        counts[host] = counts.get(host, 0) + 1

    for host, hits in sorted(counts.items(), key=lambda kv: -kv[1]):
        if not _matches_name(host, company_name):
            continue
        if not has_mx(host):
            continue
        confidence = 85 if hits > 1 else 70
        logger.info("Domínio inferido company=%s domain=%s hits=%d", company_name, host, hits)
        return {"domain": host, "confidence": confidence, "source": "web_search"}

    return None


def resolve(db: Session, company_name: Optional[str], linkedin_slug: Optional[str] = None,
            location: Optional[str] = None, allow_network: bool = False) -> Optional[dict]:
    """Cascata completa: banco → (opcional) web."""
    domain = from_database(db, company_name, linkedin_slug)
    if domain:
        return {"domain": domain, "confidence": 95, "source": "database"}
    if allow_network and company_name:
        return from_web(company_name, location)
    return None
