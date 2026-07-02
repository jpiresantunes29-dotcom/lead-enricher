import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ._utils import HEADERS, normalize_domain, tld_to_region, is_public_url
from .phone_normalizer import pick_best_phone

logger = logging.getLogger(__name__)
TIMEOUT = int(os.getenv("SCRAPING_TIMEOUT", "15"))

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)


def _fetch(url: str) -> Optional[tuple]:
    """Devolve (soup, html) ou None. Bloqueia alvos de rede interna (anti-SSRF)."""
    if not is_public_url(url):
        logger.warning("Blocked non-public fetch target url=%s", url)
        return None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser"), resp.text
    except Exception as e:
        logger.debug("Fetch failed url=%s: %s", url, e)
        return None


def _meta(soup: BeautifulSoup, name: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
    if tag:
        return tag.get("content", "").strip() or None
    return None


def _jsonld_org(soup: BeautifulSoup) -> dict:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            t = data.get("@type", "")
            if any(x in t for x in ["Organization", "Corporation", "LocalBusiness"]):
                return data
        except Exception:
            continue
    return {}


def _extract_linkedin(soup: BeautifulSoup, text: str) -> Optional[str]:
    """Extrai link LinkedIn priorizando footer/header (social links da própria empresa)."""
    # 1. Tentar dentro de <footer> ou <header> primeiro
    for container_name in ("footer", "header"):
        container = soup.find(container_name)
        if container:
            for a in container.find_all("a", href=True):
                href = a["href"]
                if "linkedin.com/company/" in href:
                    return href.split("?")[0].rstrip("/")
    # 2. Qualquer âncora no documento
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "linkedin.com/company/" in href:
            return href.split("?")[0].rstrip("/")
    # 3. Fallback regex no texto
    match = re.search(r"https?://(?:www\.)?linkedin\.com/company/[^\s\"'<>]+", text)
    return match.group(0).rstrip("/") if match else None


def scrape_website(url: str) -> dict:
    """Scrapes a company homepage and returns extracted fields."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    fetched = _fetch(url)
    if not fetched:
        return {"status": "failed", "raw_input_url": url}
    soup, html = fetched

    # Detecta região para parsing de telefone
    domain = normalize_domain(url)
    region = tld_to_region(domain, default="BR")

    page_text = soup.get_text(separator=" ", strip=True)
    org = _jsonld_org(soup)

    company_name = (
        org.get("name")
        or _meta(soup, "og:site_name")
        or _meta(soup, "og:title")
        or (soup.title.string.strip() if soup.title and soup.title.string else None)
    )
    if company_name:
        company_name = re.sub(r"\s*[\|\-–—].*$", "", company_name).strip()

    description = (
        org.get("description")
        or _meta(soup, "og:description")
        or _meta(soup, "description")
    )
    if description:
        description = description[:500]

    location = None
    addr = org.get("address")
    if isinstance(addr, dict):
        location = addr.get("addressLocality") or addr.get("addressRegion")
    elif isinstance(addr, str):
        location = addr
    if not location:
        loc_obj = org.get("location")
        if isinstance(loc_obj, dict):
            location = loc_obj.get("name")
    if not location:
        location = _meta(soup, "geo.region") or _meta(soup, "geo.placename")

    emails = list(set(EMAIL_REGEX.findall(page_text)))
    corporate_email = None
    if emails:
        domain_emails = [e for e in emails if domain in e and "noreply" not in e and "no-reply" not in e]
        corporate_email = domain_emails[0] if domain_emails else emails[0]

    # Telefone via libphonenumber (substitui regex frouxo)
    phone = pick_best_phone(page_text, html=html, default_region=region)

    linkedin_url = _extract_linkedin(soup, page_text)

    sector = org.get("industry") or _meta(soup, "article:section")

    employee_count_raw = None
    emp = org.get("numberOfEmployees")
    if emp:
        employee_count_raw = str(emp.get("value", emp)) if isinstance(emp, dict) else str(emp)

    # Try /about pages for missing fields — fetch all in parallel
    if not location or not corporate_email or not phone or not linkedin_url:
        base = url.rstrip("/")
        suffixes = ["/about", "/sobre", "/contato", "/contact", "/about-us", "/quem-somos", "/empresa"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch, base + s): s for s in suffixes}
            for fut in as_completed(futures):
                result = fut.result()
                if not result:
                    continue
                about_soup, about_html = result
                about_text = about_soup.get_text(separator=" ", strip=True)
                if not corporate_email:
                    found_emails = EMAIL_REGEX.findall(about_text)
                    if found_emails:
                        corporate_email = found_emails[0]
                if not phone:
                    phone = pick_best_phone(about_text, html=about_html, default_region=region)
                if not linkedin_url:
                    linkedin_url = _extract_linkedin(about_soup, about_text)
                if not location:
                    location = _meta(about_soup, "geo.region") or _meta(about_soup, "geo.placename")
                if corporate_email and phone and linkedin_url and location:
                    break

    return {
        "status": "enriched",
        "raw_input_url": url,
        "company_name": company_name,
        "website": url,
        "linkedin_url": linkedin_url,
        "sector": sector,
        "employee_count": employee_count_raw,
        "location": location,
        "description": description,
        "corporate_email": corporate_email,
        "phone": phone,
    }
