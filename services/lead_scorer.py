"""
Nota de 0 a 100 de um lead — em quem ligar primeiro.

O produto coleta muita coisa sobre um domínio, mas coletar não é priorizar:
uma lista de 200 fichas completas continua sendo 200 decisões manuais. Este
módulo transforma o que já foi coletado numa ordem de trabalho.

Três decisões de projeto sustentam o resto:

1. **Função pura.** `score_lead()` não toca banco, rede nem relógio. Recebe a
   ficha e os decisores, devolve um dicionário. É o que permite testar os 14
   sinais sem subir aplicação, e recalcular em lote sem efeito colateral.

2. **O peso segue o vendedor, não o engenheiro.** Um DMARC em `reject` é um
   sinal técnico bonito e vale pouco para quem vai ligar; um decisor com
   telefone vale muito. Por isso o eixo ALCANCE pesa mais que o eixo MATURIDADE
   — a pergunta que a nota responde é "consigo falar com alguém que decide?",
   não "esta empresa é bem configurada?".

3. **A nota é normalizada, o detalhamento é bruto.** `score` sai sempre em
   0-100 para ser comparável entre fichas; `breakdown` guarda ponto a ponto o
   que cada sinal contribuiu, porque a tela precisa responder "por que 47?" —
   uma nota que ninguém consegue auditar é uma nota em que ninguém confia.

Mudou peso ou sinal? Suba `SCORING_VERSION`. A coluna `score_version` grava a
versão junto com a nota, então uma ficha pontuada pela régua antiga é
reconhecível e pode ser recalculada depois — sem isso, notas de réguas
diferentes conviveriam na mesma lista sem ninguém perceber.
"""
from typing import Any, Dict, List, Optional, Sequence

#: Régua vigente. Ver o cabeçalho do módulo antes de mudar.
SCORING_VERSION = "SCORING_V1"

#: Faixas de prioridade a partir da nota normalizada. Os rótulos são os que a
#: interface pinta de 🔴 🟡 🔵.
PRIORITY_HIGH = "alta"
PRIORITY_MEDIUM = "media"
PRIORITY_LOW = "baixa"
PRIORITIES = (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)

#: Limiares (inclusivos) da nota normalizada para cada faixa.
THRESHOLD_HIGH = 70
THRESHOLD_MEDIUM = 40

#: Provedores de e-mail corporativo. Não é sinal de tamanho — é sinal de que a
#: empresa terceirizou e-mail para quem cobra por caixa, o que separa negócio
#: montado de site parado com e-mail do provedor de hospedagem.
_CORPORATE_MX = ("google", "microsoft", "outlook", "office", "zoho", "proofpoint", "mimecast")

#: Bandas de `employee_count` que indicam empresa com estrutura de compra.
#: Abaixo de 11 pessoas normalmente não há função de decisão separada; acima de
#: 5000 o ciclo é longo e raramente entra por prospecção fria.
_BAND_POINTS = {
    "1-10": 1,
    "11-50": 4,
    "51-200": 6,
    "201-500": 6,
    "501-1000": 5,
    "1001-5000": 4,
    "5001-10000": 2,
    "10000+": 2,
}


def _as_dict(value: Any) -> dict:
    """`employee_count` chega como dict do banco, mas pode vir nulo ou sujo."""
    return value if isinstance(value, dict) else {}


def _emails_de(dm: Any) -> List[dict]:
    emails = getattr(dm, "probable_emails", None)
    if not isinstance(emails, list):
        return []
    return [e for e in emails if isinstance(e, dict)]


def _melhor_status_de_email(decision_makers: Sequence[Any]) -> Optional[str]:
    """
    Melhor status de e-mail entre todos os decisores.

    "Melhor" e não "média" de propósito: para ligar basta UM caminho que
    funcione. Uma ficha com nove e-mails inválidos e um verificado vale o
    verificado.
    """
    ordem = {"valid": 3, "catch_all": 2, "unknown": 1, "invalid": 0}
    melhor: Optional[str] = None
    for dm in decision_makers:
        for item in _emails_de(dm):
            status = item.get("status")
            if status in ordem and (melhor is None or ordem[status] > ordem[melhor]):
                melhor = status
    return melhor


def _sinal(key: str, label: str, points: float, max_points: float,
           detail: str) -> dict:
    """Uma linha do detalhamento — é isto que a tela lista no popover."""
    return {
        "key": key,
        "label": label,
        "points": round(points, 1),
        "max": max_points,
        "detail": detail,
        "hit": points > 0,
    }


# ── Eixo 1: ALCANCE — dá para falar com quem decide? ────────────────────────


def _sinais_de_alcance(lead: Any, dms: Sequence[Any]) -> List[dict]:
    sinais = []

    # Decisores encontrados. O primeiro vale desproporcionalmente mais que o
    # quinto: sair de zero nomes para um nome é o que destrava a ligação;
    # do terceiro em diante é conforto, não desbloqueio.
    n = len(dms)
    pontos = {0: 0, 1: 7, 2: 10}.get(n, 12 if n >= 3 else 0)
    sinais.append(_sinal(
        "decisores", "Decisores identificados", pontos, 12,
        f"{n} decisor(es) na ficha" if n else "Nenhum decisor identificado",
    ))

    # E-mail de decisor verificado no SMTP. É o sinal mais caro de obter e o
    # que mais economiza tempo: e-mail que rebate queima o contato.
    status = _melhor_status_de_email(dms)
    pontos_email = {"valid": 10, "catch_all": 4, "unknown": 2}.get(status or "", 0)
    detalhe_email = {
        "valid": "E-mail de decisor verificado no servidor",
        "catch_all": "Domínio aceita tudo — e-mail provável, não confirmado",
        "unknown": "E-mail deduzido do padrão, sem verificação",
        "invalid": "Só e-mails recusados pelo servidor",
    }.get(status or "", "Nenhum e-mail de decisor")
    sinais.append(_sinal("email_decisor", "E-mail de decisor", pontos_email, 10, detalhe_email))

    # Telefone de decisor. Celular pessoal é o caminho mais curto que existe
    # para uma conversa e quase nunca vem do caminho gratuito.
    com_telefone = sum(1 for dm in dms if getattr(dm, "phone", None))
    pontos_tel = 9 if com_telefone else 0
    sinais.append(_sinal(
        "telefone_decisor", "Telefone de decisor", pontos_tel, 9,
        f"{com_telefone} decisor(es) com telefone" if com_telefone
        else "Nenhum telefone de decisor",
    ))

    # Telefone da empresa: não é o decisor, mas é uma porta de entrada real.
    tem_tel_empresa = bool(getattr(lead, "phone", None))
    sinais.append(_sinal(
        "telefone_empresa", "Telefone da empresa", 4 if tem_tel_empresa else 0, 4,
        "Telefone da empresa disponível" if tem_tel_empresa else "Sem telefone da empresa",
    ))

    # E-mail corporativo genérico (contato@, comercial@). Vale pouco — chega
    # numa caixa compartilhada — mas é melhor que nada.
    tem_email = bool(getattr(lead, "corporate_email", None))
    sinais.append(_sinal(
        "email_corporativo", "E-mail corporativo", 3 if tem_email else 0, 3,
        "E-mail corporativo publicado" if tem_email else "Sem e-mail corporativo",
    ))

    return sinais


# ── Eixo 2: MATURIDADE — é uma operação montada? ────────────────────────────


def _sinais_de_maturidade(lead: Any) -> List[dict]:
    sinais = []
    report = getattr(lead, "dns_report", None)
    email_cfg = (report or {}).get("email") if isinstance(report, dict) else None
    email_cfg = email_cfg if isinstance(email_cfg, dict) else {}

    # Provedor de e-mail pago = alguém assina uma fatura por caixa de correio.
    mx = (getattr(lead, "mx_provider", None) or "").lower()
    if mx and any(p in mx for p in _CORPORATE_MX):
        pontos_mx, detalhe_mx = 6, f"E-mail corporativo em {lead.mx_provider}"
    elif mx:
        pontos_mx, detalhe_mx = 3, f"MX próprio ou de hospedagem ({lead.mx_provider})"
    else:
        pontos_mx, detalhe_mx = 0, "Sem registro MX identificado"
    sinais.append(_sinal("mx_provider", "Provedor de e-mail", pontos_mx, 6, detalhe_mx))

    # SPF: existe alguém cuidando do domínio.
    spf = email_cfg.get("spf")
    tem_spf = isinstance(spf, dict)
    sinais.append(_sinal(
        "spf", "SPF publicado", 3 if tem_spf else 0, 3,
        "SPF configurado" if tem_spf else "Sem SPF",
    ))

    # DMARC: em `quarantine`/`reject` significa política ativa, não enfeite.
    dmarc = email_cfg.get("dmarc")
    if isinstance(dmarc, dict) and dmarc.get("enforced"):
        pontos_dmarc, detalhe_dmarc = 4, f"DMARC ativo ({dmarc.get('policy')})"
    elif isinstance(dmarc, dict):
        pontos_dmarc, detalhe_dmarc = 2, "DMARC apenas em monitoramento (p=none)"
    else:
        pontos_dmarc, detalhe_dmarc = 0, "Sem DMARC"
    sinais.append(_sinal("dmarc", "Política DMARC", pontos_dmarc, 4, detalhe_dmarc))

    # DKIM: assinatura de saída configurada.
    dkim = email_cfg.get("dkim")
    tem_dkim = isinstance(dkim, list) and len(dkim) > 0
    sinais.append(_sinal(
        "dkim", "DKIM publicado", 2 if tem_dkim else 0, 2,
        f"{len(dkim)} seletor(es) DKIM" if tem_dkim else "Sem DKIM",
    ))

    # Hospedagem identificada: site em infraestrutura reconhecível.
    hosting = getattr(lead, "hosting_provider", None)
    sinais.append(_sinal(
        "hosting", "Hospedagem identificada", 2 if hosting else 0, 2,
        f"Hospedado em {hosting}" if hosting else "Hospedagem não identificada",
    ))

    return sinais


# ── Eixo 3: PORTE E IDENTIDADE — é a empresa que eu quero? ──────────────────


def _sinais_de_porte(lead: Any) -> List[dict]:
    sinais = []

    # Tamanho. A curva é proposital: nem microempresa nem multinacional é o
    # alvo típico de prospecção fria — ver `_BAND_POINTS`.
    emp = _as_dict(getattr(lead, "employee_count", None))
    exato = getattr(lead, "employee_count_linkedin", None) or emp.get("exact")
    banda = emp.get("band")
    pontos_tam = _BAND_POINTS.get(banda or "", 0)
    if pontos_tam and exato:
        # Contagem exata da aba People vale mais que faixa deduzida do HTML.
        pontos_tam += 2
    detalhe_tam = (
        f"{exato} funcionários" if exato else
        f"Faixa {banda}" if banda else "Tamanho desconhecido"
    )
    sinais.append(_sinal("tamanho", "Porte da empresa", pontos_tam, 8, detalhe_tam))

    # LinkedIn confirmado: existe página real e ela foi casada com o domínio,
    # que é o que permite achar decisores depois.
    conf = (getattr(lead, "linkedin_confidence", None) or "").lower()
    pontos_li = {"verified": 5, "probable": 3, "unverified": 1}.get(conf, 0)
    if not getattr(lead, "linkedin_url", None):
        pontos_li = 0
    sinais.append(_sinal(
        "linkedin", "Página no LinkedIn", pontos_li, 5,
        f"LinkedIn {conf}" if pontos_li else "Sem LinkedIn confirmado",
    ))

    # Setor: sem ele não dá para segmentar nem escrever abordagem.
    setor = getattr(lead, "sector", None)
    sinais.append(_sinal(
        "setor", "Setor identificado", 3 if setor else 0, 3,
        f"Setor: {setor}" if setor else "Setor não identificado",
    ))

    # Descrição: matéria-prima do roteiro de ligação e do resumo de IA.
    desc = getattr(lead, "description", None) or ""
    tem_desc = len(desc.strip()) >= 40
    sinais.append(_sinal(
        "descricao", "Descrição da empresa", 2 if tem_desc else 0, 2,
        "Descrição coletada" if tem_desc else "Sem descrição aproveitável",
    ))

    # Localização: define fuso, idioma da abordagem e quem atende a conta.
    local = getattr(lead, "location", None)
    sinais.append(_sinal(
        "localizacao", "Localização", 2 if local else 0, 2,
        f"Localização: {local}" if local else "Localização desconhecida",
    ))

    return sinais


def _priority_for(score: int) -> str:
    if score >= THRESHOLD_HIGH:
        return PRIORITY_HIGH
    if score >= THRESHOLD_MEDIUM:
        return PRIORITY_MEDIUM
    return PRIORITY_LOW


def score_lead(lead: Any, decision_makers: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """
    Pontua uma ficha. Sem banco, sem rede, sem relógio.

    `decision_makers` é opcional só para permitir pontuar uma ficha recém
    coletada, antes da busca de decisores. Quando vier `None`, os decisores já
    ligados à ficha são usados — passar a lista explicitamente evita uma ida ao
    banco por lazy-load em quem já os tem em mãos.

    Devolve `{score, priority, breakdown, version}`, pronto para gravar nas
    quatro colunas de mesmo nome.
    """
    if decision_makers is None:
        decision_makers = list(getattr(lead, "decision_makers", None) or [])

    grupos = [
        ("alcance", "Alcance comercial", _sinais_de_alcance(lead, decision_makers)),
        ("maturidade", "Maturidade digital", _sinais_de_maturidade(lead)),
        ("porte", "Porte e identidade", _sinais_de_porte(lead)),
    ]

    total = sum(s["points"] for _, _, sinais in grupos for s in sinais)
    teto = sum(s["max"] for _, _, sinais in grupos for s in sinais)

    # Teto é constante da régua, mas dividir por ele à mão numa constante
    # separada convidaria os dois a divergirem no primeiro sinal novo.
    score = int(round(100 * total / teto)) if teto else 0
    score = max(0, min(100, score))
    priority = _priority_for(score)

    breakdown = {
        "version": SCORING_VERSION,
        "score": score,
        "priority": priority,
        "points": round(total, 1),
        "max_points": teto,
        "groups": [
            {
                "key": key,
                "label": label,
                "points": round(sum(s["points"] for s in sinais), 1),
                "max": sum(s["max"] for s in sinais),
                "signals": sinais,
            }
            for key, label, sinais in grupos
        ],
    }

    return {
        "score": score,
        "priority": priority,
        "score_breakdown": breakdown,
        "score_version": SCORING_VERSION,
    }


def apply_score(lead: Any, decision_makers: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """
    `score_lead()` + gravação nos campos da ficha. NÃO commita.

    Existe para que os quatro pontos que pontuam (coleta, pós-decisores,
    recálculo sob demanda e recálculo em lote) escrevam as mesmas quatro
    colunas do mesmo jeito — a alternativa era repetir quatro atribuições em
    quatro arquivos e descobrir a divergência quando uma delas esquecesse
    `score_version`.
    """
    resultado = score_lead(lead, decision_makers)
    lead.score = resultado["score"]
    lead.priority = resultado["priority"]
    lead.score_breakdown = resultado["score_breakdown"]
    lead.score_version = resultado["score_version"]
    return resultado
