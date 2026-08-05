"""
Descoberta de domínio a partir do nome da empresa (e do LinkedIn, quando há).

Planilha de prospecção quase nunca tem a coluna "site" — a base real que
motivou isso tem 968 empresas e só 67 domínios confiáveis. Sem descobrir o
domínio, o enriquecimento (scraping, DNS/MX, decisores) não tem por onde
começar.

Estratégia: busca em engines (mesma cascata do LinkedIn), descarta redes
sociais e agregadores, e valida o candidato abrindo o site e comparando o
conteúdo com o nome da empresa. Devolve confiança para a UI decidir se
mostra como sugestão ou como dado.
"""
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ._ddg import search_multi
from ._utils import is_public_host, normalize_domain
from .scraper import scrape_website

logger = logging.getLogger(__name__)

# Domínios que nunca são o site da empresa
BLOCKED_HOSTS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "pinterest.com", "wikipedia.org", "glassdoor.com",
    "glassdoor.com.br", "indeed.com", "catho.com.br", "vagas.com.br", "infojobs.com.br",
    "gupy.io", "trabalhabrasil.com.br", "empregos.com.br", "reclameaqui.com.br",
    "econodata.com.br", "cnpj.biz", "casadosdados.com.br", "empresascnpj.com",
    "consultasocio.com", "solutudo.com.br", "apontador.com.br", "telelistas.net",
    "guiamais.com.br", "google.com", "bing.com", "duckduckgo.com", "amazon.com",
    "mercadolivre.com.br", "olx.com.br", "medium.com", "blogspot.com", "wordpress.com",
    "sympla.com.br", "eventbrite.com", "crunchbase.com", "zoominfo.com", "apollo.io",
    "rocketreach.co", "lusha.com", "6sense.com", "dnb.com", "bloomberg.com",
    "jusbrasil.com.br", "escavador.com", "gov.br", "sebrae.com.br",
}

# Padrão amplo: qualquer URL http(s) (a filtragem fina é nossa, não da engine)
_URL_PATTERN = r"https?://[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}(?:/[^\s\"'<>]*)?"

# Ruído comum em razão social — atrapalha a comparação por similaridade
_LEGAL_NOISE = re.compile(
    r"\b(ltda|s\s?a|sa|eireli|me|epp|mei|group|grupo|holding|comercio|comercial|"
    r"industria|industrial|servicos|solucoes|tecnologia|do brasil|brasil)\b"
)


def _slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _core_name(company_name: str) -> str:
    """Nome sem sufixo jurídico: 'Abix Tecnologia Ltda' → 'abix'."""
    text = unicodedata.normalize("NFKD", str(company_name or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    text = _LEGAL_NOISE.sub(" ", text)
    words = [w for w in text.split() if len(w) > 2]
    return "".join(words[:3]) if words else _slug(company_name)


def _root_host(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return normalize_domain(host)


def _is_blocked(host: str) -> bool:
    return any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS)


def _name_similarity(company_name: str, host: str) -> float:
    """0..1 entre o nome da empresa e o rótulo principal do domínio."""
    label = _slug(host.split(".")[0])
    if not label:
        return 0.0
    core = _core_name(company_name)
    full = _slug(company_name)
    if not core and not full:
        return 0.0
    if label in (core, full) or core in label or (len(label) > 3 and label in full):
        return 1.0
    return max(
        SequenceMatcher(None, core, label).ratio(),
        SequenceMatcher(None, full, label).ratio(),
    )


def _linkedin_slug(linkedin_url: Optional[str]) -> str:
    if not linkedin_url:
        return ""
    match = re.search(r"/company/([a-zA-Z0-9\-._%]+)", linkedin_url)
    return _slug(match.group(1)) if match else ""


def _candidates(company_name: str, location: Optional[str]) -> List[Dict]:
    """URLs plausíveis para o site oficial, em ordem de aparição nas engines."""
    queries = [f'"{company_name}" site oficial']
    if location:
        queries.append(f'{company_name} {location} site')
    queries.append(f'{company_name} empresa site')

    seen: set[str] = set()
    out: List[Dict] = []
    for query in queries:
        try:
            results = search_multi(query, pattern=_URL_PATTERN, timeout=10)
        except Exception as e:
            logger.debug("Busca de domínio falhou para %s: %s", company_name, e)
            results = []
        for item in results:
            host = _root_host(item.get("url", ""))
            if not host or host in seen or _is_blocked(host):
                continue
            seen.add(host)
            out.append({"host": host, "title": item.get("title") or ""})
        if len(out) >= 6:
            break
    return out[:6]


def _confirm_on_site(host: str, company_name: str) -> float:
    """Abre o site e mede se o conteúdo fala mesmo dessa empresa."""
    try:
        data = scrape_website(f"https://{host}")
    except Exception:
        return 0.0
    if not data or data.get("status") == "failed":
        return 0.0
    haystack = " ".join(
        str(data.get(k) or "") for k in ("company_name", "description", "raw_input_url")
    )
    core = _core_name(company_name)
    if core and core in _slug(haystack):
        return 1.0
    return _name_similarity(company_name, host)


def find_domain(
    company_name: str,
    linkedin_url: Optional[str] = None,
    location: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """
    Descobre o domínio da empresa.

    Retorna {domain, confidence, evidence}:
      high   — nome bate com o domínio (ou o site confirma) → pode enriquecer
      medium — plausível, mas sem confirmação forte → sugestão para o usuário
      none   — nada encontrado
    """
    name = (company_name or "").strip()
    if not name:
        return {"domain": None, "confidence": "none", "evidence": "sem nome de empresa"}

    li_slug = _linkedin_slug(linkedin_url)
    best: Optional[Dict] = None

    for candidate in _candidates(name, location):
        host = candidate["host"]
        if not is_public_host(host):
            continue
        score = _name_similarity(name, host)
        # O slug do LinkedIn costuma ser o nome da marca — bom desempate
        if li_slug:
            label = _slug(host.split(".")[0])
            if label and (label in li_slug or li_slug in label):
                score = max(score, 0.95)
        entry = {"host": host, "score": score, "title": candidate["title"]}
        if best is None or score > best["score"]:
            best = entry
        if score >= 0.8:
            break

    if not best:
        return {"domain": None, "confidence": "none", "evidence": "nenhum site encontrado"}

    if best["score"] < 0.8:
        # Nome e domínio não batem: confirma abrindo o site antes de aceitar
        confirmed = _confirm_on_site(best["host"], name)
        best["score"] = max(best["score"], confirmed)

    if best["score"] >= 0.8:
        confidence = "high"
    elif best["score"] >= 0.5:
        confidence = "medium"
    else:
        return {
            "domain": None, "confidence": "none",
            "evidence": f'melhor candidato {best["host"]} não confere com o nome',
        }

    logger.info(
        "Domínio descoberto empresa=%s domain=%s confidence=%s",
        name, best["host"], confidence,
    )
    return {
        "domain": best["host"],
        "confidence": confidence,
        "evidence": best["title"][:200] or f'busca por "{name}"',
    }
