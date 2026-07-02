"""
Testes das fases 4–5: webhook CRM assinado, insights de IA e provedor Hunter.
Tudo mockado — nenhuma chamada externa real.
"""
import json
import hashlib
import hmac
import os
from unittest.mock import patch, MagicMock

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401
from models.database import Profile


def _make_lead(client):
    with patch("routers.enrichment.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})
    return resp.json()["data"]


def _make_pro_profile():
    db = _Session()
    profile = db.query(Profile).filter(Profile.id == "test-user-123").first()
    profile.plan = "pro"
    db.commit()
    db.close()


# ── status de integrações ─────────────────────────────────────────────────────
def test_integrations_status_unconfigured(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CRM_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    resp = client.get("/api/integrations/status")
    assert resp.status_code == 200
    assert resp.json() == {"ai": False, "crm_webhook": False, "hunter": False}


# ── webhook CRM ───────────────────────────────────────────────────────────────
def test_push_unconfigured_returns_503(client, monkeypatch):
    monkeypatch.delenv("CRM_WEBHOOK_URL", raising=False)
    lead = _make_lead(client)
    resp = client.post(f"/api/leads/{lead['id']}/push")
    assert resp.status_code == 503


def test_push_sends_signed_payload(client, monkeypatch):
    monkeypatch.setenv("CRM_WEBHOOK_URL", "https://hooks.example.com/in")
    monkeypatch.setenv("CRM_WEBHOOK_SECRET", "s3gr3do")
    lead = _make_lead(client)

    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, data=data, headers=headers)
        m = MagicMock()
        m.status_code = 200
        return m

    with patch("services.crm.webhook.requests.post", side_effect=fake_post):
        resp = client.post(f"/api/leads/{lead['id']}/push")

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert captured["url"] == "https://hooks.example.com/in"

    # Assinatura HMAC válida sobre o corpo exato
    expected = hmac.new(b"s3gr3do", captured["data"], hashlib.sha256).hexdigest()
    assert captured["headers"]["X-LeadEnricher-Signature"] == f"sha256={expected}"

    # Payload contém o lead com score
    payload = json.loads(captured["data"])
    assert payload["event"] == "lead.push"
    assert payload["lead"]["company_name"] == "Nubank"
    assert "score" in payload["lead"]

    # Auditoria: nota criada na timeline
    timeline = client.get(f"/api/leads/{lead['id']}/activities").json()
    assert any("CRM" in (a["notes"] or "") for a in timeline)


def test_push_upstream_failure_returns_502(client, monkeypatch):
    monkeypatch.setenv("CRM_WEBHOOK_URL", "https://hooks.example.com/in")
    lead = _make_lead(client)
    fail = MagicMock()
    fail.status_code = 500
    with patch("services.crm.webhook.requests.post", return_value=fail):
        resp = client.post(f"/api/leads/{lead['id']}/push")
    assert resp.status_code == 502


# ── IA ────────────────────────────────────────────────────────────────────────
def test_ai_summary_unconfigured_returns_503(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lead = _make_lead(client)
    resp = client.post(f"/api/leads/{lead['id']}/ai-summary")
    assert resp.status_code == 503


def test_ai_summary_free_plan_gated(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("AI_ALLOW_FREE", raising=False)
    lead = _make_lead(client)
    resp = client.post(f"/api/leads/{lead['id']}/ai-summary")
    assert resp.status_code == 402


def test_ai_summary_generates_and_caches(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    lead = _make_lead(client)
    _make_pro_profile()

    with patch("services.ai_insights._call_claude", return_value="Resumo executivo da Nubank.") as mock_ai:
        r1 = client.post(f"/api/leads/{lead['id']}/ai-summary")
        r2 = client.post(f"/api/leads/{lead['id']}/ai-summary")

    assert r1.status_code == 200
    assert r1.json() == {"summary": "Resumo executivo da Nubank.", "cached": False}
    assert r2.json()["cached"] is True
    assert mock_ai.call_count == 1  # segunda chamada veio do cache

    # O resumo cacheado aparece no LeadOut
    lead_out = client.get(f"/api/leads/{lead['id']}").json()
    assert lead_out["ai_summary"] == "Resumo executivo da Nubank."


def test_call_script(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    lead = _make_lead(client)
    _make_pro_profile()
    with patch("services.ai_insights._call_claude", return_value="Roteiro: abertura..."):
        resp = client.post(f"/api/leads/{lead['id']}/call-script", json={"product": "ERP"})
    assert resp.status_code == 200
    assert "Roteiro" in resp.json()["script"]


# ── provedor Hunter ───────────────────────────────────────────────────────────
def test_hunter_status_mapping(monkeypatch):
    from services.providers import hunter

    monkeypatch.setenv("HUNTER_API_KEY", "hk-test")
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"data": {"status": "accept_all"}}
    with patch("services.providers.hunter.requests.get", return_value=fake):
        assert hunter.verify_email("x@acme.com") == "catch_all"

    fake.json.return_value = {"data": {"status": "valid"}}
    with patch("services.providers.hunter.requests.get", return_value=fake):
        assert hunter.verify_email("x@acme.com") == "valid"


def test_premium_verifier_fallback_when_unconfigured(monkeypatch):
    from services.providers import premium_verify_email

    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    assert premium_verify_email("x@acme.com") is None
