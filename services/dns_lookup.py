"""
DNS lookup completo no estilo DNS Dumpster.

Coleta para um domínio:
  - MX (com IP, ASN, organização e país de cada hostname)
  - A, AAAA (do raiz e do www)
  - NS
  - TXT (com extração estruturada de SPF, DMARC, DKIM, verifications)
  - SOA

Identifica:
  - Provedor de email (Microsoft 365, Google Workspace, Zoho, Proofpoint...)
  - Provedor de hosting (via ASN do A record)
  - Confiança alta (match exato), média (ASN), baixa (fallback domínio)
"""
import re
import dns.resolver
import dns.exception
from functools import lru_cache
from typing import List, Optional

try:
    from ipwhois import IPWhois
    IPWHOIS_OK = True
except Exception:
    IPWHOIS_OK = False

from ._utils import normalize_domain


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


@lru_cache(maxsize=512)
def _ip_info(ip: str) -> dict:
    """Lookup ASN/org via ipwhois (RDAP). Cacheado por IP."""
    if not IPWHOIS_OK or not ip:
        return {"asn": None, "asn_org": None, "country": None}
    try:
        result = IPWhois(ip).lookup_rdap(asn_methods=["whois", "http"])
        return {
            "asn": result.get("asn"),
            "asn_org": (result.get("asn_description") or "").strip(),
            "country": result.get("asn_country_code"),
        }
    except Exception:
        return {"asn": None, "asn_org": None, "country": None}


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
    # Fallback: domínio raiz do hostname
    if host:
        parts = host.rsplit(".", 2)
        if len(parts) >= 2:
            return ".".join(parts[-2:]).title(), "low"
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
      domain, mx, a, aaaa, ns, txt, spf, dmarc, soa,
      mx_provider, mx_provider_confidence, hosting_provider, hosting_confidence
    """
    domain = normalize_domain(domain_input)

    report = {
        "domain": domain,
        "mx": [],
        "a": [],
        "aaaa": [],
        "ns": [],
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
    }

    # --- MX ---
    mx_answers = _resolve(domain, "MX", lifetime=8)
    mx_records = []
    for rdata in mx_answers:
        host = str(rdata.exchange).rstrip(".").lower()
        ip = _resolve_a(host)
        info = _ip_info(ip) if ip else {"asn": None, "asn_org": None, "country": None}
        mx_records.append({
            "priority": rdata.preference,
            "host": host,
            "ip": ip,
            "asn": info["asn"],
            "asn_org": info["asn_org"],
            "country": info["country"],
        })
    mx_records.sort(key=lambda r: r["priority"])
    report["mx"] = mx_records

    # Identifica provedor MX
    if mx_records:
        for rec in mx_records:
            name, conf = identify_provider(rec["host"], rec["asn_org"])
            if name and conf in ("high", "medium"):
                report["mx_provider"] = name
                report["mx_provider_confidence"] = conf
                break
        if not report["mx_provider"]:
            name, conf = identify_provider(mx_records[0]["host"], mx_records[0]["asn_org"])
            report["mx_provider"] = name
            report["mx_provider_confidence"] = conf

    # --- A (raiz e www) ---
    a_records = set()
    for host_to_check in (domain, f"www.{domain}"):
        for rdata in _resolve(host_to_check, "A"):
            a_records.add(str(rdata))
    report["a"] = sorted(a_records)

    # Hosting provider via ASN do primeiro A
    if report["a"]:
        info = _ip_info(report["a"][0])
        if info["asn_org"]:
            name, conf = identify_provider("", info["asn_org"])
            if name:
                report["hosting_provider"] = name
                report["hosting_confidence"] = conf
            else:
                report["hosting_provider"] = info["asn_org"]
                report["hosting_confidence"] = "medium"

    # --- AAAA ---
    aaaa_records = set()
    for host_to_check in (domain, f"www.{domain}"):
        for rdata in _resolve(host_to_check, "AAAA"):
            aaaa_records.add(str(rdata))
    report["aaaa"] = sorted(aaaa_records)

    # --- NS ---
    report["ns"] = sorted(str(r).rstrip(".").lower() for r in _resolve(domain, "NS"))

    # --- TXT ---
    txt_list = []
    for rdata in _resolve(domain, "TXT"):
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
        for rdata in _resolve(f"_dmarc.{domain}", "TXT"):
            txt = _txt_unquote(rdata)
            if txt.lower().startswith("v=dmarc1"):
                report["dmarc"] = txt
                break

    # --- SOA ---
    soa_answers = _resolve(domain, "SOA")
    if soa_answers:
        soa = soa_answers[0]
        report["soa"] = {
            "mname": str(soa.mname).rstrip(".").lower(),
            "rname": str(soa.rname).rstrip(".").lower(),
            "serial": soa.serial,
        }

    return report


# ============================================================
# Compatibilidade com a API antiga
# ============================================================
def get_mx_provider(domain: str) -> dict:
    """Wrapper de compatibilidade — devolve {provider, records, confidence}."""
    report = get_dns_report(domain)
    return {
        "domain": report["domain"],
        "provider": report["mx_provider"],
        "records": [{"priority": r["priority"], "host": r["host"]} for r in report["mx"]],
        "confidence": report["mx_provider_confidence"],
    }
