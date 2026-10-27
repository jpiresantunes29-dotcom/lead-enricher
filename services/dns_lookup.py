"""
DNS lookup completo no estilo DNS Dumpster.

Coleta para um domínio:
  - MX (com IP, PTR reverso, ASN, rede, organização e país de cada hostname)
  - A, AAAA (do raiz e do www)
  - NS
  - TXT (com extração estruturada de SPF, DMARC, DKIM, verifications)
  - SOA

Identifica:
  - Provedor de email (Microsoft 365, Google Workspace, Zoho, Proofpoint...),
    separando o gateway de segurança da caixa postal que fica atrás dele
  - Situação do MX: publicado, MX nulo (RFC 7505), implícito (só A), herdado
    do domínio raiz, ou DNS sem resposta
  - Provedor de hosting (via ASN do A record)
  - Confiança alta (match exato no MX primário), média (backup/ASN), baixa
    (fallback domínio)
"""
import logging
import os
import re
import dns.resolver
import dns.reversename
import dns.exception
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import List, Optional

try:
    from ipwhois import IPWhois
    IPWHOIS_OK = True
except Exception:
    IPWHOIS_OK = False

from . import dns_resolver
from ._utils import (
    TWO_LEVEL_SUFFIXES, host_matches, normalize_domain, registrable_domain,
)

logger = logging.getLogger(__name__)

# Consulta RDAP/WHOIS é a etapa mais lenta do relatório (rede, sem SLA), então
# roda sempre em paralelo e com teto de tempo.
WHOIS_TIMEOUT = int(os.getenv("WHOIS_TIMEOUT", "4"))
# Quantos servidores MX ganham a linha completa (IP, PTR, ASN, rede, país) no
# painel "Infraestrutura de e-mail". Praticamente todo domínio publica no
# máximo 4-5 MX; acima disso é cauda de fallback que não muda a leitura.
MX_DETAIL_MAX = int(os.getenv("MX_DETAIL_MAX", "4"))


# ============================================================
# Padrões de provedores (expandido — estilo DNS Dumpster)
# Match por SUFIXO do hostname MX (ver `host_matches`): o padrão precisa ser o
# domínio inteiro do provedor, rótulo por rótulo — "uol.com.br" não pode casar
# com "bauol.com.br".
# ============================================================
PROVIDER_PATTERNS = [
    # Microsoft. `mx.microsoft` é o MX com DNSSEC que o Microsoft 365 passou a
    # entregar para domínios novos (<tenant>.mx.microsoft).
    ("mail.protection.outlook.com", "Microsoft 365"),
    ("protection.outlook.com", "Microsoft 365"),
    ("mx.microsoft", "Microsoft 365"),
    ("outlook.com", "Microsoft 365"),
    ("hotmail.com", "Microsoft (Hotmail)"),
    ("live.com", "Microsoft (Live)"),
    # Google
    ("aspmx.l.google.com", "Google Workspace"),
    ("googlemail.com", "Google Workspace"),
    ("google.com", "Google Workspace"),
    ("googlehosted.com", "Google Workspace"),
    # PTR dos servidores de e-mail do Google. Só aparece pelo reverso, quando
    # o MX usa nome próprio do cliente apontando para o Google.
    ("1e100.net", "Google Workspace"),
    # Zoho
    ("zoho.com", "Zoho Mail"),
    ("zohomail.com", "Zoho Mail"),
    ("zohomail.eu", "Zoho Mail"),
    ("zoho.eu", "Zoho Mail"),
    ("zoho.in", "Zoho Mail"),
    ("zoho.com.au", "Zoho Mail"),
    # Yahoo
    ("yahoodns.net", "Yahoo Business Mail"),
    ("yahoo.com", "Yahoo Mail"),
    # Email security (gateways)
    ("pphosted.com", "Proofpoint"),
    ("proofpoint.com", "Proofpoint"),
    ("ppe-hosted.com", "Proofpoint Essentials"),
    ("mimecast.com", "Mimecast"),
    ("mimecast.co.za", "Mimecast"),
    ("mimecast-offshore.com", "Mimecast"),
    ("barracudanetworks.com", "Barracuda"),
    ("barracuda.com", "Barracuda"),
    ("iphmx.com", "Cisco IronPort"),
    ("ironport.com", "Cisco IronPort"),
    ("cisco.com", "Cisco"),
    ("messagelabs.com", "Symantec MessageLabs"),
    ("symanteccloud.com", "Symantec.cloud"),
    ("hydrasophos.com", "Sophos Hydra"),
    ("sophos.com", "Sophos"),
    ("trendmicro.com", "Trend Micro"),
    ("trendmicro.eu", "Trend Micro"),
    ("mailcontrol.com", "Forcepoint"),
    ("hornetsecurity.com", "Hornetsecurity"),
    ("spamtitan.com", "SpamTitan"),
    ("antispamcloud.com", "AntiSpamCloud"),
    ("mxlogic.net", "McAfee MXLogic"),
    ("mailfilter.io", "MailFilter"),
    ("spamh.com", "SpamH"),
    # mx10/mx20/mx30.mailspamprotection.com é o MX padrão da SiteGround, não
    # da Locaweb — o rótulo antigo punha a empresa errada na ficha.
    ("mailspamprotection.com", "SiteGround"),
    ("gatefy.com", "Gatefy"),
    # Cloudflare
    ("mx.cloudflare.net", "Cloudflare Email Routing"),
    ("cloudflare.net", "Cloudflare Email Routing"),
    # Hosts BR
    ("locaweb.com.br", "Locaweb"),
    ("kinghost.net", "KingHost"),
    ("kinghost.com.br", "KingHost"),
    ("hostgator.com.br", "HostGator BR"),
    ("hostgator.com", "HostGator"),
    ("uolhost.com.br", "UOL Host"),
    ("uhserver.com", "UOL Host"),
    ("uol.com.br", "UOL Host"),
    ("unifique.com.br", "Unifique"),
    ("skymail.net.br", "Skymail"),
    ("skymail.com.br", "Skymail"),
    ("hostinger.com", "Hostinger"),
    ("hostinger.com.br", "Hostinger"),
    ("umbler.com", "Umbler"),
    ("brasileiro.com.br", "Provedor BR"),
    ("registro.br", "Registro.br"),
    # Hosts internacionais
    ("titan.email", "Titan Mail"),
    ("hover.com", "Hover"),
    ("namecheap.com", "Namecheap Email"),
    ("privateemail.com", "Namecheap Private Email"),
    ("godaddy.com", "GoDaddy"),
    ("secureserver.net", "GoDaddy"),
    ("rackspace.com", "Rackspace Email"),
    ("emailsrvr.com", "Rackspace Email"),
    ("mailgun.org", "Mailgun"),
    ("mailgun.net", "Mailgun"),
    ("amazonses.com", "Amazon SES"),
    ("amazonaws.com", "Amazon AWS"),
    ("postmarkapp.com", "Postmark"),
    ("sendgrid.net", "SendGrid"),
    ("sparkpostmail.com", "SparkPost"),
    # Email pessoal/dev focused
    ("fastmail.com", "Fastmail"),
    ("messagingengine.com", "Fastmail"),
    ("protonmail.ch", "ProtonMail"),
    ("protonmail.com", "ProtonMail"),
    ("tutanota.com", "Tutanota"),
    ("tuta.com", "Tutanota"),
    ("migadu.com", "Migadu"),
    ("mailfence.com", "Mailfence"),
    ("posteo.de", "Posteo"),
    ("mailbox.org", "Mailbox.org"),
    ("runbox.com", "Runbox"),
    ("hey.com", "HEY (Basecamp)"),
    ("skiff.com", "Skiff Mail"),
    # Outros
    ("yandex.net", "Yandex"),
    ("yandex.ru", "Yandex"),
    ("mail.ru", "Mail.ru"),
    ("hetzner.com", "Hetzner"),
    ("ovh.net", "OVH"),
    ("ovh.com", "OVH"),
    ("ionos.com", "IONOS"),
    ("1and1.com", "1&1 IONOS"),
    ("registrar-servers.com", "Namecheap"),
]

# Serviços que ficam NA FRENTE da caixa postal: filtram (ou só encaminham) e
# entregam para outro provedor. O MX mostra o gateway; quem guarda o e-mail
# da empresa é outra coisa, e quem vende para ela precisa saber as duas.
GATEWAY_PROVIDERS = frozenset({
    "Proofpoint", "Proofpoint Essentials", "Mimecast", "Barracuda",
    "Cisco IronPort", "Cisco", "Symantec MessageLabs", "Symantec.cloud",
    "Sophos Hydra", "Sophos", "Trend Micro", "AntiSpamCloud",
    "McAfee MXLogic", "MailFilter", "SpamH", "Gatefy", "Forcepoint",
    "Hornetsecurity", "SpamTitan", "Cloudflare Email Routing",
})

# Provedores que o PTR não pode afirmar: são nuvem ou hospedagem genérica, e o
# reverso de uma máquina deles diz onde o servidor roda, não quem é o serviço
# de e-mail. MX próprio numa EC2 tem PTR "...compute-1.amazonaws.com" — e
# isso não faz da empresa cliente de e-mail da Amazon.
_PTR_NOT_EVIDENCE = frozenset({
    "Amazon AWS", "Hetzner", "OVH", "IONOS", "1&1 IONOS", "GoDaddy",
    "Cloudflare Email Routing",
})

# Includes do SPF que entregam a CAIXA POSTAL por trás de um gateway. Só
# provedores de caixa postal: SendGrid, Mailchimp, Amazon SES e afins também
# aparecem no SPF, mas disparam marketing — não guardam o e-mail de ninguém.
MAILBOX_SPF_PATTERNS = [
    ("protection.outlook.com", "Microsoft 365"),
    ("outlook.com", "Microsoft 365"),
    ("google.com", "Google Workspace"),
    ("googlemail.com", "Google Workspace"),
    ("zoho.com", "Zoho Mail"),
    ("zoho.eu", "Zoho Mail"),
    ("zohomail.com", "Zoho Mail"),
    ("locaweb.com.br", "Locaweb"),
    ("kinghost.net", "KingHost"),
    ("uhserver.com", "UOL Host"),
    ("uol.com.br", "UOL Host"),
    ("skymail.net.br", "Skymail"),
    ("titan.email", "Titan Mail"),
    ("hostinger.com", "Hostinger"),
    ("hostgator.com.br", "HostGator BR"),
    ("secureserver.net", "GoDaddy"),
    ("emailsrvr.com", "Rackspace Email"),
    ("messagingengine.com", "Fastmail"),
    ("protonmail.ch", "ProtonMail"),
    ("yandex.net", "Yandex"),
]

# ASN substring → provedor (fallback quando hostname não bate)
ASN_PATTERNS = [
    ("GOOGLE", "Google Workspace"),
    ("MICROSOFT", "Microsoft 365"),
    ("AMAZON", "Amazon AWS"),
    ("CLOUDFLARE", "Cloudflare"),
    ("PROOFPOINT", "Proofpoint"),
    ("MIMECAST", "Mimecast"),
    ("BARRACUDA", "Barracuda"),
    ("ZOHO", "Zoho Mail"),
    ("FASTLY", "Fastly"),
    ("AKAMAI", "Akamai"),
    ("DIGITALOCEAN", "DigitalOcean"),
    ("HETZNER", "Hetzner"),
    ("OVH", "OVH"),
    ("LOCAWEB", "Locaweb"),
    ("UOL", "UOL Host"),
    ("HOSTGATOR", "HostGator"),
    ("KINGHOST", "KingHost"),
    ("GODADDY", "GoDaddy"),
    ("NAMECHEAP", "Namecheap"),
    ("RACKSPACE", "Rackspace"),
]


def _resolve_status(domain: str, rtype: str, lifetime: float = 5) -> tuple:
    """
    `(rdatas, status)`, com status "ok" | "empty" | "nxdomain" | "unreachable".

    A lista vazia sozinha não basta para o MX: "o domínio não publica MX",
    "o domínio não existe" e "ninguém respondeu" pedem leituras diferentes na
    ficha — só as duas primeiras autorizam dizer que não há e-mail ali.
    """
    try:
        return list(dns_resolver.resolve(domain, rtype, lifetime=lifetime)), "ok"
    except dns.resolver.NXDOMAIN:
        return [], "nxdomain"
    except dns.resolver.NoAnswer:
        # Resposta legítima: o domínio existe e não publica esse registro.
        return [], "empty"
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as e:
        # Aqui ninguém respondeu — nem o sistema, nem o fallback. É um buraco
        # no relatório, não um domínio sem registro, e precisa aparecer no log
        # para não virar "ficha vazia" sem explicação.
        logger.warning("DNS sem resposta domain=%s type=%s: %s",
                       domain, rtype, type(e).__name__)
        return [], "unreachable"
    except Exception as e:
        logger.warning("DNS erro inesperado domain=%s type=%s: %s", domain, rtype, e)
        return [], "unreachable"


def _resolve(domain: str, rtype: str, lifetime: int = 5) -> List:
    return _resolve_status(domain, rtype, lifetime)[0]


# ============================================================
# MX: registros publicados, MX nulo, MX implícito, subdomínio
# ============================================================

def parse_mx_answers(rdatas) -> dict:
    """
    `{"records": [{priority, host}], "null_mx": bool}` a partir das respostas.

    - Ordena por prioridade (menor = preferido) e, no empate, por nome: o
      "servidor primário" da ficha tem de ser o mesmo a cada coleta, não o
      que o DNS devolveu primeiro desta vez.
    - Host repetido com duas prioridades fica com a menor — é a que vale para
      quem entrega.
    - MX nulo (RFC 7505, `0 .`) é a declaração explícita de que o domínio NÃO
      recebe e-mail. Só vale quando é o único registro; misturado com MX reais
      é erro de configuração, e os reais continuam recebendo.
    """
    hosts = {}
    null_mx = False
    for r in rdatas or []:
        host = str(r.exchange).rstrip(".").lower()
        if not host:
            null_mx = True
            continue
        pref = int(r.preference)
        if host not in hosts or pref < hosts[host]:
            hosts[host] = pref
    records = sorted(
        ({"priority": p, "host": h} for h, p in hosts.items()),
        key=lambda r: (r["priority"], r["host"]),
    )
    return {"records": records, "null_mx": null_mx and not records}


def _mx_of(name: str) -> dict:
    """MX de um nome só, sem fallback: {status, records, alias_of}."""
    try:
        answer = dns_resolver.resolve(name, "MX", lifetime=6)
    except dns.resolver.NXDOMAIN:
        return {"status": "nxdomain", "records": [], "alias_of": None}
    except dns.resolver.NoAnswer:
        return {"status": "none", "records": [], "alias_of": None}
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as e:
        logger.warning("DNS sem resposta domain=%s type=MX: %s", name, type(e).__name__)
        return {"status": "unreachable", "records": [], "alias_of": None}
    except Exception as e:
        logger.warning("DNS erro inesperado domain=%s type=MX: %s", name, e)
        return {"status": "unreachable", "records": [], "alias_of": None}

    # Domínio que é CNAME de outro herda o MX do destino — o resolver já
    # seguiu o apelido, e o painel deve dizer de onde o MX veio de fato.
    canonical = str(getattr(answer, "canonical_name", "") or "").rstrip(".").lower()
    alias_of = canonical if canonical and canonical != name else None

    parsed = parse_mx_answers(answer)
    if parsed["records"]:
        return {"status": "ok", "records": parsed["records"], "alias_of": alias_of}
    if parsed["null_mx"]:
        return {"status": "null_mx", "records": [], "alias_of": alias_of}
    return {"status": "none", "records": [], "alias_of": alias_of}


def lookup_mx(domain: str) -> dict:
    """
    MX publicado para o domínio, com o motivo quando não há:

      status        ok | null_mx | implicit | none | nxdomain | unreachable
      records       [{priority, host}] em ordem de preferência
      source_domain de qual nome o MX veio (o próprio ou o domínio raiz)
      alias_of      destino do CNAME, quando o domínio é um apelido
      implicit_host host que receberia por MX implícito (RFC 5321 §5.1)

    Subdomínio sem MX ("vendas.empresa.com.br") cai para o domínio raiz: o
    e-mail da empresa mora lá, e "sem MX" seria falso para quem vai escrever.

    Sem MX nenhum, o protocolo manda entregar no A do próprio domínio (MX
    implícito). Isso é registrado como tal e NUNCA vira linha de MX nem
    provedor: o A aponta para o servidor do SITE, e afirmar que a hospedagem
    do site é o provedor de e-mail seria inventar dado.

    "unreachable" não cai em fallback nenhum: DNS que não respondeu não prova
    ausência, e a ficha não pode dizer "não recebe e-mail" por causa disso.
    """
    domain = normalize_domain(domain)
    own = _mx_of(domain)
    out = {"status": own["status"], "records": own["records"],
           "source_domain": domain, "alias_of": own["alias_of"],
           "implicit_host": None}
    if own["status"] in ("ok", "null_mx", "unreachable"):
        return out

    org = registrable_domain(domain)
    if org and org != domain:
        parent = _mx_of(org)
        if parent["status"] in ("ok", "null_mx"):
            out.update(status=parent["status"], records=parent["records"],
                       source_domain=org, alias_of=parent["alias_of"])
            return out

    if own["status"] == "nxdomain":
        return out

    a_records, _ = _resolve_status(domain, "A")
    if a_records:
        out.update(status="implicit", implicit_host=domain)
    return out


def _cname_target(host: str) -> Optional[str]:
    """Destino do CNAME de um host (MX com nome próprio apelidando o provedor)."""
    answers = _resolve(host, "CNAME", lifetime=3)
    if not answers:
        return None
    target = getattr(answers[0], "target", answers[0])
    return str(target).rstrip(".").lower() or None


def spf_includes(spf: Optional[str]) -> List[str]:
    """Domínios dos `include:` e do `redirect=` de um SPF, em ordem."""
    out = []
    for token in (spf or "").split():
        body = token.lstrip("+-~?").lower()
        if body.startswith("include:"):
            out.append(body.split(":", 1)[1].strip("."))
        elif body.startswith("redirect="):
            out.append(body.split("=", 1)[1].strip("."))
    return [d for d in out if d]


def mailbox_from_spf(spf: Optional[str]) -> tuple:
    """
    `(provedor, candidatos)` da caixa postal segundo o SPF.

    O provedor só sai quando o SPF aponta para UM provedor de caixa postal.
    Dois (Google e Microsoft juntos, comum no meio de uma migração) é
    ambíguo — os dois vão em `candidatos` e a ficha não escolhe por sorteio.
    """
    found = []
    for include in spf_includes(spf):
        for pattern, name in MAILBOX_SPF_PATTERNS:
            if host_matches(include, pattern):
                if name not in found:
                    found.append(name)
                break
    return (found[0] if len(found) == 1 else None), found


def _known_provider(host: Optional[str]) -> Optional[str]:
    """Provedor pelo padrão de hostname, ou None — sem ASN nem palpite."""
    if not host:
        return None
    name, conf = identify_provider(host, None)
    return name if conf == "high" else None


def summarize_mail_provider(mx_records: List[dict], spf: Optional[str] = None,
                            resolve_cname=None) -> dict:
    """
    Quem opera o e-mail da empresa, a partir das linhas de MX (com `ptr` e
    `asn_org` quando houver) e do SPF.

    Ordem das provas, da mais forte para a mais fraca:
      1. hostname de um MX PRIMÁRIO (menor prioridade) bate com o provedor
      2. CNAME do MX primário bate (nome próprio apelidando o provedor)
      3. PTR do IP do MX primário bate (nome próprio com IP do provedor)
      4. um MX de BACKUP bate — "medium": backup no Google atrás de um
         servidor próprio não faz da empresa cliente do Google Workspace
      5. ASN do MX — "medium"; 6. nome do domínio do MX — "low"

    Antes, bastava QUALQUER MX bater para afirmar o provedor com confiança
    alta, mesmo o de prioridade 50 que só recebe quando o primário cai.

    Quando o primário é um gateway (Proofpoint, Mimecast...), o SPF diz qual
    caixa postal está atrás dele. `mx_provider` sai no formato que o
    classificador de tier já usa — "Microsoft 365 (via Proofpoint)" — e os
    dois papéis também vão separados em `mx_gateway` e `mailbox_provider`.
    """
    out = {
        "mx_provider": None, "mx_provider_confidence": "none",
        "mx_provider_evidence": None, "mx_primary": None,
        "mx_gateway": None, "mailbox_provider": None, "mailbox_evidence": None,
        "mailbox_candidates": [],
    }
    if not mx_records:
        return out
    ordered = sorted(mx_records, key=lambda r: (r["priority"], r["host"]))
    top = ordered[0]["priority"]
    primary = [r for r in ordered if r["priority"] == top]
    backup = [r for r in ordered if r["priority"] != top]
    out["mx_primary"] = primary[0]["host"]

    name, conf, evidence = None, "none", None
    for rec in primary:
        name = _known_provider(rec["host"])
        if name:
            conf, evidence = "high", "mx_host"
            break
    if not name and resolve_cname:
        for rec in primary:
            try:
                target = resolve_cname(rec["host"])
            except Exception:
                target = None
            name = _known_provider(target)
            if name:
                conf, evidence = "high", "mx_cname"
                break
    if not name:
        for rec in primary:
            candidate = _known_provider(rec.get("ptr"))
            if candidate and candidate not in _PTR_NOT_EVIDENCE:
                name, conf, evidence = candidate, "high", "mx_ptr"
                break
    if not name:
        for rec in backup:
            name = _known_provider(rec["host"])
            if name:
                conf, evidence = "medium", "mx_backup"
                break
    if not name:
        for rec in ordered:
            candidate, candidate_conf = identify_provider(rec["host"], rec.get("asn_org"))
            if candidate and candidate_conf == "medium":
                name, conf, evidence = candidate, "medium", "asn"
                break
    if not name:
        name, conf = identify_provider(primary[0]["host"], primary[0].get("asn_org"))
        evidence = "hostname" if name else None

    if not name:
        return out

    out.update(mx_provider=name, mx_provider_confidence=conf,
               mx_provider_evidence=evidence)
    if name in GATEWAY_PROVIDERS:
        out["mx_gateway"] = name
        mailbox, candidates = mailbox_from_spf(spf)
        out["mailbox_candidates"] = candidates
        if mailbox:
            out.update(mailbox_provider=mailbox, mailbox_evidence="spf",
                       mx_provider=f"{mailbox} (via {name})")
    elif conf == "high":
        out.update(mailbox_provider=name, mailbox_evidence=evidence)
    return out


@lru_cache(maxsize=512)
def _resolve_a(host: str) -> Optional[str]:
    answers = _resolve(host, "A")
    if answers:
        return str(answers[0])
    return None


_EMPTY_IP_INFO = {"asn": None, "asn_org": None, "asn_cidr": None,
                  "country": None, "country_name": None}

# Código ISO → país em português. O painel mostra "Brasil", não "BR": quem lê a
# ficha é vendedor, não analista de rede. Códigos fora da lista caem no próprio
# código (melhor que campo vazio).
COUNTRY_NAMES = {
    "BR": "Brasil", "US": "Estados Unidos", "CA": "Canadá", "MX": "México",
    "AR": "Argentina", "CL": "Chile", "CO": "Colômbia", "PE": "Peru",
    "UY": "Uruguai", "PY": "Paraguai", "BO": "Bolívia", "EC": "Equador",
    "PT": "Portugal", "ES": "Espanha", "FR": "França", "DE": "Alemanha",
    "IT": "Itália", "GB": "Reino Unido", "IE": "Irlanda", "NL": "Países Baixos",
    "BE": "Bélgica", "CH": "Suíça", "AT": "Áustria", "SE": "Suécia",
    "NO": "Noruega", "DK": "Dinamarca", "FI": "Finlândia", "PL": "Polônia",
    "CZ": "Tchéquia", "RO": "Romênia", "RU": "Rússia", "UA": "Ucrânia",
    "TR": "Turquia", "IL": "Israel", "AE": "Emirados Árabes Unidos",
    "SA": "Arábia Saudita", "ZA": "África do Sul", "IN": "Índia",
    "CN": "China", "HK": "Hong Kong", "TW": "Taiwan", "JP": "Japão",
    "KR": "Coreia do Sul", "SG": "Singapura", "AU": "Austrália",
    "NZ": "Nova Zelândia",
}


_ASN_PREFIX_RE = re.compile(r"^AS\d+\s*[-–]\s*", re.I)
_ASN_COUNTRY_SUFFIX_RE = re.compile(r",\s*[A-Za-z]{2}$")


def clean_asn_org(asn_org: Optional[str]) -> Optional[str]:
    """
    'AS265262 - Skymail Servicos de Computacao, BR' → 'Skymail Servicos de
    Computacao'. A string crua do RDAP serve na tabela técnica; no campo
    "Hosting" da ficha só atrapalha a leitura.
    """
    if not asn_org:
        return None
    text = _ASN_COUNTRY_SUFFIX_RE.sub("", _ASN_PREFIX_RE.sub("", asn_org.strip()))
    return text.strip() or None


def country_label(code: Optional[str]) -> Optional[str]:
    """'BR' → 'Brasil'. Desconhecido volta como veio."""
    if not code:
        return None
    return COUNTRY_NAMES.get(code.upper(), code.upper())


@lru_cache(maxsize=512)
def _ip_info(ip: str) -> dict:
    """
    Lookup ASN/org/rede via ipwhois (RDAP). Cacheado por IP.

    Com timeout curto: sem ele, um servidor WHOIS lento sozinho estoura o
    tempo da requisição inteira (era o gargalo de ~80 s por domínio).
    """
    if not IPWHOIS_OK or not ip:
        return dict(_EMPTY_IP_INFO)
    try:
        result = IPWhois(ip, timeout=WHOIS_TIMEOUT).lookup_rdap(
            asn_methods=["whois", "http"], retry_count=0,
        )
        country = result.get("asn_country_code")
        return {
            "asn": result.get("asn"),
            "asn_org": (result.get("asn_description") or "").strip(),
            "asn_cidr": result.get("asn_cidr"),
            "country": country,
            "country_name": country_label(country),
        }
    except Exception as e:
        logger.debug("Lookup de ASN falhou ip=%s: %s", ip, e)
        return dict(_EMPTY_IP_INFO)


@lru_cache(maxsize=512)
def _ptr(ip: str) -> Optional[str]:
    """
    Hostname reverso (PTR) do IP. É o que revela o dono real da máquina quando
    o MX usa nome próprio do cliente — 'mx-ha.empresa.com.br' apontando para um
    PTR 'mx-ha.skymail.net.br' entrega o provedor terceirizado.
    """
    if not ip:
        return None
    try:
        rev = str(dns.reversename.from_address(ip))
    except Exception:
        return None
    answers = _resolve(rev, "PTR", lifetime=3)
    if answers:
        return str(answers[0]).rstrip(".").lower()
    return None


# Sufixos públicos de dois níveis. Sem eles, o fallback de "mx.skymail.net.br"
# devolvia "Net.Br" — o sufixo, não o provedor — direto no campo do vendedor.
# A lista mora em _utils porque o site oficial e o MX de subdomínio usam a
# mesma régua.
_TWO_LEVEL_SUFFIXES = TWO_LEVEL_SUFFIXES


def registrable_name(hostname: str) -> Optional[str]:
    """
    Nome comercial provável a partir do hostname: 'mx-ha.skymail.net.br' →
    'Skymail'. Usado só como último recurso, quando nem o hostname conhecido
    nem o ASN identificaram o provedor.
    """
    host = (hostname or "").strip(".").lower()
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return None
    suffix = ".".join(parts[-2:])
    label = parts[-3] if (suffix in _TWO_LEVEL_SUFFIXES and len(parts) >= 3) else parts[-2]
    return label.replace("-", " ").title() if label else None


def identify_provider(hostname: str, asn_org: Optional[str]) -> tuple:
    """
    Identifica provedor por (hostname, asn_org).
    Retorna (nome, confiança: high|medium|low).
    """
    host = (hostname or "").lower().rstrip(".")
    for pattern, name in PROVIDER_PATTERNS:
        if host_matches(host, pattern):
            return name, "high"
    if asn_org:
        upper = asn_org.upper()
        for pattern, name in ASN_PATTERNS:
            if pattern in upper:
                return name, "medium"
    # Fallback: nome registrável do hostname
    name = registrable_name(host)
    if name:
        return name, "low"
    return None, "none"


def _classify_txt(txt: str) -> Optional[str]:
    """Classifica registro TXT em uma categoria conhecida."""
    t = txt.lower()
    if t.startswith("v=spf1"):
        return "spf"
    if t.startswith("v=dmarc1"):
        return "dmarc"
    if t.startswith("v=dkim1"):
        return "dkim"
    if "google-site-verification" in t:
        return "google_verify"
    if "ms=" in t[:5] or "msv1" in t:
        return "ms_verify"
    if "facebook-domain-verification" in t:
        return "fb_verify"
    if "atlassian-domain-verification" in t:
        return "atlassian_verify"
    return None


def _txt_unquote(rdata) -> str:
    """Concatena strings TXT e remove aspas."""
    try:
        return "".join(s.decode() if isinstance(s, bytes) else s for s in rdata.strings)
    except Exception:
        return str(rdata).strip('"')


def get_dns_report(domain_input: str) -> dict:
    """
    Coleta relatório DNS completo estilo DNS Dumpster.

    Retorna dict com:
      domain, mx, a, aaaa, ns, ns_records, txt, spf, dmarc, soa,
      mx_provider, mx_provider_confidence,
      hosting_provider, hosting_confidence, hosting_asn, hosting_asn_org,
      hosting_country

    Cada item de `mx` traz priority, host, ip, ptr, asn, asn_org, asn_cidr,
    country e country_name — é o que o painel "Infraestrutura de e-mail"
    desenha linha a linha.
    """
    domain = normalize_domain(domain_input)

    report = {
        "domain": domain,
        "mx": [],
        "a": [],
        "aaaa": [],
        "ns": [],
        "ns_records": [],
        "txt": [],
        "spf": None,
        "dmarc": None,
        "dkim_records": [],
        "verifications": [],
        "soa": None,
        "mx_provider": None,
        "mx_provider_confidence": "none",
        # Por que o MX está (ou não) aí — ver `lookup_mx`. Sem isto, "sem MX"
        # (o domínio não recebe e-mail) e "DNS não respondeu" (não sabemos)
        # saíam iguais na ficha.
        "mx_status": None,
        "mx_source_domain": None,
        "mx_alias_of": None,
        "mx_implicit_host": None,
        "mx_primary": None,
        "mx_provider_evidence": None,
        # Gateway de segurança na frente e caixa postal atrás, separados.
        "mx_gateway": None,
        "mailbox_provider": None,
        "mailbox_evidence": None,
        "mailbox_candidates": [],
        "hosting_provider": None,
        "hosting_confidence": "none",
        "hosting_asn": None,
        "hosting_asn_org": None,
        "hosting_country": None,
    }

    # Todas as consultas DNS são independentes entre si — em paralelo o
    # relatório inteiro custa o tempo da consulta mais lenta, não a soma.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            "mx": pool.submit(lookup_mx, domain),
            "a": pool.submit(_resolve, domain, "A"),
            "a_www": pool.submit(_resolve, f"www.{domain}", "A"),
            "aaaa": pool.submit(_resolve, domain, "AAAA"),
            "aaaa_www": pool.submit(_resolve, f"www.{domain}", "AAAA"),
            "ns": pool.submit(_resolve, domain, "NS"),
            "txt": pool.submit(_resolve, domain, "TXT"),
            "dmarc": pool.submit(_resolve, f"_dmarc.{domain}", "TXT"),
            "soa": pool.submit(_resolve, domain, "SOA"),
        }
        answers = {}
        for key, future in futures.items():
            try:
                answers[key] = future.result(timeout=12)
            except Exception:
                answers[key] = []

    # --- MX ---
    # `lookup_mx` já devolve as linhas deduplicadas e na ordem de preferência,
    # sem o MX nulo ("."), e diz por que não há MX quando não há.
    mx_lookup = answers["mx"] if isinstance(answers["mx"], dict) else {
        "status": "unreachable", "records": [], "source_domain": domain,
        "alias_of": None, "implicit_host": None,
    }
    mx_hosts = list(mx_lookup["records"])
    report["mx_status"] = mx_lookup["status"]
    report["mx_source_domain"] = mx_lookup["source_domain"]
    report["mx_alias_of"] = mx_lookup["alias_of"]
    report["mx_implicit_host"] = mx_lookup["implicit_host"]

    # IPs dos MX e dos NS na mesma leva: são só consultas A, baratas, e o
    # painel mostra o IP das duas famílias de registro.
    ns_hosts = sorted({str(r).rstrip(".").lower() for r in answers["ns"]})
    resolve_hosts = [rec["host"] for rec in mx_hosts[:8]] + ns_hosts[:MX_DETAIL_MAX]
    host_ips = {}
    if resolve_hosts:
        with ThreadPoolExecutor(max_workers=8) as pool:
            ip_futures = {h: pool.submit(_resolve_a, h) for h in dict.fromkeys(resolve_hosts)}
            for host, future in ip_futures.items():
                try:
                    host_ips[host] = future.result(timeout=8)
                except Exception:
                    host_ips[host] = None
    mx_ips = {rec["host"]: host_ips.get(rec["host"]) for rec in mx_hosts}

    # --- A / AAAA (raiz e www) ---
    report["a"] = sorted({str(r) for r in answers["a"]} | {str(r) for r in answers["a_www"]})
    report["aaaa"] = sorted({str(r) for r in answers["aaaa"]} | {str(r) for r in answers["aaaa_www"]})

    # O painel "Infraestrutura de e-mail" mostra ASN, rede e país em CADA linha
    # de MX — então o WHOIS deixou de ser condicionado a "o hostname não
    # identificou o provedor" e passa a rodar para todos os MX detalhados.
    # O custo não explode porque tudo vai num único pool paralelo (o relatório
    # espera o lookup mais lento, não a soma) e o resultado é cacheado por IP —
    # Google, Microsoft e Locaweb se repetem entre domínios.
    whois_targets = [ip for ip in (mx_ips.get(r["host"]) for r in mx_hosts[:MX_DETAIL_MAX]) if ip]
    if report["a"]:
        whois_targets.append(report["a"][0])
    whois_targets = list(dict.fromkeys(whois_targets))
    # PTR é consulta DNS comum (barata): vale para todo MX que tenha IP,
    # inclusive os que ficaram de fora do teto do WHOIS.
    ptr_targets = list(dict.fromkeys([ip for ip in mx_ips.values() if ip] + whois_targets))

    whois_info = {}
    ptr_info = {}
    if whois_targets or ptr_targets:
        with ThreadPoolExecutor(max_workers=10) as pool:
            whois_futures = {ip: pool.submit(_ip_info, ip) for ip in whois_targets}
            ptr_futures = {ip: pool.submit(_ptr, ip) for ip in ptr_targets}
            for ip, future in whois_futures.items():
                try:
                    whois_info[ip] = future.result(timeout=WHOIS_TIMEOUT + 2)
                except Exception:
                    whois_info[ip] = dict(_EMPTY_IP_INFO)
            for ip, future in ptr_futures.items():
                try:
                    ptr_info[ip] = future.result(timeout=5)
                except Exception:
                    ptr_info[ip] = None

    mx_records = []
    for rec in mx_hosts:
        ip = mx_ips.get(rec["host"])
        info = whois_info.get(ip) or dict(_EMPTY_IP_INFO)
        mx_records.append({
            "priority": rec["priority"],
            "host": rec["host"],
            "ip": ip,
            "ptr": ptr_info.get(ip),
            "asn": info["asn"],
            "asn_org": info["asn_org"],
            "asn_cidr": info.get("asn_cidr"),
            "country": info["country"],
            "country_name": info.get("country_name") or country_label(info["country"]),
        })
    report["mx"] = mx_records

    # Hosting provider via ASN do primeiro A
    if report["a"]:
        info = whois_info.get(report["a"][0]) or dict(_EMPTY_IP_INFO)
        report["hosting_asn"] = info["asn"]
        report["hosting_asn_org"] = info["asn_org"]
        report["hosting_country"] = info.get("country_name") or country_label(info["country"])
        if info["asn_org"]:
            name, conf = identify_provider("", info["asn_org"])
            if name:
                report["hosting_provider"] = name
                report["hosting_confidence"] = conf
            else:
                report["hosting_provider"] = clean_asn_org(info["asn_org"])
                report["hosting_confidence"] = "medium"

    # --- NS ---
    # `ns` continua lista de strings (consumidores antigos); `ns_records` é a
    # versão com IP que o painel usa.
    report["ns"] = ns_hosts
    report["ns_records"] = [
        {"host": h, "ip": host_ips.get(h)} for h in ns_hosts
    ]

    # --- TXT ---
    txt_list = []
    for rdata in answers["txt"]:
        txt = _txt_unquote(rdata)
        txt_list.append(txt)
        kind = _classify_txt(txt)
        if kind == "spf" and not report["spf"]:
            report["spf"] = txt
        elif kind == "dmarc":
            report["dmarc"] = txt
        elif kind == "dkim":
            report["dkim_records"].append(txt)
        elif kind in ("google_verify", "ms_verify", "fb_verify", "atlassian_verify"):
            report["verifications"].append({"type": kind, "value": txt})
    report["txt"] = txt_list

    # DMARC fica em _dmarc.<domain>
    if not report["dmarc"]:
        for rdata in answers["dmarc"]:
            txt = _txt_unquote(rdata)
            if txt.lower().startswith("v=dmarc1"):
                report["dmarc"] = txt
                break

    # --- Provedor de e-mail ---
    # Depois do TXT de propósito: com gateway na frente, é o SPF que diz qual
    # caixa postal está atrás dele. O CNAME só é consultado para MX primário
    # que nenhum padrão reconheceu — custa uma consulta, e só nesse caso.
    report.update(summarize_mail_provider(mx_records, report["spf"],
                                          resolve_cname=_cname_target))

    # --- SOA ---
    if answers["soa"]:
        soa = answers["soa"][0]
        report["soa"] = {
            "mname": str(soa.mname).rstrip(".").lower(),
            "rname": str(soa.rname).rstrip(".").lower(),
            "serial": soa.serial,
        }

    return report
