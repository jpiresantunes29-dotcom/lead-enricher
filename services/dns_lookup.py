"""
DNS lookup completo no estilo DNS Dumpster.

Coleta para um domínio:
  - MX (com IP, PTR reverso, ASN, rede, organização e país de cada hostname)
  - A, AAAA (do raiz e do www)
  - NS
  - TXT (com extração estruturada de SPF, DMARC, DKIM, verifications)
  - SOA

Identifica:
  - Provedor de email (Microsoft 365, Google Workspace, Zoho, Proofpoint...)
  - Provedor de hosting (via ASN do A record)
  - Confiança alta (match exato), média (ASN), baixa (fallback domínio)
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

from ._utils import normalize_domain

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
# Match por hostname MX
# ============================================================
PROVIDER_PATTERNS = [
    # Microsoft
    ("mail.protection.outlook.com", "Microsoft 365"),
    ("protection.outlook.com", "Microsoft 365"),
    ("outlook.com", "Microsoft 365"),
    ("eo.outlook.com", "Microsoft 365"),
    ("hotmail.com", "Microsoft (Hotmail)"),
    ("live.com", "Microsoft (Live)"),
    # Google
    ("aspmx.l.google.com", "Google Workspace"),
    ("googlemail.com", "Google Workspace"),
    ("google.com", "Google Workspace"),
    ("googlehosted.com", "Google Workspace"),
    # Zoho
    ("zoho.com", "Zoho Mail"),
    ("zohomail.com", "Zoho Mail"),
    ("zohomail.eu", "Zoho Mail"),
    # Yahoo
    ("yahoodns.net", "Yahoo Business Mail"),
    ("yahoo.com", "Yahoo Mail"),
    # Email security (gateways)
    ("pphosted.com", "Proofpoint"),
    ("proofpoint.com", "Proofpoint"),
    ("ppe-hosted.com", "Proofpoint Essentials"),
    ("mimecast.com", "Mimecast"),
    ("mimecast.co.za", "Mimecast"),
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
    ("antispamcloud.com", "AntiSpamCloud"),
    ("mxlogic.net", "McAfee MXLogic"),
    ("mailfilter.io", "MailFilter"),
    ("spamh.com", "SpamH"),
    ("mailspamprotection.com", "Locaweb (Spam Protection)"),
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
    ("uol.com.br", "UOL Host"),
    ("unifique.com.br", "Unifique"),
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


def _resolve(domain: str, rtype: str, lifetime: int = 5) -> List:
    try:
        return list(dns.resolver.resolve(domain, rtype, lifetime=lifetime))
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
            dns.resolver.NoNameservers, dns.exception.Timeout):
        return []
    except Exception:
        return []


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
_TWO_LEVEL_SUFFIXES = {
    "com.br", "net.br", "org.br", "adv.br", "eng.br", "ind.br", "srv.br",
    "psc.br", "med.br", "esp.br", "tur.br", "agr.br", "art.br", "inf.br",
    "co.uk", "org.uk", "me.uk", "com.mx", "com.ar", "com.co", "com.pe",
    "com.uy", "com.py", "com.au", "net.au", "co.nz", "co.za", "co.jp",
    "co.in", "com.pt", "com.es", "com.tr",
}


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
    host = (hostname or "").lower()
    for pattern, name in PROVIDER_PATTERNS:
        if pattern in host:
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
            "mx": pool.submit(_resolve, domain, "MX", 6),
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
    mx_hosts = [
        {"priority": r.preference, "host": str(r.exchange).rstrip(".").lower()}
        for r in answers["mx"]
    ]
    mx_hosts.sort(key=lambda r: r["priority"])

    # Provedor pelo hostname resolve a maioria dos casos (Google, Microsoft,
    # Zoho, Locaweb...) sem tocar na rede. Só o que sobra vai para o WHOIS.
    provider_name, provider_conf = None, "none"
    for rec in mx_hosts:
        name, conf = identify_provider(rec["host"], None)
        if name and conf == "high":
            provider_name, provider_conf = name, conf
            break

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

    if mx_records and not provider_name:
        for rec in mx_records:
            name, conf = identify_provider(rec["host"], rec["asn_org"])
            if name and conf in ("high", "medium"):
                provider_name, provider_conf = name, conf
                break
        if not provider_name:
            provider_name, provider_conf = identify_provider(
                mx_records[0]["host"], mx_records[0]["asn_org"]
            )
    if provider_name:
        report["mx_provider"] = provider_name
        report["mx_provider_confidence"] = provider_conf

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

    # --- SOA ---
    if answers["soa"]:
        soa = answers["soa"][0]
        report["soa"] = {
            "mname": str(soa.mname).rstrip(".").lower(),
            "rname": str(soa.rname).rstrip(".").lower(),
            "serial": soa.serial,
        }

    return report
