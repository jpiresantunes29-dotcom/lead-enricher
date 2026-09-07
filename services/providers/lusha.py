"""
Lusha — enriquecimento de contato com a chave DO USUÁRIO (modelo BYOA).

Diferente de `hunter.py`, que lê a chave do ambiente e vale para a instalação
inteira, aqui a credencial é de quem está usando: cada conta conecta o próprio
Lusha em Configurações e gasta os próprios créditos. Sem chave conectada, nada
aqui faz requisição — o produto continua 100% no caminho gratuito.

Por que isso existe: a auditoria do caminho gratuito mostrou um teto estrutural,
não de engenharia. Celular de executivo não está em fonte pública, e site
corporativo moderno não publica e-mail nominal (0 em 5 dos testados), então o
padrão de domínio raramente é aprendido e o palpite fica em ~45/100. O Lusha
fecha exatamente esse buraco para quem já paga por ele.

## Custo (confirmado na documentação da Lusha)

  e-mail            1 crédito
  telefone          5 créditos
  qualquer chamada  mínimo 1 crédito, MESMO sem resultado

Esse mínimo é o motivo de este módulo ser sempre o último passo da cascata:
chamar a Lusha para um contato que o caminho gratuito já resolveu é crédito
do usuário queimado à toa.

## Limite de requisições por plano

  Free/Starter   40/min,  100/dia
  Pro            50/min,  1.500/dia
  Premium/Scale  300/min, 18.000+/dia

Um 429 não é erro nosso: devolvemos None e o contato segue com o que o
gratuito conseguiu.

## Onde o dado da Lusha vai parar (decisão de produto, tomada em 06/09/2026)

O contato revelado é gravado nas tabelas globais (`Person`, `PersonEmail`,
`PersonPhone`), que não são segmentadas por usuário. Consequência: um e-mail
ou celular pago pela conta Lusha do usuário A passa a ser servido a qualquer
outro usuário que busque a mesma empresa, pelo cache (`decision_finder`, fonte
-1) e pelo `reveal` a partir do cache.

Isso é deliberado — a base compartilhada é o que faz o produto ficar melhor a
cada busca. Fica registrado aqui porque tem contrapartida: contratos de
provedores de dados B2B costumam restringir redistribuição a terceiros, então
é um ponto a revisar se os termos da Lusha mudarem ou se houver auditoria.
Isolar o dado por dono exigiria uma coluna de proprietário em `PersonEmail`
e `PersonPhone` e filtro nos dois pontos de leitura.

## Formato da resposta

A Lusha versionou o payload ao longo do tempo (v1 → v2 → v3) e a estrutura
exata não está publicada de forma estável. Por isso a extração aqui é
tolerante: procura e-mails e telefones em vários caminhos e formatos
conhecidos, em vez de assumir um único caminho fixo que quebraria em silêncio
na próxima mudança deles. Se a Lusha responder algo que não reconhecemos, o
resultado é "não achei" — nunca uma exceção que derruba a revelação inteira.
"""
import logging
import re
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.lusha.com/v2/person"
_TIMEOUT = 8

#: Confiança atribuída ao dado vindo da Lusha. Alta porque é contato verificado
#: por provedor pago, mas abaixo de 97 (SMTP confirmado por nós) de propósito:
#: nós checamos a caixa agora, a Lusha checou quando coletou.
CONF_LUSHA = 92

#: Como a Lusha rotula o tipo do telefone → nosso vocabulário.
_PHONE_TYPE_MAP = {
    "mobile": "mobile",
    "cell": "mobile",
    "cellphone": "mobile",
    "direct": "mobile",
    "work": "company",
    "office": "company",
    "hq": "company",
    "landline": "fixed_line",
    "fixed": "fixed_line",
}


def is_configured(api_key: Optional[str] = None) -> bool:
    """
    Há chave utilizável? Diferente dos outros provedores, a resposta depende
    do usuário da vez — não existe "configurado" global neste módulo.
    """
    return bool(api_key and api_key.strip())


def credencial_valida(api_key: str) -> bool:
    """
    A Lusha aceita esta chave? Usado na hora de conectar a conta.

    Distingue "chave recusada" de "Lusha fora do ar": só um 401/403 explícito
    reprova. Se a Lusha não responder, aceitamos a chave — recusar por
    indisponibilidade deles impediria o usuário de conectar por um motivo que
    não é dele nem nosso, e uma chave errada aparece depois como revelação sem
    resultado, não como dado errado.
    """
    if not is_configured(api_key):
        return False
    try:
        resp = requests.get(
            _API_URL,
            # Consulta mínima só para exercitar a autenticação. A Lusha cobra
            # no mínimo 1 crédito por chamada, então isso custa 1 crédito do
            # usuário — uma vez, no momento de conectar.
            params={"firstName": "Test", "lastName": "User", "companyDomain": "lusha.com"},
            headers={"api_key": api_key.strip(), "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.info("Não deu para validar a chave da Lusha agora: %s", e)
        return True
    if resp.status_code in (401, 403):
        return False
    return True


def _digits_to_e164(raw: str, default_country: str = "55") -> Optional[str]:
    """
    Telefone da Lusha → E.164. Aceita '+55 11 98765-4321', '5511987654321'
    e '(11) 98765-4321'.

    Só assume o DDI brasileiro quando o número tem cara de nacional (10 ou 11
    dígitos). Prefixar '55' em um número que já é de outro país produziria um
    telefone que não existe, e um telefone errado é pior que nenhum: o vendedor
    liga, fala com um estranho e perde a confiança na ficha inteira.
    """
    if not raw:
        return None
    text = str(raw).strip()
    explicit_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if explicit_plus:
        return f"+{digits}"
    if len(digits) in (10, 11):
        return f"+{default_country}{digits}"
    if digits.startswith(default_country) and len(digits) in (12, 13):
        return f"+{digits}"
    # Comprimento que não reconhecemos: devolver com '+' cru seria inventar um
    # DDI. Melhor não entregar telefone do que entregar um que não disca.
    return None


def _walk(node, key_matcher, out: list, depth: int = 0) -> None:
    """
    Varre a resposta procurando valores cujas chaves casem com `key_matcher`.

    Existe porque a estrutura do payload da Lusha não é estável entre versões:
    e-mail já apareceu como `emailAddresses[].email`, `emails[].address` e
    `email` solto. Varrer é mais robusto que fixar um caminho — e o custo é
    irrelevante para um payload de um contato.
    """
    if depth > 6:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key_matcher(key):
                if isinstance(value, str) and value.strip():
                    out.append(value.strip())
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item.strip():
                            out.append(item.strip())
                        elif isinstance(item, dict):
                            _walk(item, key_matcher, out, depth + 1)
            if isinstance(value, (dict, list)):
                _walk(value, key_matcher, out, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _walk(item, key_matcher, out, depth + 1)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def _extract_emails(payload: dict) -> List[str]:
    found: List[str] = []
    _walk(payload, lambda k: "email" in k.lower() or k.lower() == "address", found)
    seen, out = set(), []
    for value in found:
        value = value.lower()
        if _EMAIL_RE.match(value) and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _extract_phones(payload: dict) -> List[dict]:
    """Telefones com tipo, quando a Lusha informa. Celular vem primeiro."""
    raw_items: List[dict] = []

    def collect(node, depth: int = 0):
        if depth > 6:
            return
        if isinstance(node, dict):
            # Objeto de telefone: tem um campo de número e (às vezes) um tipo
            number = None
            for key in ("number", "phoneNumber", "phone", "internationalNumber", "e164"):
                value = node.get(key)
                if isinstance(value, str) and any(c.isdigit() for c in value):
                    number = value
                    break
            if number:
                type_raw = ""
                for key in ("phoneType", "type", "numberType"):
                    value = node.get(key)
                    if isinstance(value, str):
                        type_raw = value
                        break
                raw_items.append({"number": number, "type_raw": type_raw})
            for value in node.values():
                if isinstance(value, (dict, list)):
                    collect(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, str) and any(c.isdigit() for c in item):
                    raw_items.append({"number": item, "type_raw": ""})
                else:
                    collect(item, depth + 1)

    collect(payload)

    seen, out = set(), []
    for item in raw_items:
        e164 = _digits_to_e164(item["number"])
        if not e164 or e164 in seen:
            continue
        seen.add(e164)
        out.append({
            "e164": e164,
            "formatted": item["number"],
            "type": _PHONE_TYPE_MAP.get(item["type_raw"].strip().lower(), "unknown"),
            "confidence": CONF_LUSHA,
        })
    # Celular é o que o gratuito nunca consegue — vale mais na ficha.
    out.sort(key=lambda p: 0 if p["type"] == "mobile" else 1)
    return out


def _extract_title(payload: dict) -> Optional[str]:
    for key in ("jobTitle", "title", "position", "headline"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            inner = value.get("title") or value.get("name")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return None


def find_contacts(
    full_name: Optional[str] = None,
    domain: Optional[str] = None,
    company_name: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    api_key: Optional[str] = None,
    **_ignored,
) -> Optional[dict]:
    """
    Enriquece uma pessoa na Lusha. Devolve None quando não há chave, quando a
    Lusha não reconhece o contato, ou em qualquer falha — nunca levanta.

    Precisa de LinkedIn OU (nome + empresa/domínio). O LinkedIn é preferido:
    é identificador único, enquanto "João Silva" + "Acme" pode casar com a
    pessoa errada.
    """
    if not is_configured(api_key):
        return None

    params = {}
    if linkedin_url:
        params["linkedinUrl"] = linkedin_url
    if full_name:
        parts = [p for p in str(full_name).split() if p]
        if parts:
            params["firstName"] = parts[0]
            if len(parts) > 1:
                params["lastName"] = parts[-1]
    if domain:
        params["companyDomain"] = domain
    if company_name:
        params["companyName"] = company_name

    # Sem identificador forte a Lusha cobraria o mínimo de 1 crédito para
    # devolver nada — não vale gastar o dinheiro do usuário nesse palpite.
    tem_identidade = "linkedinUrl" in params or (
        "firstName" in params and ("companyDomain" in params or "companyName" in params)
    )
    if not tem_identidade:
        return None

    try:
        resp = requests.get(
            _API_URL,
            params=params,
            headers={"api_key": api_key.strip(), "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.info("Lusha indisponível (segue no gratuito): %s", e)
        return None

    if resp.status_code == 401:
        logger.warning("Lusha recusou a chave do usuário (401).")
        return None
    if resp.status_code == 402:
        logger.warning("Lusha sem créditos na conta do usuário (402).")
        return None
    if resp.status_code == 429:
        logger.warning("Lusha atingiu o limite de requisições do plano (429).")
        return None
    if resp.status_code == 404:
        return None  # contato não está na base deles — resposta normal
    if resp.status_code != 200:
        logger.info("Lusha respondeu %s", resp.status_code)
        return None

    try:
        payload = resp.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    emails = _extract_emails(payload)
    phones = _extract_phones(payload)
    if not emails and not phones:
        return None

    return {
        "provider": "lusha",
        "emails": [
            {"email": e, "status": "unknown", "confidence": CONF_LUSHA}
            for e in emails
        ],
        "phones": phones,
        "title": _extract_title(payload.get("data") or payload),
    }
