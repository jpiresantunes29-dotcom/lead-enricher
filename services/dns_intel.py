"""
Relatório DNS completo — o que o DNS Dumpster mostra, sob demanda.

`services.dns_lookup.get_dns_report()` roda dentro do orçamento do
enriquecimento e devolve só o que a ficha comercial precisa (provedor de
e-mail, MX, NS, A, SPF/DMARC). Este módulo é a camada de baixo: varre a zona
inteira e devolve cada registro com nome, código e valor cru. Só é chamado
quando o usuário abre o painel "Relatório DNS completo" — nunca durante a
busca, para não somar 20 s ao enriquecimento.

Coleta:
  registros   A, AAAA, MX, NS, TXT, SOA, CAA, SRV, CNAME, DS/DNSKEY (DNSSEC)
  e-mail      SPF (mecanismos + contagem de lookups), DMARC (todas as tags),
              DKIM (varredura de seletores conhecidos, com tamanho da chave),
              MTA-STS (TXT + política HTTPS), TLS-RPT, BIMI, autodiscover
  hosts       subdomínios por Certificate Transparency (crt.sh) e por lista de
              nomes comuns; cada host com IP, PTR, ASN, rede, país e banner
              HTTP (status, servidor, título)
  domínio     RDAP: registrar, datas de registro/expiração, status, DNSSEC
  stack       ferramentas identificadas nos TXT (Zoom, DocuSign, Atlassian...),
              que é o que transforma um dump de DNS em sinal comercial
"""
import base64
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import dns.resolver
import requests

from ._utils import HEADERS, is_public_host, normalize_domain
# Reaproveita a inteligência de rede do lookup do enriquecimento — mesma tabela
# de provedores, mesmo WHOIS/PTR com cache por IP.
from .dns_lookup import (
    _EMPTY_IP_INFO, _ip_info, _ptr, _txt_unquote, clean_asn_org,
    country_label, identify_provider,
)

logger = logging.getLogger(__name__)

# Incremente quando a coleta mudar: relatórios gravados por versões anteriores
# deixam de ser servidos do cache e são recoletados na próxima abertura.
FULL_REPORT_VERSION = 1

# Teto de tempo do relatório inteiro. A função serverless da Vercel morre em
# 60 s sem devolver nada — melhor entregar o que já foi coletado, com aviso do
# que ficou de fora, do que perder tudo.
INTEL_BUDGET = int(os.getenv("DNS_INTEL_BUDGET", "24"))
HTTP_TIMEOUT = int(os.getenv("DNS_INTEL_HTTP_TIMEOUT", "5"))
# Tetos de fan-out. Domínio grande tem centenas de subdomínios em log de
# certificado; a tabela vira ruído e o WHOIS de cada IP é a parte cara.
HOST_LIMIT = int(os.getenv("DNS_INTEL_HOST_LIMIT", "40"))
CT_CANDIDATE_LIMIT = int(os.getenv("DNS_INTEL_CT_LIMIT", "80"))
# Identidade de rede sai do Team Cymru por DNS (barato), então cobre a tabela
# inteira — o teto só existe para o caso de cair no RDAP de reserva.
ASN_LIMIT = int(os.getenv("DNS_INTEL_ASN_LIMIT", "40"))
BANNER_LIMIT = int(os.getenv("DNS_INTEL_BANNER_LIMIT", "8"))

# Seletores DKIM publicados por provedores conhecidos. Não há como listar
# seletores pelo DNS (não existe wildcard nem enumeração), então a única forma
# de mostrar a chave é tentar os nomes que os provedores usam.
DKIM_SELECTORS = [
    "google", "default", "selector1", "selector2", "s1", "s2", "k1", "k2",
    "dkim", "mail", "smtp", "zoho", "zmail", "mandrill", "everlytickey1",
    "sig1", "protonmail", "mailjet", "sendgrid", "titan1", "ctct1", "pic",
    "hs1", "hs2", "mimecast", "krs", "sm", "email",
]

# Subdomínios que praticamente todo domínio corporativo publica. Complementa o
# Certificate Transparency: host interno sem certificado público (ex.: vpn,
# erp, homolog) só aparece por esta lista.
COMMON_SUBDOMAINS = [
    "www", "mail", "webmail", "smtp", "imap", "pop", "mx", "email", "correio",
    "autodiscover", "autoconfig", "owa", "exchange", "vpn", "remote", "portal",
    "intranet", "extranet", "api", "app", "admin", "painel", "sistema", "erp",
    "crm", "loja", "shop", "blog", "ead", "cursos", "suporte", "help", "ftp",
    "cpanel", "webdisk", "ns1", "ns2", "dev", "homolog", "staging", "teste",
    "cdn", "static", "assets", "docs", "status", "git", "jira",
]

# SRV que revelam serviço de e-mail/colaboração em uso.
SRV_NAMES = [
    ("_autodiscover._tcp", "Autodiscover (Exchange/Microsoft 365)"),
    ("_submission._tcp", "Envio SMTP (porta 587)"),
    ("_imaps._tcp", "IMAP sobre TLS"),
    ("_pop3s._tcp", "POP3 sobre TLS"),
    ("_sipfederationtls._tcp", "Federação SIP (Teams/Skype)"),
    ("_caldavs._tcp", "CalDAV (agenda)"),
    ("_xmpp-server._tcp", "XMPP (mensageria)"),
]

# Tipos consultados no domínio raiz, na ordem em que aparecem no painel.
ROOT_TYPES = ["A", "AAAA", "MX", "NS", "TXT", "SOA", "CAA", "DS", "DNSKEY"]

# Assinaturas de TXT → (chave, rótulo). É o bloco que transforma dump de DNS em
# sinal comercial: quem verificou o domínio no Zoom, no DocuSign ou no
# Atlassian está dizendo qual stack usa.
TXT_SIGNATURES = [
    ("v=spf1", "spf", "SPF (autorização de envio)"),
    ("v=dmarc1", "dmarc", "DMARC (política de rejeição)"),
    ("v=dkim1", "dkim", "DKIM (assinatura)"),
    ("v=stsv1", "mta_sts", "MTA-STS (TLS obrigatório)"),
    ("v=tlsrptv1", "tls_rpt", "TLS-RPT (relatório de TLS)"),
    ("v=bimi1", "bimi", "BIMI (logo no e-mail)"),
    ("google-site-verification", "verify_google", "Verificação — Google"),
    ("ms=", "verify_microsoft", "Verificação — Microsoft"),
    ("ms-domain-verification", "verify_microsoft", "Verificação — Microsoft"),
    ("msv1", "verify_microsoft", "Verificação — Microsoft"),
    ("facebook-domain-verification", "verify_meta", "Verificação — Meta"),
    ("atlassian-domain-verification", "atlassian", "Atlassian (Jira/Confluence)"),
    ("atlassian-sending-domain-verification", "atlassian", "Atlassian"),
    ("apple-domain-verification", "apple", "Apple Business"),
    ("adobe-idp-site-verification", "adobe", "Adobe"),
    ("adobe-sign-verification", "adobe", "Adobe Sign"),
    ("docusign=", "docusign", "DocuSign"),
    ("zoom-domain-verification", "zoom", "Zoom"),
    ("zoom_verify", "zoom", "Zoom"),
    ("slack-domain-verification", "slack", "Slack"),
    ("notion-domain-verification", "notion", "Notion"),
    ("miro-verification", "miro", "Miro"),
    ("asana-domain-verification", "asana", "Asana"),
    ("dropbox-domain-verification", "dropbox", "Dropbox"),
    ("stripe-verification", "stripe", "Stripe"),
    ("shopify", "shopify", "Shopify"),
    ("hubspot", "hubspot", "HubSpot"),
    ("pardot", "pardot", "Salesforce Pardot"),
    ("salesforce", "salesforce", "Salesforce"),
    ("amazonses:", "ses", "Amazon SES"),
    ("mailgun", "mailgun", "Mailgun"),
    ("sendgrid", "sendgrid", "SendGrid"),
    ("mandrill", "mandrill", "Mailchimp/Mandrill"),
    ("brevo-code", "brevo", "Brevo (Sendinblue)"),
    ("sendinblue-code", "brevo", "Brevo (Sendinblue)"),
    ("postman-domain-verification", "postman", "Postman"),
    ("onetrust-domain-verification", "onetrust", "OneTrust (LGPD)"),
    ("logmein-verification", "logmein", "LogMeIn/GoTo"),
    ("citrix-verification", "citrix", "Citrix"),
    ("cisco-ci-domain-verification", "cisco", "Cisco Webex"),
    ("workplace-domain-verification", "workplace", "Workplace (Meta)"),
    ("canva-site-verification", "canva", "Canva"),
    ("globalsign-domain-verification", "globalsign", "GlobalSign (certificado)"),
    ("_globalsign-domain-verification", "globalsign", "GlobalSign (certificado)"),
    ("have-i-been-pwned-verification", "hibp", "Have I Been Pwned"),
    ("status-page-domain-verification", "statuspage", "Statuspage"),
    ("segment-site-verification", "segment", "Segment"),
    ("rvcs", "rvcs", "RD Station"),
    ("teamviewer-sso-verification", "teamviewer", "TeamViewer"),
    ("wrike-verification", "wrike", "Wrike"),
    ("smartsheet-site-validation", "smartsheet", "Smartsheet"),
    ("loom-site-verification", "loom", "Loom"),
    ("openai-domain-verification", "openai", "OpenAI"),
    ("anthropic-domain-verification", "anthropic", "Anthropic"),
]

_QUALIFIER_LABEL = {
    "+": "permite", "-": "rejeita", "~": "marca como suspeito", "?": "neutro",
}
# Mecanismos de SPF que gastam consulta DNS. O limite do protocolo é 10; passar
# disso faz o SPF ser avaliado como permerror — ou seja, o domínio acha que
# está protegido e não está.
_SPF_LOOKUP_MECHS = {"include", "a", "mx", "ptr", "exists", "redirect"}
_SPF_ALL_POLICY = {
    "-": ("fail", "Rejeita quem não está na lista"),
    "~": ("softfail", "Aceita marcando como suspeito"),
    "?": ("neutral", "Neutro — não diz nada"),
    "+": ("pass", "Aceita qualquer remetente (inseguro)"),
}
_DMARC_POLICY_LABEL = {
    "none": "Só monitora (não bloqueia)",
    "quarantine": "Manda para o spam",
    "reject": "Rejeita a mensagem",
}
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


# ============================================================
# Consultas DNS
# ============================================================

def _query(name: str, rtype: str, lifetime: float = 4.0) -> Tuple[List[Any], Optional[int]]:
    """Consulta um tipo e devolve (rdatas, ttl). TTL é dado do painel."""
    try:
        answer = dns.resolver.resolve(name, rtype, lifetime=lifetime)
        ttl = int(answer.rrset.ttl) if answer.rrset is not None else None
        return list(answer), ttl
    except Exception:
        return [], None


def _txt_values(name: str, lifetime: float = 4.0) -> List[str]:
    rdatas, _ = _query(name, "TXT", lifetime)
    return [_txt_unquote(r) for r in rdatas]


def _first_a(host: str, lifetime: float = 3.0) -> Optional[str]:
    rdatas, _ = _query(host, "A", lifetime)
    return str(rdatas[0]) if rdatas else None


# ============================================================
# Identidade de rede do IP (ASN, rede, país, registro regional)
# ============================================================

@lru_cache(maxsize=512)
def _cymru_asn_name(asn: str) -> Optional[str]:
    """'15169' → 'GOOGLE - Google LLC, US' (dono do bloco, pelo Team Cymru)."""
    values = _txt_values(f"AS{asn}.asn.cymru.com", 5)
    if not values:
        return None
    parts = [p.strip() for p in values[0].split("|")]
    return parts[-1] or None if len(parts) >= 5 else None


@lru_cache(maxsize=1024)
def ip_identity(ip: str) -> dict:
    """
    ASN, rede, país, registro regional e data de alocação de um IP.

    Fonte primária é o Team Cymru por DNS: uma consulta TXT devolve tudo de
    uma vez, sem o rate limit dos servidores WHOIS — que era o motivo de a
    coluna ASN vir vazia na maioria das linhas quando 18 IPs eram consultados
    ao mesmo tempo. O RDAP (ipwhois) fica de reserva, e cobre IPv6.
    """
    info = dict(_EMPTY_IP_INFO)
    info.update({"asn_registry": None, "asn_allocated": None, "source": None})
    if not ip:
        return info

    if ":" not in ip:
        reverse = ".".join(reversed(ip.split(".")))
        values = _txt_values(f"{reverse}.origin.asn.cymru.com", 5)
        if values:
            parts = [p.strip() for p in values[0].split("|")]
            if len(parts) >= 5 and parts[0]:
                asn = parts[0].split()[0]
                country = parts[2] or None
                info.update({
                    "asn": asn,
                    "asn_cidr": parts[1] or None,
                    "country": country,
                    "country_name": country_label(country),
                    "asn_registry": parts[3] or None,
                    "asn_allocated": parts[4] or None,
                    "asn_org": _cymru_asn_name(asn),
                    "source": "cymru",
                })
                return info

    fallback = _ip_info(ip)
    if fallback.get("asn"):
        info.update(fallback)
        info["source"] = "rdap"
    return info


# ============================================================
# Parsers de e-mail
# ============================================================

def parse_spf(raw: Optional[str]) -> Optional[dict]:
    """
    Quebra o SPF em mecanismos com nome, valor e efeito, e conta quantas
    consultas DNS ele custa (limite de 10 no protocolo).
    """
    if not raw:
        return None
    tokens = raw.split()
    mechanisms, lookups, all_token = [], 0, None
    for token in tokens:
        low = token.lower()
        if low.startswith("v="):
            continue
        qualifier = "+"
        body = token
        if body[:1] in "+-~?":
            qualifier, body = body[0], body[1:]
        if "=" in body and (":" not in body or body.index("=") < body.index(":")):
            kind, _, value = body.partition("=")          # redirect=, exp=
        else:
            kind, _, value = body.partition(":")          # include:, ip4:, a:
        kind = kind.lower()
        if kind == "all":
            all_token = f"{qualifier}all"
            continue
        if kind in _SPF_LOOKUP_MECHS:
            lookups += 1
        mechanisms.append({
            "raw": token,
            "type": kind,
            "value": value or None,
            "qualifier": qualifier,
            "qualifier_label": _QUALIFIER_LABEL.get(qualifier, qualifier),
            "costs_lookup": kind in _SPF_LOOKUP_MECHS,
        })
    policy, policy_label = _SPF_ALL_POLICY.get(
        (all_token or "")[:1], (None, "Sem mecanismo 'all' — política indefinida")
    )
    return {
        "raw": raw,
        "mechanisms": mechanisms,
        "all": all_token,
        "policy": policy,
        "policy_label": policy_label,
        "lookups": lookups,
        "lookup_limit": 10,
        "over_limit": lookups > 10,
    }


def parse_dmarc(raw: Optional[str]) -> Optional[dict]:
    """Todas as tags do DMARC, cada uma com o código e o que ela faz."""
    if not raw:
        return None
    tags: Dict[str, str] = {}
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        key = key.strip().lower()
        if key:
            tags[key] = value.strip()
    policy = (tags.get("p") or "").lower() or None
    subdomain_policy = (tags.get("sp") or "").lower() or None
    return {
        "raw": raw,
        "tags": tags,
        "policy": policy,
        "policy_label": _DMARC_POLICY_LABEL.get(policy or "", "—"),
        "subdomain_policy": subdomain_policy,
        "subdomain_policy_label": _DMARC_POLICY_LABEL.get(subdomain_policy or "", None),
        "percent": tags.get("pct"),
        "rua": tags.get("rua"),
        "ruf": tags.get("ruf"),
        "alignment_dkim": tags.get("adkim"),
        "alignment_spf": tags.get("aspf"),
        "enforced": policy in ("quarantine", "reject"),
    }


def _dkim_key_bits(public_key: str) -> Optional[int]:
    """
    Tamanho da chave a partir do DER da SubjectPublicKeyInfo: um RSA-2048 dá
    294 bytes, um RSA-1024 dá 162. Chave de 1024 bits é considerada fraca hoje
    — é a informação que justifica mostrar o campo.
    """
    if not public_key:
        return None
    try:
        der = base64.b64decode(public_key + "=" * (-len(public_key) % 4))
    except Exception:
        return None
    if len(der) < 60:
        return None
    bits = round((len(der) - 38) * 8 / 256) * 256
    return bits or None


def parse_dkim(selector: str, raw: str) -> dict:
    tags: Dict[str, str] = {}
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        key = key.strip().lower()
        if key:
            tags[key] = value.strip()
    public_key = tags.get("p", "")
    return {
        "selector": selector,
        "host": None,          # preenchido pelo chamador
        "raw": raw,
        "key_type": tags.get("k") or ("rsa" if public_key else None),
        "hash": tags.get("h"),
        "flags": tags.get("t"),
        "revoked": public_key == "",
        "key_bits": _dkim_key_bits(public_key),
        "weak_key": (_dkim_key_bits(public_key) or 2048) < 2048,
    }


# ============================================================
# Fontes HTTP (política MTA-STS, RDAP do domínio, crt.sh, banners)
# ============================================================

def _get(url: str, timeout: Optional[int] = None,
         headers: Optional[dict] = None, **kwargs) -> Optional[requests.Response]:
    try:
        return requests.get(
            url, timeout=(3, timeout or HTTP_TIMEOUT),
            headers=headers or HEADERS, **kwargs
        )
    except Exception as e:
        logger.debug("GET falhou url=%s: %s", url, e)
        return None


def fetch_mta_sts_policy(domain: str) -> Optional[dict]:
    """A política fica em https://mta-sts.<domínio>/.well-known/mta-sts.txt."""
    host = f"mta-sts.{domain}"
    if not is_public_host(host):
        return None
    resp = _get(f"https://{host}/.well-known/mta-sts.txt")
    if not resp or resp.status_code != 200:
        return None
    text = resp.text[:4000]
    policy: Dict[str, Any] = {"raw": text, "version": None, "mode": None,
                              "mx": [], "max_age": None}
    for line in text.splitlines()[:60]:
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "mx":
            policy["mx"].append(value)
        elif key == "version":
            policy["version"] = value
        elif key == "mode":
            policy["mode"] = value
        elif key == "max_age":
            policy["max_age"] = value
    return policy


def _vcard_fields(entity: dict) -> Dict[str, str]:
    """Extrai fn/org/email do vcardArray de uma entidade RDAP."""
    fields: Dict[str, str] = {}
    vcard = entity.get("vcardArray")
    if not (isinstance(vcard, list) and len(vcard) > 1 and isinstance(vcard[1], list)):
        return fields
    for item in vcard[1]:
        if isinstance(item, list) and len(item) >= 4 and isinstance(item[0], str):
            value = item[-1]
            if isinstance(value, str) and value.strip():
                fields.setdefault(item[0].lower(), value.strip())
    return fields


def _format_cnpj(handle: Optional[str]) -> Optional[str]:
    """O handle do titular no registro.br é o CNPJ — vale mostrar formatado."""
    digits = re.sub(r"\D", "", handle or "")
    if len(digits) != 14:
        return None
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def fetch_rdap(domain: str) -> Optional[dict]:
    """
    RDAP do domínio: titular, registrar, datas de registro/expiração, status,
    contatos e DNSSEC.

    Domínio .br vai direto ao rdap.registro.br — além de responder rápido, é
    lá que aparecem razão social e CNPJ do titular, que o bootstrap genérico
    do rdap.org nem sempre entrega dentro do tempo.
    """
    endpoints = (
        [f"https://rdap.registro.br/domain/{domain}"]
        if domain.endswith(".br")
        else [f"https://rdap.org/domain/{domain}"]
    )
    data = None
    for url in endpoints:
        resp = _get(url, timeout=8, headers={**HEADERS, "Accept": "application/rdap+json"})
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
                break
            except Exception:
                continue
    if not data:
        return None

    events = {}
    for event in data.get("events") or []:
        action = (event.get("eventAction") or "").lower()
        if action and event.get("eventDate"):
            events[action] = event["eventDate"]

    contacts = []
    registrant = registrar = None
    for entity in data.get("entities") or []:
        roles = [str(r).lower() for r in (entity.get("roles") or [])]
        fields = _vcard_fields(entity)
        name = fields.get("fn") or fields.get("org") or entity.get("handle")
        record = {
            "roles": roles,
            "name": name,
            "handle": entity.get("handle"),
            "cnpj": _format_cnpj(entity.get("handle")),
            "email": fields.get("email"),
        }
        if name or record["email"]:
            contacts.append(record)
        if "registrant" in roles and not registrant:
            registrant = record
        if "registrar" in roles and not registrar:
            registrar = record

    return {
        "handle": data.get("handle"),
        "registrar": (registrar or {}).get("name"),
        "owner": (registrant or {}).get("name"),
        "owner_cnpj": (registrant or {}).get("cnpj"),
        "status": data.get("status") or [],
        "registered_on": events.get("registration"),
        "expires_on": events.get("expiration"),
        "changed_on": events.get("last changed") or events.get("last update of rdap database"),
        "nameservers": [ns.get("ldhName", "").lower()
                        for ns in (data.get("nameservers") or []) if ns.get("ldhName")],
        "dnssec_signed": (data.get("secureDNS") or {}).get("delegationSigned"),
        "contacts": contacts,
        "source": "registro.br" if domain.endswith(".br") else "rdap.org",
    }


def _ct_names(raw_names, domain: str, into: set) -> None:
    for name in raw_names:
        name = str(name).strip().lower().lstrip("*.").rstrip(".")
        if name and name.endswith(f".{domain}") and " " not in name:
            into.add(name)


def fetch_ct_subdomains(domain: str) -> List[str]:
    """
    Subdomínios pelos logs de Certificate Transparency: todo certificado
    emitido para o domínio é público, e é assim que o DNS Dumpster monta a
    lista de hosts.

    Cert Spotter primeiro porque responde; o crt.sh cai com 502 boa parte do
    tempo e não pode ser a única fonte.
    """
    names: set = set()
    resp = _get(
        f"https://api.certspotter.com/v1/issuances?domain={domain}"
        "&include_subdomains=true&expand=dns_names",
        timeout=8,
    )
    if resp is not None and resp.status_code == 200:
        try:
            for row in resp.json()[:2000]:
                _ct_names(row.get("dns_names") or [], domain, names)
        except Exception:
            pass
    if not names:
        resp = _get(f"https://crt.sh/?q=%25.{domain}&output=json", timeout=8)
        if resp is not None and resp.status_code == 200:
            try:
                for row in resp.json()[:4000]:
                    _ct_names(str(row.get("name_value") or "").split("\n"), domain, names)
            except Exception:
                pass
    return sorted(names)


def fetch_http_banner(host: str) -> Optional[dict]:
    """
    Banner HTTP do host: status, servidor, tecnologia declarada e título da
    página — as colunas 'HTTP' e 'HTTP TECH' do DNS Dumpster.
    """
    if not is_public_host(host):
        return None
    for scheme in ("https", "http"):
        try:
            with requests.get(f"{scheme}://{host}", timeout=(3, HTTP_TIMEOUT),
                              headers=HEADERS, allow_redirects=True,
                              stream=True) as resp:
                chunk = b""
                for part in resp.iter_content(8192):
                    chunk = part or b""
                    break
                title = None
                match = _TITLE_RE.search(chunk.decode("utf-8", "ignore"))
                if match:
                    title = re.sub(r"\s+", " ", match.group(1)).strip()[:120]
                final = resp.url.split("://", 1)[-1].rstrip("/")
                return {
                    "scheme": scheme,
                    "status": resp.status_code,
                    "server": resp.headers.get("Server"),
                    "powered_by": resp.headers.get("X-Powered-By"),
                    "title": title or None,
                    "redirect_to": final if final.split("/")[0] != host else None,
                }
        except Exception:
            continue
    return None


# ============================================================
# Classificação de TXT
# ============================================================

def classify_txt(txt: str) -> Tuple[Optional[str], Optional[str]]:
    """Devolve (chave, rótulo) do primeiro padrão conhecido que casar."""
    low = (txt or "").strip().lower()
    for pattern, kind, label in TXT_SIGNATURES:
        if low.startswith(pattern) or pattern in low:
            return kind, label
    return None, None


# ============================================================
# Relatório
# ============================================================

def _host_entry(hostname: str, ip: Optional[str], role: str) -> dict:
    return {"host": hostname, "role": role, "ip": ip, "ptr": None, "asn": None,
            "asn_org": None, "asn_cidr": None, "asn_registry": None,
            "asn_allocated": None, "country": None, "country_name": None,
            "provider": None, "http": None}


def get_full_report(domain_input: str) -> dict:
    """
    Relatório completo de um domínio. Nunca levanta exceção: o que não coube
    no orçamento de tempo volta em `warnings`, e o painel mostra o resto.
    """
    started = time.monotonic()
    domain = normalize_domain(domain_input)
    deadline = started + INTEL_BUDGET
    warnings: List[str] = []

    def remaining(reserve: float = 0.0) -> float:
        return max(0.5, deadline - time.monotonic() - reserve)

    report: Dict[str, Any] = {
        "version": FULL_REPORT_VERSION,
        "domain": domain,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "records": {"mx": [], "ns": [], "a": [], "aaaa": [], "cname": [],
                    "txt": [], "soa": None, "caa": [], "srv": [], "ttl": {}},
        "email": {"spf": None, "dmarc": None, "dkim": [], "mta_sts": None,
                  "tls_rpt": None, "bimi": None},
        "hosts": [],
        "registration": None,
        "dnssec": {"signed": False, "ds": [], "dnskey_count": 0},
        "stack": [],
        "summary": {},
        "warnings": warnings,
    }

    # ── Fase 1: tudo que é consulta independente, de uma vez ─────────────────
    results: Dict[Any, Any] = {}
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures: Dict[Any, Any] = {}
        for rtype in ROOT_TYPES:
            futures[("root", rtype)] = pool.submit(_query, domain, rtype, 5)
        futures[("www", "CNAME")] = pool.submit(_query, f"www.{domain}", "CNAME", 3)
        futures[("txt", "_dmarc")] = pool.submit(_txt_values, f"_dmarc.{domain}")
        futures[("txt", "_mta-sts")] = pool.submit(_txt_values, f"_mta-sts.{domain}")
        futures[("txt", "_smtp._tls")] = pool.submit(_txt_values, f"_smtp._tls.{domain}")
        futures[("txt", "default._bimi")] = pool.submit(_txt_values, f"default._bimi.{domain}")
        for selector in DKIM_SELECTORS:
            futures[("dkim", selector)] = pool.submit(
                _txt_values, f"{selector}._domainkey.{domain}", 3
            )
        for name, label in SRV_NAMES:
            futures[("srv", name)] = pool.submit(_query, f"{name}.{domain}", "SRV", 3)
        for sub in COMMON_SUBDOMAINS:
            futures[("sub", sub)] = pool.submit(_first_a, f"{sub}.{domain}")
        futures[("ct",)] = pool.submit(fetch_ct_subdomains, domain)
        futures[("rdap",)] = pool.submit(fetch_rdap, domain)
        futures[("mtasts_policy",)] = pool.submit(fetch_mta_sts_policy, domain)

        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=remaining(reserve=8))
            except Exception:
                results[key] = None
                if key[0] in ("ct", "rdap", "mtasts_policy"):
                    warnings.append(f"Fonte '{key[0]}' não respondeu a tempo.")

    def rr(key) -> Tuple[List[Any], Optional[int]]:
        value = results.get(key)
        return value if isinstance(value, tuple) else ([], None)

    # ── Registros do domínio raiz ────────────────────────────────────────────
    ttls = report["records"]["ttl"]
    for rtype in ROOT_TYPES:
        _, ttl = rr(("root", rtype))
        if ttl is not None:
            ttls[rtype] = ttl

    a_records = [str(r) for r in rr(("root", "A"))[0]]
    aaaa_records = [str(r) for r in rr(("root", "AAAA"))[0]]
    report["records"]["a"] = sorted(set(a_records))
    report["records"]["aaaa"] = sorted(set(aaaa_records))

    mx_raw = sorted(
        [{"priority": r.preference, "host": str(r.exchange).rstrip(".").lower()}
         for r in rr(("root", "MX"))[0]],
        key=lambda x: (x["priority"], x["host"]),
    )
    ns_raw = sorted({str(r).rstrip(".").lower() for r in rr(("root", "NS"))[0]})

    cname_www, _ = rr(("www", "CNAME"))
    if cname_www:
        report["records"]["cname"].append({
            "host": f"www.{domain}",
            "target": str(cname_www[0]).rstrip(".").lower(),
        })

    soa_rdatas, soa_ttl = rr(("root", "SOA"))
    if soa_rdatas:
        soa = soa_rdatas[0]
        report["records"]["soa"] = {
            "mname": str(soa.mname).rstrip(".").lower(),
            "rname": str(soa.rname).rstrip(".").lower(),
            "serial": soa.serial, "refresh": soa.refresh, "retry": soa.retry,
            "expire": soa.expire, "minimum": soa.minimum, "ttl": soa_ttl,
        }

    for rdata in rr(("root", "CAA"))[0]:
        try:
            value = rdata.value.decode() if isinstance(rdata.value, bytes) else str(rdata.value)
            tag = rdata.tag.decode() if isinstance(rdata.tag, bytes) else str(rdata.tag)
            report["records"]["caa"].append(
                {"flags": rdata.flags, "tag": tag, "value": value}
            )
        except Exception:
            continue

    for name, label in SRV_NAMES:
        rdatas, ttl = rr(("srv", name))
        for rdata in rdatas:
            report["records"]["srv"].append({
                "name": f"{name}.{domain}", "service": label,
                "priority": rdata.priority, "weight": rdata.weight,
                "port": rdata.port,
                "target": str(rdata.target).rstrip(".").lower(),
                "ttl": ttl,
            })

    # DNSSEC: DS na zona pai + DNSKEY na própria zona.
    ds_records = rr(("root", "DS"))[0]
    dnskeys = rr(("root", "DNSKEY"))[0]
    report["dnssec"] = {
        "signed": bool(ds_records),
        "ds": [{"key_tag": d.key_tag, "algorithm": d.algorithm,
                "digest_type": d.digest_type} for d in ds_records],
        "dnskey_count": len(dnskeys),
    }

    # ── TXT: valor cru + classificação + stack ──────────────────────────────
    txt_entries = []
    stack: Dict[str, str] = {}
    spf_raw = None
    # Protocolos de e-mail têm bloco próprio; o resto do que aparece no TXT é
    # ferramenta contratada pela empresa e vai para o bloco "Stack".
    email_kinds = {"spf", "dmarc", "dkim", "mta_sts", "tls_rpt", "bimi"}
    for rdata in rr(("root", "TXT"))[0]:
        value = _txt_unquote(rdata)
        kind, label = classify_txt(value)
        txt_entries.append({"value": value, "kind": kind, "label": label,
                            "length": len(value)})
        if kind == "spf" and not spf_raw:
            spf_raw = value
        if kind and kind not in email_kinds:
            stack[kind] = label
    report["records"]["txt"] = txt_entries
    report["stack"] = [{"key": k, "label": v} for k, v in sorted(stack.items(), key=lambda kv: kv[1])]

    # ── Autenticação de e-mail ──────────────────────────────────────────────
    report["email"]["spf"] = parse_spf(spf_raw)

    dmarc_raw = next(
        (t for t in (results.get(("txt", "_dmarc")) or []) if t.lower().startswith("v=dmarc1")),
        None,
    )
    report["email"]["dmarc"] = parse_dmarc(dmarc_raw)

    dkim_list = []
    for selector in DKIM_SELECTORS:
        for value in results.get(("dkim", selector)) or []:
            if "p=" not in value.lower():
                continue
            entry = parse_dkim(selector, value)
            entry["host"] = f"{selector}._domainkey.{domain}"
            dkim_list.append(entry)
            break
    report["email"]["dkim"] = dkim_list

    mta_sts_txt = next(
        (t for t in (results.get(("txt", "_mta-sts")) or []) if t.lower().startswith("v=stsv1")),
        None,
    )
    policy = results.get(("mtasts_policy",))
    if mta_sts_txt or policy:
        report["email"]["mta_sts"] = {
            "txt": mta_sts_txt, "host": f"_mta-sts.{domain}", "policy": policy,
            "mode": (policy or {}).get("mode"),
        }
    tls_rpt = next(
        (t for t in (results.get(("txt", "_smtp._tls")) or []) if t.lower().startswith("v=tlsrptv1")),
        None,
    )
    if tls_rpt:
        report["email"]["tls_rpt"] = {"raw": tls_rpt, "host": f"_smtp._tls.{domain}"}
    bimi = next(
        (t for t in (results.get(("txt", "default._bimi")) or []) if t.lower().startswith("v=bimi1")),
        None,
    )
    if bimi:
        report["email"]["bimi"] = {"raw": bimi, "host": f"default._bimi.{domain}"}

    report["registration"] = results.get(("rdap",))

    # ── Fase 2: hosts (subdomínios do CT precisam resolver antes) ───────────
    hosts: Dict[str, dict] = {}

    def add_host(hostname: str, ip: Optional[str], role: str):
        hostname = (hostname or "").rstrip(".").lower()
        if not hostname:
            return
        existing = hosts.get(hostname)
        if existing:
            if ip and not existing["ip"]:
                existing["ip"] = ip
            return
        hosts[hostname] = _host_entry(hostname, ip, role)

    add_host(domain, report["records"]["a"][0] if report["records"]["a"] else None, "site")
    for rec in mx_raw:
        add_host(rec["host"], None, "mx")
    for host in ns_raw:
        add_host(host, None, "ns")
    for sub in COMMON_SUBDOMAINS:
        ip = results.get(("sub", sub))
        if ip:
            add_host(f"{sub}.{domain}", ip, "host")

    ct_names = [n for n in (results.get(("ct",)) or []) if n not in hosts]
    # Testar subdomínio de certificado é a etapa mais cara da coleta (nome
    # desativado só responde no timeout), então só entra se sobrou tempo.
    if ct_names and remaining() > 8:
        candidates = ct_names[:CT_CANDIDATE_LIMIT]
        with ThreadPoolExecutor(max_workers=32) as pool:
            futures = {name: pool.submit(_first_a, name, 2.0) for name in candidates}
            for name, future in futures.items():
                try:
                    ip = future.result(timeout=remaining(reserve=6))
                except Exception:
                    ip = None
                if ip:
                    add_host(name, ip, "host")
        if len(ct_names) > CT_CANDIDATE_LIMIT:
            warnings.append(
                f"{len(ct_names)} subdomínios em logs de certificado; "
                f"os {CT_CANDIDATE_LIMIT} primeiros foram testados."
            )
    elif ct_names:
        warnings.append(
            f"{len(ct_names)} subdomínios em logs de certificado não foram "
            "testados: o tempo da coleta acabou."
        )

    # Hosts sem IP (MX/NS entram sem resolver) + corte no teto da tabela.
    pending = [h for h, entry in hosts.items() if not entry["ip"]]
    if pending:
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = {h: pool.submit(_first_a, h) for h in pending}
            for host, future in futures.items():
                try:
                    hosts[host]["ip"] = future.result(timeout=remaining(reserve=5))
                except Exception:
                    pass

    role_order = {"site": 0, "mx": 1, "ns": 2, "host": 3}
    ordered = sorted(hosts.values(), key=lambda h: (role_order.get(h["role"], 9), h["host"]))
    if len(ordered) > HOST_LIMIT:
        warnings.append(
            f"{len(ordered)} hosts encontrados; a tabela mostra os {HOST_LIMIT} primeiros."
        )
        ordered = ordered[:HOST_LIMIT]

    # ── Fase 3: identidade de rede dos IPs + banner HTTP ────────────────────
    unique_ips: List[str] = []
    for entry in ordered:
        if entry["ip"] and entry["ip"] not in unique_ips:
            unique_ips.append(entry["ip"])
    whois_ips = unique_ips[:ASN_LIMIT]
    if len(unique_ips) > ASN_LIMIT:
        warnings.append(
            f"Identificação de rede (ASN) feita nos {ASN_LIMIT} primeiros IPs."
        )

    # Banner é HTTP de verdade (conecta no host): último a rodar e primeiro a
    # ser cortado quando o orçamento aperta.
    banner_hosts = ([e["host"] for e in ordered
                     if e["ip"] and e["role"] in ("site", "host")][:BANNER_LIMIT]
                    if remaining() > 6 else [])
    if not banner_hosts and ordered:
        warnings.append("Banners HTTP não coletados: o tempo da coleta acabou.")

    ip_info: Dict[str, dict] = {}
    ptr_info: Dict[str, Optional[str]] = {}
    banners: Dict[str, Optional[dict]] = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        whois_futures = {ip: pool.submit(ip_identity, ip) for ip in whois_ips}
        ptr_futures = {ip: pool.submit(_ptr, ip) for ip in unique_ips[:HOST_LIMIT]}
        banner_futures = {h: pool.submit(fetch_http_banner, h) for h in banner_hosts}
        for ip, future in whois_futures.items():
            try:
                ip_info[ip] = future.result(timeout=remaining(reserve=3))
            except Exception:
                ip_info[ip] = dict(_EMPTY_IP_INFO)
        for ip, future in ptr_futures.items():
            try:
                ptr_info[ip] = future.result(timeout=remaining(reserve=2))
            except Exception:
                ptr_info[ip] = None
        for host, future in banner_futures.items():
            try:
                banners[host] = future.result(timeout=remaining())
            except Exception:
                banners[host] = None

    for entry in ordered:
        info = ip_info.get(entry["ip"]) or {}
        entry["ptr"] = ptr_info.get(entry["ip"])
        entry["asn"] = info.get("asn")
        entry["asn_org"] = info.get("asn_org")
        entry["asn_cidr"] = info.get("asn_cidr")
        entry["asn_registry"] = info.get("asn_registry")
        entry["asn_allocated"] = info.get("asn_allocated")
        entry["country"] = info.get("country")
        entry["country_name"] = info.get("country_name") or country_label(info.get("country"))
        entry["provider"] = clean_asn_org(info.get("asn_org"))
        entry["http"] = banners.get(entry["host"])
    report["hosts"] = ordered

    host_by_name = {e["host"]: e for e in ordered}

    # MX e NS na forma de tabela, cada um com a rede por trás.
    mx_records = []
    provider_name, provider_conf = None, "none"
    for rec in mx_raw:
        entry = host_by_name.get(rec["host"]) or _host_entry(rec["host"], None, "mx")
        mx_records.append({
            "priority": rec["priority"], "host": rec["host"], "ip": entry["ip"],
            "ptr": entry["ptr"], "asn": entry["asn"], "asn_org": entry["asn_org"],
            "asn_cidr": entry["asn_cidr"], "asn_registry": entry.get("asn_registry"),
            "country": entry["country"], "country_name": entry["country_name"],
            "ttl": ttls.get("MX"),
        })
        if not provider_name:
            name, conf = identify_provider(rec["host"], entry["asn_org"])
            if name:
                provider_name, provider_conf = name, conf
    report["records"]["mx"] = mx_records
    report["records"]["ns"] = [
        {
            "host": host,
            "ip": (host_by_name.get(host) or {}).get("ip"),
            "asn": (host_by_name.get(host) or {}).get("asn"),
            "asn_org": (host_by_name.get(host) or {}).get("asn_org"),
            "country_name": (host_by_name.get(host) or {}).get("country_name"),
            "ttl": ttls.get("NS"),
        }
        for host in ns_raw
    ]

    # ── Resumo (é o que aparece fechado, antes de abrir o painel) ───────────
    site_entry = host_by_name.get(domain)
    hosting_info = ip_info.get(site_entry["ip"]) if site_entry and site_entry["ip"] else None
    hosting_provider = None
    if hosting_info and hosting_info.get("asn_org"):
        name, _conf = identify_provider("", hosting_info["asn_org"])
        hosting_provider = name or clean_asn_org(hosting_info["asn_org"])

    dmarc = report["email"]["dmarc"]
    total_records = (
        len(mx_records) + len(ns_raw) + len(report["records"]["a"])
        + len(report["records"]["aaaa"]) + len(txt_entries)
        + len(report["records"]["caa"]) + len(report["records"]["srv"])
        + len(report["records"]["cname"]) + (1 if report["records"]["soa"] else 0)
    )
    report["summary"] = {
        "mx_host": mx_records[0]["host"] if mx_records else None,
        "mx_count": len(mx_records),
        "mx_provider": provider_name,
        "mx_provider_confidence": provider_conf,
        "hosting_provider": hosting_provider,
        "hosting_asn": (hosting_info or {}).get("asn"),
        "hosting_country": (hosting_info or {}).get("country_name"),
        "registrar": (report["registration"] or {}).get("registrar"),
        "owner": (report["registration"] or {}).get("owner"),
        "owner_cnpj": (report["registration"] or {}).get("owner_cnpj"),
        "registered_on": (report["registration"] or {}).get("registered_on"),
        "expires_on": (report["registration"] or {}).get("expires_on"),
        "dnssec": report["dnssec"]["signed"],
        "spf": bool(report["email"]["spf"]),
        "dmarc_policy": (dmarc or {}).get("policy"),
        "dkim_selectors": len(dkim_list),
        "mta_sts_mode": (report["email"]["mta_sts"] or {}).get("mode"),
        "records_total": total_records,
        "hosts_total": len(ordered),
        "stack_total": len(report["stack"]),
    }
    report["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    logger.info(
        "DNS intel domain=%s hosts=%d registros=%d elapsed=%dms",
        domain, len(ordered), total_records, report["elapsed_ms"],
    )
    return report
