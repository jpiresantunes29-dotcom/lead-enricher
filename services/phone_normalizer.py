"""
Normaliza telefones extraídos do HTML para o formato +XX X XXXX XXXX.

Usa libphonenumber (port oficial do Google) para:
  - filtrar candidatos inválidos (CPF, CNPJ, protocolos, números fake)
  - identificar país e tipo (mobile/fixed_line)
  - formatar de maneira consistente

Convenção de saída:
  +55 11 9 8888 7777   (celular BR)
  +55 11 4002 8922      (fixo BR)
  +1 415 555 0123       (US)
"""
import re
import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberType
from typing import List, Optional


def _format_pretty(num: phonenumbers.PhoneNumber) -> str:
    """
    Formata como +DDI DDD [9] XXXX XXXX (estilo brasileiro espaçado).
    Para outros países, usa INTERNATIONAL nativo do libphonenumber.
    """
    region = phonenumbers.region_code_for_number(num)
    intl = phonenumbers.format_number(num, PhoneNumberFormat.INTERNATIONAL)
    # intl vem como "+55 11 98888-7777" ou "+1 415-555-0123"
    # Padronizar: trocar todos os hífens por espaço
    pretty = intl.replace("-", " ")

    if region == "BR":
        # Garantir agrupamento celular: "+55 11 9 8888 7777"
        digits = re.sub(r"\D", "", phonenumbers.format_number(num, PhoneNumberFormat.E164))
        # E.164 BR: 55 + DDD(2) + número(8 fixo ou 9 móvel)
        if digits.startswith("55") and len(digits) == 13:
            ddd = digits[2:4]
            n = digits[4:]
            return f"+55 {ddd} {n[0]} {n[1:5]} {n[5:9]}"
        if digits.startswith("55") and len(digits) == 12:
            ddd = digits[2:4]
            n = digits[4:]
            return f"+55 {ddd} {n[:4]} {n[4:8]}"
    return pretty


def _classify_type(num: phonenumbers.PhoneNumber) -> str:
    t = phonenumbers.number_type(num)
    return {
        PhoneNumberType.MOBILE: "mobile",
        PhoneNumberType.FIXED_LINE: "fixed_line",
        PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
        PhoneNumberType.TOLL_FREE: "toll_free",
        PhoneNumberType.PREMIUM_RATE: "premium",
        PhoneNumberType.VOIP: "voip",
    }.get(t, "unknown")


def extract_and_normalize_phones(text: str, default_region: str = "BR") -> List[dict]:
    """
    Extrai todos os números válidos do texto.
    Retorna lista de dicts ordenada por relevância (válidos primeiro):
      {raw, formatted, type, country, valid}
    """
    if not text:
        return []

    found = []
    seen = set()
    try:
        matcher = phonenumbers.PhoneNumberMatcher(text, default_region)
    except Exception:
        return []

    for match in matcher:
        num = match.number
        if not phonenumbers.is_valid_number(num):
            continue
        e164 = phonenumbers.format_number(num, PhoneNumberFormat.E164)
        if e164 in seen:
            continue
        seen.add(e164)
        found.append({
            "raw": match.raw_string,
            "formatted": _format_pretty(num),
            "e164": e164,
            "type": _classify_type(num),
            "country": phonenumbers.region_code_for_number(num),
            "valid": True,
        })
    return found


def pick_best_phone(text: str, html: Optional[str] = None, default_region: str = "BR") -> Optional[str]:
    """
    Estratégia de escolha do telefone "principal":
      1. Número que aparece em href="tel:..." (mais confiável)
      2. Primeiro número móvel válido (cliente prefere whatsapp)
      3. Primeiro número fixo válido
      4. None
    Devolve já formatado como "+XX X XXXX XXXX".
    """
    # Prioridade 1: tel: links no HTML
    if html:
        tel_links = re.findall(r'href=["\']tel:([^"\']+)["\']', html, re.IGNORECASE)
        for raw_tel in tel_links:
            try:
                num = phonenumbers.parse(raw_tel, default_region)
                if phonenumbers.is_valid_number(num):
                    return _format_pretty(num)
            except Exception:
                continue

    # Prioridade 2 e 3: extração do texto
    candidates = extract_and_normalize_phones(text, default_region)
    if not candidates:
        return None

    # Móvel primeiro
    for c in candidates:
        if c["type"] == "mobile":
            return c["formatted"]
    # Fixo
    for c in candidates:
        if c["type"] in ("fixed_line", "fixed_or_mobile"):
            return c["formatted"]
    # Qualquer válido
    return candidates[0]["formatted"]
