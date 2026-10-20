"""
Cadência comercial — três fluxogramas de prospecção como máquina de estados.

    Agendamento do 1º contato   lote de 50 (40 T1 / 5 T2 / 5 T3) numa data
    Contatos e Qualificação     1ª tentativa → D+7 → D+7 → aquecimento;
                                telefone errado → mapear contato alternativo;
                                contato → reunião → qualificado + demanda →
                                proposta, senão aquecimento
    Aquecimento de lead frio    quinzenal: e-mail 1, 2, 3, WhatsApp lista — e
                                recomeça

O vendedor não monta cronograma: ele diz o que aconteceu (`resultado`) e a
máquina cria a próxima tarefa, na data certa. Cada tarefa leva `step`, que é
o que a agenda usa para dizer o que fazer e com que texto.

Nada aqui commita: as funções recebem a sessão do chamador e devolvem o que
criaram, como em `activity_rules.apply_rules`.
"""
from datetime import date, datetime, time, timedelta, UTC
from typing import Dict, List, Optional, Sequence
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from models.database import Activity, DecisionMaker, Lead
from services.activity_rules import add_business_days

#: O fuso dos vendedores. "Agendar para dia 21 às 9h" é 9h de Brasília, não UTC.
TZ = ZoneInfo("America/Sao_Paulo")

# Estados (Lead.cadence). Nulo = base não executada.
AGENDADO = "agendado"
TENTATIVAS = "tentativas"
QUALIFICACAO = "qualificacao"
PROPOSTA = "proposta"
AQUECIMENTO = "aquecimento"
ENCERRADO = "encerrado"
CADENCES = (AGENDADO, TENTATIVAS, QUALIFICACAO, PROPOSTA, AQUECIMENTO, ENCERRADO)

MAX_TENTATIVAS = 3
DIAS_ENTRE_TENTATIVAS = 7
DIAS_AQUECIMENTO = 14  # quinzenal

#: Lote padrão do fluxograma de agendamento.
LOTE_PADRAO = {1: 40, 2: 5, 3: 5}

#: Etapas: rótulo, tipo da atividade e o texto que vai no link de contato.
STEPS: Dict[str, Dict[str, str]] = {
    "primeiro_contato": {"titulo": "1º contato", "type": "call",
                         "dica": "Faça 2 tentativas seguidas. Registre o resultado."},
    "tentativa_2": {"titulo": "2ª tentativa de contato", "type": "call",
                    "dica": "Sem retorno na 1ª. Faça 2 tentativas seguidas."},
    "tentativa_3": {"titulo": "3ª tentativa de contato", "type": "call",
                    "dica": "Última tentativa antes do aquecimento."},
    "contato_alternativo": {"titulo": "Mapear contato alternativo", "type": "task",
                            "dica": "Telefone errado. Cadastre outro contato do decisor."},
    "reuniao_qualificacao": {"titulo": "Reunião de qualificação", "type": "meeting",
                             "dica": "Confirme demanda, prazo e quem decide."},
    "proposta": {"titulo": "Construir proposta comercial", "type": "task",
                 "dica": "Lead qualificado com demanda imediata."},
    "aquec_email_1": {"titulo": "Aquecimento — e-mail 1", "type": "email",
                      "dica": "Apresente a Skymail e um caso parecido."},
    "aquec_email_2": {"titulo": "Aquecimento — e-mail 2", "type": "email",
                      "dica": "Compartilhe um conteúdo útil para o setor."},
    "aquec_email_3": {"titulo": "Aquecimento — e-mail 3", "type": "email",
                      "dica": "Convide para uma conversa rápida."},
    "aquec_whatsapp": {"titulo": "Aquecimento — WhatsApp lista", "type": "task",
                       "dica": "Mensagem curta na lista de transmissão."},
}
_AQUECIMENTO = ("aquec_email_1", "aquec_email_2", "aquec_email_3", "aquec_whatsapp")
_TENTATIVA_STEP = {1: "primeiro_contato", 2: "tentativa_2", 3: "tentativa_3"}

RESULTADOS = (
    "iniciar", "sem_contato", "telefone_errado", "reuniao_marcada",
    "qualificado_com_demanda", "qualificado_sem_demanda", "nao_qualificado",
    "aquecimento", "ganho", "perdido", "encerrar",
)


class CadenceError(ValueError):
    """Resultado que não cabe no estado atual da ficha (vira 422)."""


# ── Datas ────────────────────────────────────────────────────────────────────

def _local(dia: date, hora: time) -> datetime:
    return datetime.combine(dia, hora, tzinfo=TZ).astimezone(UTC)


def _dia_util(dt: datetime) -> datetime:
    """Sábado/domingo empurram para segunda, preservando a hora."""
    local = dt.astimezone(TZ)
    while local.weekday() >= 5:
        local += timedelta(days=1)
    return local.astimezone(UTC)


def em_dias(agora: datetime, dias: int, hora: time = time(9, 0)) -> datetime:
    """D+N corrido, às `hora` de Brasília, caindo sempre em dia útil."""
    alvo = agora.astimezone(TZ).date() + timedelta(days=dias)
    return _dia_util(_local(alvo, hora))


# ── Peças ────────────────────────────────────────────────────────────────────

def contato_principal(lead: Lead) -> Optional[DecisionMaker]:
    """O principal; sem ele, o primeiro com telefone; sem telefone, o primeiro."""
    dms = list(lead.decision_makers or [])
    for dm in dms:
        if dm.is_primary:
            return dm
    return next((dm for dm in dms if dm.phone), dms[0] if dms else None)


def _nova(lead: Lead, user_id: str, step: str, due_at: datetime,
          notas: Optional[str] = None) -> Activity:
    meta = STEPS[step]
    dm = contato_principal(lead)
    texto = meta["titulo"]
    if dm and dm.name:
        texto += f" — {dm.name}"
    if notas:
        texto += f"\n{notas}"
    return Activity(
        lead_id=lead.id, user_id=user_id, type=meta["type"], step=step,
        notes=texto, due_at=due_at, decision_maker_id=dm.id if dm else None,
    )


def abertas(db: Session, lead: Lead) -> List[Activity]:
    """Tarefas da cadência ainda não concluídas, mais cedo primeiro."""
    return (
        db.query(Activity)
        .filter(Activity.lead_id == lead.id, Activity.step.isnot(None),
                Activity.completed_at.is_(None))
        .order_by(Activity.due_at.asc())
        .all()
    )


def _concluir_abertas(db: Session, lead: Lead, agora: datetime,
                      steps: Optional[Sequence[str]] = None) -> List[int]:
    """Fecha as tarefas abertas (todas ou só as `steps`). Devolve os ids."""
    feitas = []
    for act in abertas(db, lead):
        if steps is None or act.step in steps:
            act.completed_at = agora
            feitas.append(act.id)
    return feitas


def _documentar(db: Session, lead: Lead, user_id: str, detalhe: str,
                agora: Optional[datetime] = None) -> None:
    """
    O "Documentar" dos fluxogramas: uma nota já concluída na timeline.

    Nota, e não `AuditLog`: é o vendedor quem precisa ler isso, na ficha, e a
    nota vai embora junto com o lead (a FK de `audit_logs` não tem cascata).
    """
    agora = agora or datetime.now(UTC)
    db.add(Activity(lead_id=lead.id, user_id=user_id, type="note",
                    notes=detalhe[:2000], completed_at=agora, created_at=agora))


def iniciar_aquecimento(lead: Lead, user_id: str, agora: datetime) -> List[Activity]:
    """Um ciclo quinzenal: e-mail 1 no próximo dia útil, depois a cada 14 dias."""
    lead.cadence = AQUECIMENTO
    inicio = em_dias(agora, 1)
    return [
        _nova(lead, user_id, step, _dia_util(inicio + timedelta(days=DIAS_AQUECIMENTO * i)))
        for i, step in enumerate(_AQUECIMENTO)
    ]


# ── Ações disponíveis por estado ─────────────────────────────────────────────

_ACOES = {
    "iniciar": ("Iniciar cadência hoje", "primary", False),
    "sem_contato": ("Sem contato", "ghost", False),
    "telefone_errado": ("Telefone errado", "ghost", False),
    "reuniao_marcada": ("Reunião marcada", "primary", True),
    "qualificado_com_demanda": ("Qualificado — tem demanda", "primary", False),
    "qualificado_sem_demanda": ("Qualificado — sem demanda agora", "ghost", False),
    "nao_qualificado": ("Não qualificado", "ghost", False),
    "aquecimento": ("Mandar para aquecimento", "ghost", False),
    "ganho": ("Venda ganha", "primary", False),
    "perdido": ("Venda perdida", "danger", False),
    "encerrar": ("Encerrar cadência", "danger", False),
}

_POR_ESTADO = {
    None: ("iniciar", "aquecimento"),
    AGENDADO: ("sem_contato", "telefone_errado", "reuniao_marcada", "encerrar"),
    TENTATIVAS: ("sem_contato", "telefone_errado", "reuniao_marcada", "encerrar"),
    QUALIFICACAO: ("qualificado_com_demanda", "qualificado_sem_demanda",
                   "nao_qualificado", "encerrar"),
    PROPOSTA: ("ganho", "perdido"),
    AQUECIMENTO: ("reuniao_marcada", "encerrar"),
    ENCERRADO: ("iniciar",),
}


def acoes(lead: Lead) -> List[Dict]:
    return [
        {"resultado": r, "label": _ACOES[r][0], "estilo": _ACOES[r][1], "pede_data": _ACOES[r][2]}
        for r in _POR_ESTADO.get(lead.cadence, ())
    ]


# ── A máquina ────────────────────────────────────────────────────────────────

def registrar(
    db: Session,
    lead: Lead,
    user_id: str,
    resultado: str,
    *,
    notas: Optional[str] = None,
    reuniao_em: Optional[datetime] = None,
    agora: Optional[datetime] = None,
) -> Dict:
    """
    Aplica um resultado à ficha. Devolve `{message, criadas, concluidas}`.
    Levanta `CadenceError` quando o resultado não vale no estado atual.
    """
    agora = agora or datetime.now(UTC)
    permitidos = _POR_ESTADO.get(lead.cadence, ())
    if resultado not in RESULTADOS:
        raise CadenceError(f"Resultado inválido. Use: {', '.join(RESULTADOS)}.")
    if resultado not in permitidos:
        raise CadenceError("Esta ação não vale na etapa atual do lead.")

    criadas: List[Activity] = []
    concluidas: List[int] = []

    # Toda ação de contato encerra a tarefa que a pedia — o vendedor não
    # precisa voltar na agenda para marcar "feito".
    if resultado != "iniciar":
        concluidas = _concluir_abertas(db, lead, agora)

    if resultado == "iniciar":
        lead.cadence = TENTATIVAS
        lead.cadence_attempt = 1
        lead.first_contact_at = lead.first_contact_at or agora
        criadas.append(_nova(lead, user_id, "primeiro_contato", agora, notas))
        msg = "Cadência iniciada — 1º contato na agenda de hoje."

    elif resultado == "sem_contato":
        if lead.cadence == AGENDADO:
            lead.cadence = TENTATIVAS
            lead.cadence_attempt = 1
        lead.first_contact_at = lead.first_contact_at or agora
        if lead.stage == "novo":
            lead.stage = "contatado"
        tentativa = max(lead.cadence_attempt or 1, 1)
        if tentativa >= MAX_TENTATIVAS:
            criadas.extend(iniciar_aquecimento(lead, user_id, agora))
            msg = "3 tentativas sem contato — lead foi para o aquecimento quinzenal."
        else:
            lead.cadence_attempt = tentativa + 1
            step = _TENTATIVA_STEP[lead.cadence_attempt]
            criadas.append(_nova(lead, user_id, step,
                                 em_dias(agora, DIAS_ENTRE_TENTATIVAS), notas))
            msg = f"Sem contato. {lead.cadence_attempt}ª tentativa agendada para D+7."

    elif resultado == "telefone_errado":
        if lead.cadence == AGENDADO:
            lead.cadence = TENTATIVAS
            lead.cadence_attempt = 1
        step = _TENTATIVA_STEP[max(lead.cadence_attempt or 1, 1)]
        criadas.append(_nova(lead, user_id, "contato_alternativo", agora, notas))
        # A tentativa continua valendo: reabre a mesma etapa para depois que o
        # contato certo for cadastrado.
        criadas.append(_nova(lead, user_id, step, add_business_days(agora, 1)))
        msg = "Telefone errado. Cadastre o contato alternativo — a tentativa foi reaberta para amanhã."

    elif resultado == "reuniao_marcada":
        lead.cadence = QUALIFICACAO
        lead.stage = "reuniao_agendada"
        lead.first_contact_at = lead.first_contact_at or agora
        quando = reuniao_em or _dia_util(_local(
            add_business_days(agora, 1).astimezone(TZ).date(), time(10, 0)))
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=TZ)
        criadas.append(_nova(lead, user_id, "reuniao_qualificacao", quando, notas))
        msg = "Reunião de qualificação agendada."

    elif resultado == "qualificado_com_demanda":
        lead.cadence = PROPOSTA
        lead.stage = "oportunidade"
        criadas.append(_nova(lead, user_id, "proposta", add_business_days(agora, 2), notas))
        msg = "Lead qualificado com demanda — proposta comercial na agenda."

    elif resultado in ("qualificado_sem_demanda", "nao_qualificado", "aquecimento"):
        criadas.extend(iniciar_aquecimento(lead, user_id, agora))
        msg = "Lead no aquecimento: 3 e-mails e 1 WhatsApp, a cada 15 dias."

    elif resultado in ("ganho", "perdido"):
        lead.cadence = ENCERRADO
        lead.stage = resultado
        msg = "Venda ganha!" if resultado == "ganho" else "Venda marcada como perdida."

    else:  # encerrar
        lead.cadence = ENCERRADO
        msg = "Cadência encerrada."

    for act in criadas:
        db.add(act)
    _documentar(db, lead, user_id,
                f"{_ACOES[resultado][0]}. {msg}" + (f"\n{notas}" if notas else ""), agora)
    return {"message": msg, "criadas": criadas, "concluidas": concluidas}


def ao_concluir(db: Session, act: Activity, lead: Lead, agora: Optional[datetime] = None) -> List[Activity]:
    """
    Gancho de `PATCH /api/activities/{id}` ao concluir uma tarefa.

    Fecha o laço do fluxograma de aquecimento: o último envio do ciclo
    (WhatsApp lista) agenda o próximo ciclo, 15 dias depois.
    """
    agora = agora or datetime.now(UTC)
    if act.step != "aquec_whatsapp" or lead.cadence != AQUECIMENTO:
        return []
    pendentes = [a for a in abertas(db, lead) if a.id != act.id and a.step in _AQUECIMENTO]
    if pendentes:
        return []
    novas = [
        _nova(lead, act.user_id, step,
              em_dias(agora, DIAS_AQUECIMENTO * (i + 1)))
        for i, step in enumerate(_AQUECIMENTO)
    ]
    for n in novas:
        db.add(n)
    return novas


# ── Agendamento em lote ──────────────────────────────────────────────────────

def montar_lote(candidatos: Dict[int, List[Lead]], total: int,
                mix: Dict[int, int]) -> List[Lead]:
    """
    Escolhe o lote respeitando o mix (40/5/5). Função pura.

    Faltou lead num tier? A vaga vai para o tier seguinte com sobra, na ordem
    1 → 2 → 3: um lote de 50 com só 30 Tier 1 disponíveis não pode virar um
    lote de 40 — o vendedor reservou o dia para 50 ligações.
    """
    escolhidos: Dict[int, List[Lead]] = {t: [] for t in (1, 2, 3)}
    for t in (1, 2, 3):
        escolhidos[t] = candidatos.get(t, [])[: max(0, mix.get(t, 0))]
    falta = total - sum(len(v) for v in escolhidos.values())
    for t in (1, 2, 3):
        if falta <= 0:
            break
        sobra = candidatos.get(t, [])[len(escolhidos[t]):]
        extra = sobra[:falta]
        escolhidos[t].extend(extra)
        falta -= len(extra)
    return escolhidos[1] + escolhidos[2] + escolhidos[3]


def candidatos_do_lote(db: Session, user_id: str) -> Dict[int, List[Lead]]:
    """Base não executada, tier 1–3, prospectável, melhor nota primeiro."""
    from models.database import HIDDEN_LEAD_STATUSES, RELATIONSHIP_LEAD

    leads = (
        db.query(Lead)
        .filter(
            Lead.user_id == user_id,
            Lead.cadence.is_(None),
            Lead.tier.in_((1, 2, 3)),
            Lead.relationship == RELATIONSHIP_LEAD,
            Lead.status.notin_(HIDDEN_LEAD_STATUSES),
            Lead.stage.notin_(("ganho", "perdido")),
        )
        .order_by(Lead.tier.asc(), Lead.score.desc().nullslast(), Lead.created_at.asc())
        .all()
    )
    por_tier: Dict[int, List[Lead]] = {1: [], 2: [], 3: []}
    for lead in leads:
        por_tier[lead.tier].append(lead)
    return por_tier


def agendar_lote(db: Session, user_id: str, lote: Sequence[Lead], dia: date,
                 hora: time = time(9, 0)) -> List[Activity]:
    """Cria a tarefa de 1º contato de cada lead do lote e marca `agendado`."""
    due = _local(dia, hora)
    criadas = []
    for lead in lote:
        lead.cadence = AGENDADO
        lead.cadence_attempt = 1
        act = _nova(lead, user_id, "primeiro_contato", due)
        db.add(act)
        criadas.append(act)
        _documentar(db, lead, user_id, f"1º contato agendado para {dia.isoformat()}")
    return criadas


# ── Links de ação (agenda) ───────────────────────────────────────────────────

def _digitos(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    d = "".join(c for c in phone if c.isdigit())
    if not d:
        return None
    if len(d) in (10, 11):  # DDD + número, sem o 55
        d = "55" + d
    return d


def _email_de(dm: Optional[DecisionMaker]) -> Optional[str]:
    if not dm or not isinstance(dm.probable_emails, list):
        return None
    for e in dm.probable_emails:
        if isinstance(e, dict) and e.get("email") and e.get("status") != "invalid":
            return e["email"]
    return None


def acao_de(act: Activity, lead: Lead, dm: Optional[DecisionMaker], remetente: str = "") -> Dict:
    """Título, dica e os links prontos (tel:, wa.me, mailto:) da etapa."""
    meta = STEPS.get(act.step or "", {"titulo": act.notes or "Tarefa", "dica": ""})
    empresa = lead.company_name or lead.domain or ""
    primeiro_nome = (dm.name or "").split(" ")[0] if dm and dm.name else ""
    saud = f"Olá, {primeiro_nome}!" if primeiro_nome else "Olá!"

    telefone = (dm.phone if dm and dm.phone else None) or lead.phone
    digitos = _digitos(telefone)
    email = _email_de(dm) or lead.corporate_email

    if act.step == "aquec_whatsapp":
        texto = (f"{saud} Aqui é {remetente + ', da Skymail' if remetente else 'da Skymail'}. Passando para compartilhar "
                 f"como empresas como a {empresa} têm simplificado o e-mail corporativo. "
                 f"Posso te mandar um resumo?")
    else:
        texto = (f"{saud} Aqui é {remetente + ', da Skymail' if remetente else 'da Skymail'}. Tentei falar com você sobre "
                 f"o e-mail corporativo da {empresa}. Qual o melhor horário para uma conversa rápida?")

    assuntos = {
        "aquec_email_1": f"{empresa}: e-mail corporativo com mais controle",
        "aquec_email_2": f"Um conteúdo útil para a {empresa}",
        "aquec_email_3": f"15 minutos para falarmos da {empresa}?",
    }
    assunto = assuntos.get(act.step or "", f"Contato — {empresa}")
    corpo = f"{saud}\n\n{texto.split('!', 1)[-1].strip()}\n\nAbraço"

    return {
        "titulo": meta["titulo"],
        "dica": meta.get("dica", ""),
        "tel_url": f"tel:+{digitos}" if digitos else None,
        "whatsapp_url": f"https://wa.me/{digitos}?text={quote(texto)}" if digitos else None,
        "mailto_url": (f"mailto:{email}?subject={quote(assunto)}&body={quote(corpo)}"
                       if email else None),
    }
