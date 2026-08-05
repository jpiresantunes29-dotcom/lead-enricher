"""
Testes de cobrança — o caminho em que um erro custa dinheiro do cliente.

Cobre o webhook do Stripe (assinatura, idempotência, upgrade, downgrade,
reset de ciclo) e as travas do checkout. A assinatura é montada com o mesmo
esquema do Stripe (HMAC-SHA256 sobre "timestamp.payload"), então o teste
exercita a verificação de verdade, sem mockar `construct_event`.
"""
import hashlib
import hmac
import json
import time

import pytest

from tests.test_api import client, clean_db, _Session  # noqa: F401

from models.database import Profile, StripeEvent

SEGREDO = "whsec_teste_1234567890"


def _assinar(payload: bytes, segredo: str = SEGREDO) -> str:
    timestamp = str(int(time.time()))
    assinado = f"{timestamp}.".encode() + payload
    digest = hmac.new(segredo.encode(), assinado, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _evento(tipo: str, objeto: dict, event_id: str = "evt_1") -> bytes:
    """
    Envelope de evento do Stripe. O `"object": "event"` do topo não é
    decoração: `construct_event` lê esse campo para saber qual versão da API
    está recebendo, e um payload sem ele nem chega no nosso código.
    """
    return json.dumps({
        "id": event_id,
        "object": "event",
        "api_version": "2024-06-20",
        "type": tipo,
        "data": {"object": objeto},
    }).encode()


def _post_webhook(client, payload: bytes, assinatura: str | None = None):
    return client.post(
        "/api/billing/webhook",
        content=payload,
        headers={
            "stripe-signature": assinatura if assinatura is not None else _assinar(payload),
            "content-type": "application/json",
        },
    )


def _perfil(user_id: str = "test-user-123") -> Profile | None:
    db = _Session()
    try:
        return db.query(Profile).filter(Profile.id == user_id).first()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro_teste")


# ── Assinatura ───────────────────────────────────────────────────────────────

def test_webhook_recusa_assinatura_invalida(client):
    payload = _evento("checkout.session.completed", {})
    resp = _post_webhook(client, payload, assinatura="t=1,v1=deadbeef")
    assert resp.status_code == 400


def test_webhook_recusa_payload_que_nao_e_json(client):
    resp = _post_webhook(client, b"isto nao e json")
    assert resp.status_code == 400


def test_webhook_sem_segredo_configurado_nao_processa(client, monkeypatch):
    """Sem segredo, qualquer um postaria 'invoice.paid' e ganharia plano Pro."""
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    payload = _evento("checkout.session.completed", {})
    resp = _post_webhook(client, payload)
    assert resp.status_code == 503


# ── Upgrade ──────────────────────────────────────────────────────────────────

def test_pagamento_promove_o_usuario(client):
    client.get("/api/me")                       # cria o perfil no plano free
    payload = _evento("checkout.session.completed", {
        "metadata": {"user_id": "test-user-123", "plan": "pro"},
        "customer": "cus_123",
    })
    assert _post_webhook(client, payload).status_code == 200

    perfil = _perfil()
    assert perfil.plan == "pro"
    assert perfil.searches_limit == 500
    assert perfil.reveals_limit == 300
    assert perfil.stripe_customer_id == "cus_123"
    assert perfil.quota_reset_at is not None


def test_pagamento_antes_do_primeiro_login_cria_o_perfil(client):
    """
    O perfil nasce no primeiro /api/me. Se o webhook chegar antes, o cliente
    pagava e continuava no plano free — sem log e sem retentativa.
    """
    assert _perfil("usuario-novo") is None
    payload = _evento("checkout.session.completed", {
        "metadata": {"user_id": "usuario-novo", "plan": "pro"},
        "customer": "cus_novo",
    })
    assert _post_webhook(client, payload).status_code == 200

    perfil = _perfil("usuario-novo")
    assert perfil is not None
    assert perfil.plan == "pro"


def test_customer_id_e_gravado_mesmo_se_o_plano_ja_estava_certo(client):
    """Sem o customer_id, downgrade e reset mensal não acham o perfil depois."""
    client.get("/api/me")
    db = _Session()
    try:
        perfil = db.query(Profile).filter(Profile.id == "test-user-123").first()
        perfil.plan = "pro"
        db.commit()
    finally:
        db.close()

    payload = _evento("checkout.session.completed", {
        "metadata": {"user_id": "test-user-123", "plan": "pro"},
        "customer": "cus_tardio",
    })
    _post_webhook(client, payload)
    assert _perfil().stripe_customer_id == "cus_tardio"


def test_evento_repetido_nao_e_processado_duas_vezes(client):
    client.get("/api/me")
    payload = _evento("checkout.session.completed", {
        "metadata": {"user_id": "test-user-123", "plan": "pro"},
        "customer": "cus_123",
    }, event_id="evt_repetido")

    _post_webhook(client, payload)
    db = _Session()
    try:
        perfil = db.query(Profile).filter(Profile.id == "test-user-123").first()
        perfil.searches_used = 40          # usuário consumiu parte da cota
        db.commit()
    finally:
        db.close()

    _post_webhook(client, payload)         # mesmo evento chegando de novo
    assert _perfil().searches_used == 40, "reprocessar zerou a cota do cliente"

    db = _Session()
    try:
        assert db.query(StripeEvent).filter(StripeEvent.id == "evt_repetido").count() == 1
    finally:
        db.close()


# ── Downgrade e ciclo ────────────────────────────────────────────────────────

def test_cancelamento_rebaixa_para_free(client):
    client.get("/api/me")
    _post_webhook(client, _evento("checkout.session.completed", {
        "metadata": {"user_id": "test-user-123", "plan": "pro"}, "customer": "cus_x",
    }, event_id="evt_up"))

    resp = _post_webhook(client, _evento("customer.subscription.deleted", {
        "customer": "cus_x",
    }, event_id="evt_down"))
    assert resp.status_code == 200

    perfil = _perfil()
    assert perfil.plan == "free"
    assert perfil.searches_limit == 5
    assert perfil.reveals_limit == 5


def test_fatura_paga_renova_a_cota_do_ciclo(client):
    client.get("/api/me")
    _post_webhook(client, _evento("checkout.session.completed", {
        "metadata": {"user_id": "test-user-123", "plan": "pro"}, "customer": "cus_y",
    }, event_id="evt_up2"))

    db = _Session()
    try:
        perfil = db.query(Profile).filter(Profile.id == "test-user-123").first()
        perfil.searches_used, perfil.reveals_used = 480, 250
        db.commit()
    finally:
        db.close()

    _post_webhook(client, _evento("invoice.paid", {"customer": "cus_y"}, event_id="evt_inv"))

    perfil = _perfil()
    assert perfil.searches_used == 0
    assert perfil.reveals_used == 0


# ── Checkout ─────────────────────────────────────────────────────────────────

def test_checkout_recusa_plano_invalido(client):
    resp = client.post("/api/billing/checkout", json={"plan": "plano-que-nao-existe"})
    assert resp.status_code == 400


def test_portal_sem_assinatura_responde_400(client):
    client.get("/api/me")
    assert client.post("/api/billing/portal").status_code == 400
