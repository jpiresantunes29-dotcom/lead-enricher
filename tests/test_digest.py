"""
Resumo diário por e-mail (services/digest.py e POST /api/internal/digest).

O que se protege aqui: quem não tem e-mail conhecido ou desligou a
preferência nunca recebe nada, e quem não teve nenhuma atividade no dia
também não — o resumo é para chamar atenção, não para virar spam diário.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401

from models.database import Conversation, Lead, Profile, WaMessage, utcnow
from services import digest

SEGREDO = "segredo-do-cron-para-teste"


@pytest.fixture(autouse=True)
def cron_env(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", SEGREDO)


def _auth():
    return {"Authorization": f"Bearer {SEGREDO}"}


def _perfil(user_id="user-1", email="dono@empresa.com", digest_diario=True) -> Profile:
    db = _Session()
    try:
        p = Profile(id=user_id, email=email, digest_diario=digest_diario)
        db.add(p)
        db.commit()
        return p
    finally:
        db.close()


def _lead(user_id="user-1", horas_atras=1) -> Lead:
    db = _Session()
    try:
        lead = Lead(
            user_id=user_id, raw_input_domain="acme.com", domain="acme.com",
            status="enriched", created_at=utcnow() - timedelta(hours=horas_atras),
        )
        db.add(lead)
        db.commit()
        return lead
    finally:
        db.close()


# ── services/digest.py ───────────────────────────────────────────────────────

def test_usuario_sem_atividade_nao_recebe_nada(clean_db):
    _perfil()
    db = _Session()
    try:
        with patch("services.mailer.send") as mock_send:
            resumo = digest.enviar_para_todos(db)
    finally:
        db.close()
    mock_send.assert_not_called()
    assert resumo == {"total_usuarios": 1, "enviados": 0, "sem_atividade": 1, "falhas": 0}


def test_usuario_com_lead_novo_recebe_o_resumo(clean_db):
    _perfil(email="dono@empresa.com")
    _lead(horas_atras=1)
    db = _Session()
    try:
        with patch("services.mailer.send", return_value=True) as mock_send:
            resumo = digest.enviar_para_todos(db)
    finally:
        db.close()

    assert resumo["enviados"] == 1
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == "dono@empresa.com"
    assert "1 novo(s) lead(s)" in kwargs["text"]


def test_lead_de_mais_de_24h_nao_entra_no_resumo(clean_db):
    _perfil()
    _lead(horas_atras=30)
    db = _Session()
    try:
        with patch("services.mailer.send") as mock_send:
            resumo = digest.enviar_para_todos(db)
    finally:
        db.close()
    mock_send.assert_not_called()
    assert resumo["sem_atividade"] == 1


def test_usuario_sem_email_e_ignorado(clean_db):
    _perfil(email=None)
    _lead()
    db = _Session()
    try:
        with patch("services.mailer.send") as mock_send:
            resumo = digest.enviar_para_todos(db)
    finally:
        db.close()
    mock_send.assert_not_called()
    assert resumo["total_usuarios"] == 0


def test_usuario_com_digest_desligado_e_ignorado(clean_db):
    _perfil(digest_diario=False)
    _lead()
    db = _Session()
    try:
        with patch("services.mailer.send") as mock_send:
            resumo = digest.enviar_para_todos(db)
    finally:
        db.close()
    mock_send.assert_not_called()
    assert resumo["total_usuarios"] == 0


def test_conversa_pendente_entra_no_assunto_e_no_texto(clean_db):
    from models.database import HUMAN_HANDOFF

    _perfil()
    db = _Session()
    try:
        lead = Lead(user_id="user-1", raw_input_domain="acme.com", domain="acme.com",
                   status="enriched")
        db.add(lead)
        db.commit()
        conversa = Conversation(
            user_id="user-1", lead_id=lead.id, phone_e164="+5511999999999",
            channel="whatsapp", ai_status=HUMAN_HANDOFF,
            last_inbound_at=utcnow(), last_outbound_at=utcnow() - timedelta(hours=2),
        )
        db.add(conversa)
        db.commit()
    finally:
        db.close()

    db = _Session()
    try:
        with patch("services.mailer.send", return_value=True) as mock_send:
            digest.enviar_para_todos(db)
    finally:
        db.close()

    kwargs = mock_send.call_args.kwargs
    assert "esperando você" in kwargs["subject"]
    assert "esperando sua resposta" in kwargs["text"]


def test_falha_no_envio_e_contabilizada(clean_db):
    _perfil()
    _lead()
    db = _Session()
    try:
        with patch("services.mailer.send", return_value=False):
            resumo = digest.enviar_para_todos(db)
    finally:
        db.close()
    assert resumo == {"total_usuarios": 1, "enviados": 0, "sem_atividade": 0, "falhas": 1}


# ── POST /api/internal/jobs/run (inclui digest) ────────────────────────────

def test_jobs_run_chama_o_digest(client):
    """O digest roda junto com a fila de jobs (consolidado para Vercel grátis)."""
    _perfil()
    _lead()
    with patch("services.mailer.send", return_value=True):
        with patch("services.enrichment_service.enrich_company", side_effect=Exception("não deve rodar")):
            resp = client.post("/api/internal/jobs/run", headers=_auth())
    assert resp.status_code == 200
    dados = resp.json()
    assert dados["digest"]["enviados"] == 1


# ── PATCH /api/me (liga/desliga o digest) ────────────────────────────────────

def test_desliga_o_digest(client):
    resp = client.patch("/api/me", json={"digest_diario": False})
    assert resp.status_code == 200
    assert resp.json() == {"digest_diario": False}
    assert client.get("/api/me").json()["digest_diario"] is False


def test_email_e_gravado_no_primeiro_login(client):
    client.get("/api/me")
    db = _Session()
    try:
        perfil = db.query(Profile).filter(Profile.id == "test-user-123").first()
        assert perfil.email == "test@example.com"
        assert perfil.digest_diario is True
    finally:
        db.close()
