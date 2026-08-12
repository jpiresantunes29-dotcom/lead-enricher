"""
Rotas de WhatsApp: webhook da Meta e abertura de conversa.

Nada aqui toca a rede — o cliente da Meta é mockado. O que se testa é o que
acontece **antes** e **depois** do envio: quem pode disparar, quem é barrado,
e o que o sistema faz com o que chega de volta.

Os dois riscos concretos que estes testes guardam:
  - responder duas vezes ao lead porque a Meta reentregou a mesma mensagem;
  - gastar um template pago com quem não devia receber.
"""
import json
from datetime import timedelta
from itertools import count
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session  # noqa: F401

from models.database import (
    AI_ACTIVE, Activity, Conversation, DecisionMaker, Lead, WaMessage,
    RELATIONSHIP_CUSTOMER, utcnow,
)
from services.people import optout
from services.wa import client as wa_client, gate, webhook

TELEFONE = "+5511988887777"
META_FROM = "5511988887777"   # como a Meta manda: sem o "+"
SEGREDO = "segredo-de-teste"


@pytest.fixture(autouse=True)
def whatsapp_configurado(monkeypatch):
    """
    Servidor configurado e dentro do horário comercial.

    O horário é fixado de propósito: as rotas chamam `utcnow()` por dentro, e
    sem isto a suíte passaria de manhã e falharia às onze da noite, quando o
    portão entra em silêncio noturno. As três faixas de horário têm testes
    próprios em `test_wa_portao.py`, com instantes explícitos.
    """
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SEGREDO)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "abre-te-sesamo")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token-de-teste")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "primeiro_contato")
    monkeypatch.setattr(gate, "service_window", lambda agora=None: (True, False))


@pytest.fixture(autouse=True)
def ia_desligada(monkeypatch):
    """
    Sem IA, por padrão.

    O turno automático tem arquivo próprio (`test_wa_turno.py`). Aqui o que se
    testa é o recebimento e a abertura; deixar a automação ligada faria cada
    mensagem recebida disparar um turno e embaralhar as contagens.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _lead(db=None, phone=TELEFONE, relationship="LEAD", user_id="test-user-123"):
    sessao = db or _Session()
    try:
        lead = Lead(user_id=user_id, raw_input_domain="acme.com.br",
                    domain="acme.com.br", company_name="Acme", status="enriched",
                    phone=phone, relationship=relationship)
        sessao.add(lead)
        sessao.commit()
        return lead.id
    finally:
        if db is None:
            sessao.close()


def _envio_ok(wamid="wamid.SAIDA1"):
    return wa_client.SendResult(True, wa_message_id=wamid)


def _assinar(corpo: bytes) -> dict:
    return {webhook.SIGNATURE_HEADER: webhook.expected_signature(corpo, SEGREDO)}


def _evento_mensagem(texto="oi, quem fala?", wamid="wamid.ENTRADA1",
                     de=META_FROM, tipo="text"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "1234567890"},
            "messages": [{
                "from": de, "id": wamid, "timestamp": "1770000000",
                "type": tipo, "text": {"body": texto},
            }],
        }}]}],
    }


def _postar_webhook(client, evento):
    corpo = json.dumps(evento).encode("utf-8")
    return client.post("/api/wa/webhook", content=corpo, headers=_assinar(corpo))


_convites = count(1)


def _abrir_conversa(client, lead_id, **extra):
    """
    Abre uma conversa com a Meta mockada.

    Cada convite recebe um `wa_message_id` diferente porque é assim que a Meta
    se comporta — e porque o índice único da tabela existe justamente para
    barrar ids repetidos. Um mock que devolve sempre o mesmo id faz o teste
    falhar por um motivo que não existe em produção.
    """
    wamid = f"wamid.SAIDA{next(_convites)}"
    with patch("services.wa.client.send_template", return_value=_envio_ok(wamid)):
        return client.post("/api/wa/start", json={"lead_id": lead_id, **extra})


# ── Handshake de cadastro ────────────────────────────────────────────────────

def test_handshake_devolve_o_desafio_quando_o_token_bate(client):
    resp = client.get("/api/wa/webhook", params={
        "hub.mode": "subscribe", "hub.challenge": "12345",
        "hub.verify_token": "abre-te-sesamo",
    })
    assert resp.status_code == 200
    assert resp.text == "12345"


def test_handshake_com_token_errado_e_recusado(client):
    resp = client.get("/api/wa/webhook", params={
        "hub.mode": "subscribe", "hub.challenge": "12345",
        "hub.verify_token": "chute",
    })
    assert resp.status_code == 403


# ── Assinatura: o webhook é público, a assinatura é a única identidade ───────

def test_webhook_sem_assinatura_e_recusado(client):
    resp = client.post("/api/wa/webhook", json=_evento_mensagem())
    assert resp.status_code == 403


def test_webhook_com_assinatura_de_outro_corpo_e_recusado(client):
    corpo = json.dumps(_evento_mensagem()).encode("utf-8")
    outra = webhook.expected_signature(b'{"forjado":true}', SEGREDO)
    resp = client.post("/api/wa/webhook", content=corpo,
                       headers={webhook.SIGNATURE_HEADER: outra})
    assert resp.status_code == 403


def test_mensagem_forjada_nao_entra_no_banco(client):
    """A recusa precisa acontecer antes de qualquer escrita."""
    lead_id = _lead()
    _abrir_conversa(client, lead_id)

    client.post("/api/wa/webhook", json=_evento_mensagem())

    db = _Session()
    try:
        assert db.query(WaMessage).filter(WaMessage.direction == "in").count() == 0
    finally:
        db.close()


# ── Recebimento ──────────────────────────────────────────────────────────────

def test_mensagem_recebida_e_guardada_e_reabre_a_janela(client):
    lead_id = _lead()
    _abrir_conversa(client, lead_id)

    resp = _postar_webhook(client, _evento_mensagem())
    assert resp.status_code == 200
    assert resp.json()["mensagens"] == 1

    db = _Session()
    try:
        msg = db.query(WaMessage).filter(WaMessage.direction == "in").one()
        assert msg.body == "oi, quem fala?"
        conversa = db.query(Conversation).filter(Conversation.lead_id == lead_id).one()
        assert conversa.window_expires_at is not None
        assert conversa.last_message_body == "oi, quem fala?"
    finally:
        db.close()


def test_mesma_mensagem_reentregue_nao_duplica(client):
    """
    A Meta reentrega o que não recebeu 200 a tempo. Sem idempotência, uma
    lentidão nossa vira mensagem repetida na conversa — e, na Fase 5, uma
    resposta repetida no celular do lead.
    """
    lead_id = _lead()
    _abrir_conversa(client, lead_id)

    primeira = _postar_webhook(client, _evento_mensagem(wamid="wamid.REPETIDA"))
    segunda = _postar_webhook(client, _evento_mensagem(wamid="wamid.REPETIDA"))

    assert primeira.json()["mensagens"] == 1
    assert segunda.json()["mensagens"] == 0

    db = _Session()
    try:
        assert db.query(WaMessage).filter(WaMessage.direction == "in").count() == 1
    finally:
        db.close()


def test_mensagem_de_numero_sem_conversa_e_ignorada(client):
    """Não inventamos lead para quem escreveu sem ter sido convidado."""
    resp = _postar_webhook(client, _evento_mensagem(de="5511900000000"))
    assert resp.status_code == 200
    assert resp.json()["mensagens"] == 0


def test_numero_da_meta_casa_com_o_numero_guardado(client):
    """A Meta manda sem `+`; o banco guarda com. Os dois têm que se encontrar."""
    lead_id = _lead()
    _abrir_conversa(client, lead_id)
    resp = _postar_webhook(client, _evento_mensagem(de="5511988887777"))
    assert resp.json()["mensagens"] == 1


def test_confirmacao_de_entrega_atualiza_a_mensagem_enviada(client):
    lead_id = _lead()
    _abrir_conversa(client, lead_id)

    db = _Session()
    try:
        wamid = db.query(WaMessage).filter(WaMessage.direction == "out").one().wa_message_id
    finally:
        db.close()

    evento = {"entry": [{"changes": [{"value": {"statuses": [
        {"id": wamid, "status": "read", "timestamp": "1770000000"}
    ]}}]}]}
    _postar_webhook(client, evento)

    db = _Session()
    try:
        msg = db.query(WaMessage).filter(WaMessage.wa_message_id == wamid).one()
        assert msg.status == "read"
    finally:
        db.close()


def test_corpo_que_nao_e_json_nao_derruba_o_webhook(client):
    corpo = b"isto nao e json"
    resp = client.post("/api/wa/webhook", content=corpo, headers=_assinar(corpo))
    assert resp.status_code == 200


def test_mensagem_de_audio_e_registrada_com_o_tipo(client):
    lead_id = _lead()
    _abrir_conversa(client, lead_id)
    evento = _evento_mensagem(tipo="audio")
    evento["entry"][0]["changes"][0]["value"]["messages"][0].pop("text")
    _postar_webhook(client, evento)

    db = _Session()
    try:
        msg = db.query(WaMessage).filter(WaMessage.direction == "in").one()
        assert msg.body == "[audio]"
        assert msg.type == "audio"
    finally:
        db.close()


# ── Abertura: a ação paga ────────────────────────────────────────────────────

def test_abrir_conversa_envia_template_e_registra_tudo(client):
    lead_id = _lead()
    with patch("services.wa.client.send_template", return_value=_envio_ok()) as envio:
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})

    assert resp.status_code == 200
    assert envio.call_count == 1
    assert envio.call_args[0][0] == TELEFONE

    corpo = resp.json()
    assert corpo["conversation"]["ai_status"] == AI_ACTIVE
    assert corpo["conversation"]["phone_e164"] == TELEFONE

    db = _Session()
    try:
        msg = db.query(WaMessage).filter(WaMessage.direction == "out").one()
        assert msg.type == "template"
        assert msg.sent_by == "human"
        # Timeline única: o convite aparece junto das ligações e das notas.
        assert db.query(Activity).filter(Activity.lead_id == lead_id).count() == 1
    finally:
        db.close()


def test_cliente_atual_nao_recebe_convite(client):
    """O caso que o portão existe para impedir, agora pela porta da frente."""
    lead_id = _lead(relationship=RELATIONSHIP_CUSTOMER)
    with patch("services.wa.client.send_template", return_value=_envio_ok()) as envio:
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})

    assert resp.status_code == 409
    assert envio.call_count == 0


def test_numero_com_opt_out_nao_recebe_convite(client):
    lead_id = _lead()
    db = _Session()
    try:
        optout.register(db, "phone", TELEFONE, source="teste")
        db.commit()
    finally:
        db.close()

    with patch("services.wa.client.send_template", return_value=_envio_ok()) as envio:
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})

    assert resp.status_code == 409
    assert envio.call_count == 0


def test_convite_nao_e_reenviado_enquanto_nao_ha_resposta(client):
    """Reenviar cobra de novo e queima a reputação do número."""
    lead_id = _lead()
    primeira = _abrir_conversa(client, lead_id)
    assert primeira.status_code == 200

    with patch("services.wa.client.send_template", return_value=_envio_ok()) as envio:
        segunda = client.post("/api/wa/start", json={"lead_id": lead_id})

    assert segunda.status_code == 409
    assert envio.call_count == 0

    db = _Session()
    try:
        assert db.query(WaMessage).filter(WaMessage.direction == "out").count() == 1
    finally:
        db.close()


def test_conversa_ja_aberta_nao_gasta_template(client):
    """Se o lead respondeu, a janela está aberta e a resposta é de graça."""
    lead_id = _lead()
    _abrir_conversa(client, lead_id)
    _postar_webhook(client, _evento_mensagem())

    with patch("services.wa.client.send_template", return_value=_envio_ok()) as envio:
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})

    assert resp.status_code == 409
    assert "aberta" in resp.json()["detail"].lower()
    assert envio.call_count == 0


def test_lead_sem_telefone_e_recusado_com_recado_util(client):
    lead_id = _lead(phone=None)
    resp = client.post("/api/wa/start", json={"lead_id": lead_id})
    assert resp.status_code == 422
    assert "celular do decisor" in resp.json()["detail"].lower()


def test_lead_de_outro_usuario_nao_pode_ser_contatado(client):
    lead_id = _lead(user_id="outro-usuario")
    with patch("services.wa.client.send_template", return_value=_envio_ok()) as envio:
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})
    assert resp.status_code == 404
    assert envio.call_count == 0


def test_celular_do_decisor_tem_prioridade_sobre_o_da_empresa(client):
    """O da empresa é a central; o do decisor é com quem se quer falar."""
    lead_id = _lead(phone="+551140028922")
    db = _Session()
    try:
        decisor = DecisionMaker(lead_id=lead_id, name="Ana", phone=TELEFONE)
        db.add(decisor)
        db.commit()
        decisor_id = decisor.id
    finally:
        db.close()

    with patch("services.wa.client.send_template", return_value=_envio_ok()) as envio:
        resp = client.post("/api/wa/start",
                           json={"lead_id": lead_id, "decision_maker_id": decisor_id})

    assert resp.status_code == 200
    assert envio.call_args[0][0] == TELEFONE


def test_falha_da_meta_nao_deixa_conversa_fantasma(client):
    """Sem mensagem enviada não há conversa aberta: o estado segue o fato."""
    lead_id = _lead()
    recusa = wa_client.SendResult(False, error="131047: fora da janela")
    with patch("services.wa.client.send_template", return_value=recusa):
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})

    assert resp.status_code == 502
    db = _Session()
    try:
        assert db.query(Conversation).count() == 0
        assert db.query(WaMessage).count() == 0
    finally:
        db.close()


def test_sem_credencial_da_meta_a_rota_explica_o_que_falta(client, monkeypatch):
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    lead_id = _lead()
    resp = client.post("/api/wa/start", json={"lead_id": lead_id})
    assert resp.status_code == 503
    assert "WHATSAPP_ACCESS_TOKEN" in resp.json()["detail"]


# ── Status para a tela ───────────────────────────────────────────────────────

def test_status_lista_o_que_falta_configurar(client, monkeypatch):
    monkeypatch.delenv("WHATSAPP_TEMPLATE_NAME", raising=False)
    corpo = client.get("/api/wa/status").json()
    assert corpo["configurado"] is True
    assert corpo["template"] is False
    assert "WHATSAPP_TEMPLATE_NAME" in corpo["faltando"]
