"""
Helpers compartilhados entre os services do LeadEnricher.

Centraliza:
  - HEADERS HTTP padrão (User-Agent, Accept-Language) para evitar duplicação
  - normalize_domain() para limpar input do usuário
  - tld_to_region() para inferir região default do número de telefone
  - LINKEDIN_COMPANY_RE para extrair slug de URLs do LinkedIn
  - is_public_host() — guard anti-SSRF para fetches de URLs derivadas de input
"""
import ipaddress
import re
import socket
from typing import Optional
from urllib.parse import urlparse


LINKEDIN_COMPANY_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/([a-zA-Z0-9\-._%]+)",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


def normalize_domain(value: str) -> str:
    """Remove protocolo, path, query string e prefixo www."""
    value = (value or "").strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/")[0].split("?")[0]
    if value.startswith("www."):
        value = value[4:]
    return value


# Mapa TLD → região default ISO-3166 alpha-2 (para libphonenumber)
TLD_REGION_MAP = {
    "br": "BR",
    "pt": "PT",
    "ar": "AR",
    "cl": "CL",
    "co": "CO",
    "mx": "MX",
    "uy": "UY",
    "pe": "PE",
    "us": "US",
    "uk": "GB",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "nl": "NL",
    "ca": "CA",
    "au": "AU",
    "jp": "JP",
}


def tld_to_region(domain: str, default: str = "BR") -> str:
    """Infere a região do telefone a partir do TLD do domínio."""
    domain = normalize_domain(domain)
    parts = domain.split(".")
    if len(parts) >= 2:
        tld = parts[-1]
        return TLD_REGION_MAP.get(tld, default)
    return default


def is_public_host(host: str) -> bool:
    """
    Guard anti-SSRF: resolve o host e garante que NENHUM dos IPs cai em
    faixa privada, loopback, link-local ou reservada.

    Retorna False para hosts que não resolvem (não há o que buscar) ou que
    apontam para rede interna (ex.: localhost, 10.x, 169.254.x — metadata).
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    ips = {info[4][0] for info in infos}
    if not ips:
        return False
    for raw in ips:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def is_public_url(url: str) -> bool:
    """Aplica is_public_host() ao hostname de uma URL http(s)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return is_public_host(parsed.hostname)
