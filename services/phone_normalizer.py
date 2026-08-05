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


# Faixas brasileiras SEM DDD: 0800 (grátis), 0300/4004 (custo compartilhado),
# 0900 (tarifado) e números de rede corporativa. Tratá-las como DDD+número é o
# que transformava "0800 887 0463" em "+55 80 0887 0463" — um DDD 80 que não
# existe, exibido na ficha como se fosse o telefone da empresa.
_NON_GEOGRAPHIC_TYPES = {
    PhoneNumberType.TOLL_FREE,
    PhoneNumberType.PREMIUM_RATE,
    PhoneNumberType.SHARED_COST,
    PhoneNumberType.UAN,
    PhoneNumberType.VOIP,
}


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
        if phonenumbers.number_type(num) in _NON_GEOGRAPHIC_TYPES:
            # 0800/0300/4004 se discam como estão, com o prefixo nacional —
            # é assim que aparecem no site da empresa e é assim que o vendedor
            # vai digitar no telefone.
            return phonenumbers.format_number(num, PhoneNumberFormat.NATIONAL).replace("-", " ")

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
        PhoneNumberType.SHARED_COST: "shared_cost",
        PhoneNumberType.UAN: "uan",
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

    # Ordem de preferência comercial: quem atende uma ligação de vendedor.
    # Central 0800 fica atrás do telefone direto — atende, mas cai em URA.
    for wanted in (
        ("mobile",),
        ("fixed_line", "fixed_or_mobile"),
        ("toll_free", "shared_cost", "uan", "voip"),
    ):
        for c in candidates:
            if c["type"] in wanted:
                return c["formatted"]
    # Qualquer válido
    return candidates[0]["formatted"]
