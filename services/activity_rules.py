"""
Regras automáticas da camada de execução comercial
(docs/PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md §5.2) — o "Power Automate" em Python.

apply_rules() roda ao registrar uma atividade e devolve as atividades
derivadas (follow-ups, reuniões) + mudanças de estágio. Tudo na mesma
transação do chamador — nenhum commit aqui.
"""
from datetime import datetime, timedelta, UTC
from typing import List, Optional

from models.database import Activity, Lead

PIPELINE_STAGES = ("novo", "contatado", "reuniao_agendada", "oportunidade", "ganho", "perdido")

CALL_OUTCOMES = ("no_answer", "busy", "voicemail", "talked", "meeting_scheduled")
ACTIVITY_TYPES = ("call", "email", "meeting", "note", "task")

_FOLLOW_UP_NOTES = {
    "no_answer": "Refazer contato — não atendeu.",
    "voicemail": "Refazer contato — caiu na caixa postal.",
    "busy": "Refazer contato — ocupado.",
}


def add_business_days(start: datetime, days: int) -> datetime:
    """Soma dias úteis (seg–sex), preservando o horário."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:  # 0–4 = seg–sex
            remaining -= 1
    return current


def apply_rules(
    lead: Lead,
    activity: Activity,
    meeting_at: Optional[datetime] = None,
) -> List[Activity]:
    """
    Aplica as regras automáticas para uma atividade recém-registrada.
    Retorna a lista de atividades derivadas criadas (para o chamador dar add).
    """
    derived: List[Activity] = []
    now = datetime.now(UTC)

    if activity.type != "call" or not activity.outcome:
        return derived

    outcome = activity.outcome

    if outcome == "meeting_scheduled":
        lead.stage = "reuniao_agendada"
        derived.append(Activity(
            lead_id=lead.id,
            user_id=activity.user_id,
            type="meeting",
            notes=f"Reunião com {lead.company_name or lead.domain}",
            due_at=meeting_at or add_business_days(now, 1),
        ))

    elif outcome == "talked":
        if lead.stage == "novo":
            lead.stage = "contatado"

    elif outcome in ("no_answer", "voicemail"):
        if lead.stage == "novo":
            lead.stage = "contatado"
        derived.append(Activity(
            lead_id=lead.id,
            user_id=activity.user_id,
            type="task",
            notes=_FOLLOW_UP_NOTES[outcome],
            due_at=add_business_days(now, 2),
        ))

    elif outcome == "busy":
        if lead.stage == "novo":
            lead.stage = "contatado"
        derived.append(Activity(
            lead_id=lead.id,
            user_id=activity.user_id,
            type="task",
            notes=_FOLLOW_UP_NOTES["busy"],
            due_at=add_business_days(now, 1),
        ))

    return derived


def build_ics(activity: Activity, lead: Lead) -> str:
    """
    Gera um convite .ics (VCALENDAR) para uma atividade com due_at.
    Funciona com Outlook, Google e Apple Calendar — sem dependência externa.
    """
    start = activity.due_at or datetime.now(UTC)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    end = start + timedelta(minutes=30)
    stamp = datetime.now(UTC)

    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y%m%dT%H%M%SZ")

    company = lead.company_name or lead.domain or "lead"
    kind = "Reunião" if activity.type == "meeting" else "Follow-up"
    summary = f"{kind} — {company}"
    description = (activity.notes or "").replace("\n", "\\n")

    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LeadEnricher//Prospeccao//PT-BR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:leadenricher-activity-{activity.id}@leadenricher",
        f"DTSTAMP:{fmt(stamp)}",
        f"DTSTART:{fmt(start)}",
        f"DTEND:{fmt(end)}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])
