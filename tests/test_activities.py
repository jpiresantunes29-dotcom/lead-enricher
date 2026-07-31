"""
Testes da camada de execução comercial: atividades, regras automáticas,
follow-ups, .ics, pipeline e dashboard.
Reusa a infraestrutura de banco em memória de test_api.py.
"""
from datetime import datetime, timedelta, UTC

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401
from unittest.mock import patch

from models.database import Lead, Activity
from services.activity_rules import add_business_days


def _make_lead(client):
    with patch("routers.enrichment.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})
    return resp.json()["data"]


# ── regras de negócio puras ────────────────────────────────────────────────────
def test_add_business_days_skips_weekend():
    friday = datetime(2026, 6, 12, 10, 0)  # sexta-feira
    result = add_business_days(friday, 2)
    assert result.weekday() == 1  # terça
    assert result.day == 16


# ── scoring integrado ao enrich ────────────────────────────────────────────────
def test_enrich_computes_score(client):
    lead = _make_lead(client)
    # MOCK: Google MX não bate "Google Workspace", mas linkedin verified (+5)
    assert lead["score"] is not None
    assert lead["priority"] in ("alta", "media", "baixa")
    assert isinstance(lead["score_breakdown"], list)
    assert lead["stage"] == "novo"


def test_rescore_endpoint(client):
    lead = _make_lead(client)
    resp = client.post(f"/api/leads/{lead['id']}/rescore")
    assert resp.status_code == 200
    assert resp.json()["score"] == lead["score"]


# ── atividades e regras automáticas ───────────────────────────────────────────
def test_call_no_answer_creates_followup(client):
    lead = _make_lead(client)
    resp = client.post(
        f"/api/leads/{lead['id']}/activities",
        json={"type": "call", "outcome": "no_answer"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["lead_stage"] == "contatado"
    assert len(data["derived"]) == 1
    follow_up = data["derived"][0]
    assert follow_up["type"] == "task"
    assert follow_up["due_at"] is not None


def test_call_meeting_scheduled_moves_stage_and_creates_meeting(client):
    lead = _make_lead(client)
    meeting_at = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    resp = client.post(
        f"/api/leads/{lead['id']}/activities",
        json={"type": "call", "outcome": "meeting_scheduled", "meeting_at": meeting_at},
    )
    data = resp.json()
    assert data["lead_stage"] == "reuniao_agendada"
    assert data["derived"][0]["type"] == "meeting"


def test_invalid_outcome_rejected(client):
    lead = _make_lead(client)
    resp = client.post(
        f"/api/leads/{lead['id']}/activities",
        json={"type": "call", "outcome": "alien_invasion"},
    )
    assert resp.status_code == 422


def test_activity_timeline_and_pending(client):
    lead = _make_lead(client)
    client.post(f"/api/leads/{lead['id']}/activities", json={"type": "call", "outcome": "no_answer"})
    client.post(f"/api/leads/{lead['id']}/activities", json={"type": "note", "notes": "ótimo fit"})

    timeline = client.get(f"/api/leads/{lead['id']}/activities").json()
    assert len(timeline) == 3  # call + follow-up derivado + note

    pending = client.get("/api/activities/pending").json()
    assert len(pending) == 1
    assert pending[0]["type"] == "task"


def test_complete_activity(client):
    lead = _make_lead(client)
    client.post(f"/api/leads/{lead['id']}/activities", json={"type": "call", "outcome": "busy"})
    pending = client.get("/api/activities/pending").json()
    act_id = pending[0]["id"]

    resp = client.patch(f"/api/activities/{act_id}", json={"completed": True})
    assert resp.status_code == 200
    assert resp.json()["completed_at"] is not None
    assert client.get("/api/activities/pending").json() == []


def test_ics_download(client):
    lead = _make_lead(client)
    meeting_at = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    resp = client.post(
        f"/api/leads/{lead['id']}/activities",
        json={"type": "call", "outcome": "meeting_scheduled", "meeting_at": meeting_at},
    )
    meeting_id = resp.json()["derived"][0]["id"]

    ics = client.get(f"/api/activities/{meeting_id}/ics")
    assert ics.status_code == 200
    assert "text/calendar" in ics.headers["content-type"]
    assert "BEGIN:VEVENT" in ics.text
    assert "Reunião — Nubank" in ics.text


# ── pipeline ──────────────────────────────────────────────────────────────────
def test_stage_patch(client):
    lead = _make_lead(client)
    resp = client.patch(f"/api/leads/{lead['id']}/stage", json={"stage": "oportunidade"})
    assert resp.status_code == 200
    assert resp.json()["stage"] == "oportunidade"


def test_stage_patch_invalid(client):
    lead = _make_lead(client)
    resp = client.patch(f"/api/leads/{lead['id']}/stage", json={"stage": "tartaruga"})
    assert resp.status_code == 422


# ── dashboard ─────────────────────────────────────────────────────────────────
def test_dashboard_metrics(client):
    lead = _make_lead(client)
    client.post(f"/api/leads/{lead['id']}/activities", json={"type": "call", "outcome": "no_answer"})
    client.post(f"/api/leads/{lead['id']}/activities", json={"type": "call", "outcome": "talked"})
    client.post(f"/api/leads/{lead['id']}/activities", json={"type": "call", "outcome": "meeting_scheduled"})

    m = client.get("/api/dashboard/metrics").json()
    assert m["leads_pesquisados"] == 1
    assert m["ligacoes_realizadas"] == 3
    assert m["taxa_contato"] == round(2 / 3, 3)
    assert m["taxa_reuniao"] == round(1 / 3, 3)
    assert m["funil_por_estagio"].get("reuniao_agendada") == 1
    # 2 pendências: follow-up do no_answer + reunião do meeting_scheduled
    assert m["followups_pendentes"] == 2


# ── fila do dia (/api/followups/today) ────────────────────────────────────────
def test_today_followups_lists_tasks_due_today(client):
    """Regressão: o endpoint filtrava type='followup', que nunca é gerado —
    as regras criam 'task' (follow-up) e 'meeting' (reunião)."""
    lead = _make_lead(client)
    client.post(
        f"/api/leads/{lead['id']}/activities",
        json={"type": "call", "outcome": "meeting_scheduled",
              "meeting_at": datetime.now(UTC).isoformat()},
    )
    resp = client.get("/api/followups/today")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["lead_id"] == lead["id"]
    assert items[0]["domain"] == "nubank.com.br"


def test_today_followups_ignores_other_days(client):
    lead = _make_lead(client)
    client.post(
        f"/api/leads/{lead['id']}/activities",
        json={"type": "task", "notes": "ligar depois",
              "due_at": (datetime.now(UTC) + timedelta(days=5)).isoformat()},
    )
    assert client.get("/api/followups/today").json() == []
