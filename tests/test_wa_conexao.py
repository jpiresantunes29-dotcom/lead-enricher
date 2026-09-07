"""
WhatsApp conectado por conta, não por servidor.

O que estes testes guardam é o que muda quando o número deixa de ser um só:

  **Envio pelo número certo.** A automação responde em nome do dono da
  conversa. Enviar com a credencial errada é falar com o lead de alguém
  usando a identidade de outra pessoa.

  **Webhook que sabe de quem é.** Cada conta tem o próprio App Secret e o
  mesmo lead pode falar com duas delas. Sem escopo, a mensagem entra na
  conversa errada e um usuário lê o histórico do outro.

  **Token conferido antes de valer.** Gravar não é conectar: token inválido
  precisa ser recusado no formulário, não no primeiro convite — que é pago.
"""
import json
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session  # noqa: F401

from models.database import (
    AI_ACTIVE, Conversation, Lead, WaMessage, WhatsAppConnection, utcnow,
)
from services.wa import client as wa_client, credenciais, gate, webhook

OUTRO_USER = "outro-user-999"
MEU_USER = "test-user-123"
MEU_PNID = "111000111"
SEGREDO_DA_CONTA = "segredo-da-minha-conta"


@pytest.fixture(autouse=True)
def horario_comercial(monkeypatch):
    monkeypatch.setattr(gate, "service_window", lambda agora=None: (True, False))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def sem_ambiente(monkeypatch):
    """
    Servidor sem WhatsApp nenhum.

    É o cenário que interessa aqui: o que funciona precisa funcionar pela
    conexão da conta. Com as variáveis definidas, um teste passaria usando o
    número do servidor sem ninguém perceber.
    """
    for var in ("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_TEMPLATE_NAME", "WHATSAPP_APP_SECRET",
                "WHATSAPP_VERIFY_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def _conectar(user_id=MEU_USER, pnid=MEU_PNID, secret=SEGREDO_DA_CONTA,
              template="primeiro_contato"):
    db = _Session()
    try:
        conexao = WhatsAppConnection(
            user_id=user_id, phone_number_id=pnid,
            access_token=f"token-de-{user_id}", app_secret=secret,
            verify_token=f"verify-de-{user_id}", template_name=template,
            waba_id="WABA1", display_phone_number="+55 11 98888-7777",
            verified_at=utcnow(),
        )
        db.add(conexao)
        db.commit()
    finally:
        db.close()


def _lead(user_id=MEU_USER, phone="+5511988887777"):
    db = _Session()
    try:
        lead = Lead(user_id=user_id, raw_input_domain="acme.com.br",
                    domain="acme.com.br", company_name="Acme", status="enriched",
                    phone=phone, relationship="LEAD")
        db.add(lead)
        db.commit()
        return lead.id
    finally:
        db.close()


def _conversa(user_id, phone="+5511988887777", lead_id=None):
    db = _Session()
    try:
        conversa = Conversation(
            lead_id=lead_id or _lead(user_id, phone), user_id=user_id,
            phone_e164=phone, ai_status=AI_ACTIVE,
            window_expires_at=utcnow().replace(year=utcnow().year + 1),
            last_inbound_at=utcnow(),
        )
        db.add(conversa)
        db.commit()
        return conversa.id
    finally:
        db.close()


# ── Envio sai pelo número de quem é a conversa ───────────────────────────────

def test_convite_usa_o_token_da_conta_e_nao_o_do_servidor(client):
    _conectar()
    lead_id = _lead()

    with patch("services.wa.client._post",
               return_value=wa_client.SendResult(True, wa_message_id="wamid.1")) as post:
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})

    assert resp.status_code == 200
    _payload, cred = post.call_args[0]
    assert cred.phone_number_id == MEU_PNID
    assert cred.access_token == f"token-de-{MEU_USER}"
    assert cred.origem == credenciais.ORIGEM_CONTA


def test_sem_conexao_e_sem_ambiente_a_rota_manda_conectar(client):
    lead_id = _lead()
    resp = client.post("/api/wa/start", json={"lead_id": lead_id})
    assert resp.status_code == 503
    assert "Conecte seu WhatsApp" in resp.json()["detail"]


def test_resposta_manual_sai_pela_conexao_da_conta(client):
    _conectar()
    conversa_id = _conversa(MEU_USER)

    with patch("services.wa.client._post",
               return_value=wa_client.SendResult(True, wa_message_id="wamid.2")) as post:
        resp = client.post(f"/api/wa/conversations/{conversa_id}/reply",
                           json={"texto": "oi, tudo bem?"})

    assert resp.status_code == 200
    _payload, cred = post.call_args[0]
    assert cred.access_token == f"token-de-{MEU_USER}"


# ── O webhook sabe de quem é a mensagem ──────────────────────────────────────

def _evento(pnid, de="5511988887777", wamid="wamid.E1", texto="oi"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": pnid},
            "messages": [{"from": de, "id": wamid, "timestamp": "1770000000",
                          "type": "text", "text": {"body": texto}}],
        }}]}],
    }


def test_assinatura_e_conferida_com_o_segredo_da_conta_dona_do_numero(client):
    _conectar()
    conversa_id = _conversa(MEU_USER)

    corpo = json.dumps(_evento(MEU_PNID)).encode()
    resp = client.post(
        "/api/wa/webhook", content=corpo,
        headers={webhook.SIGNATURE_HEADER:
                 webhook.expected_signature(corpo, SEGREDO_DA_CONTA)},
    )

    assert resp.status_code == 200
    db = _Session()
    try:
        assert db.query(WaMessage).filter(
            WaMessage.conversation_id == conversa_id,
            WaMessage.direction == "in").count() == 1
    finally:
        db.close()


def test_assinatura_de_outra_conta_nao_serve(client):
    """O segredo de uma conta não pode validar o webhook de outra."""
    _conectar()
    _conectar(user_id=OUTRO_USER, pnid="222000222", secret="segredo-do-outro")
    _conversa(MEU_USER)

    corpo = json.dumps(_evento(MEU_PNID)).encode()
    resp = client.post(
        "/api/wa/webhook", content=corpo,
        headers={webhook.SIGNATURE_HEADER:
                 webhook.expected_signature(corpo, "segredo-do-outro")},
    )
    assert resp.status_code == 403


def test_mensagem_entra_na_conversa_da_conta_dona_do_numero(client):
    """
    O mesmo lead falando com duas contas.

    Sem escopo por dono, a mensagem cairia na conversa mais recente — que
    pode ser a do outro usuário.
    """
    telefone = "+5511988887777"
    _conectar()
    _conectar(user_id=OUTRO_USER, pnid="222000222", secret="segredo-do-outro")
    minha = _conversa(MEU_USER, telefone)
    do_outro = _conversa(OUTRO_USER, telefone)

    corpo = json.dumps(_evento(MEU_PNID, wamid="wamid.PARA_MIM")).encode()
    resp = client.post(
        "/api/wa/webhook", content=corpo,
        headers={webhook.SIGNATURE_HEADER:
                 webhook.expected_signature(corpo, SEGREDO_DA_CONTA)},
    )

    assert resp.status_code == 200
    db = _Session()
    try:
        recebida = db.query(WaMessage).filter(
            WaMessage.wa_message_id == "wamid.PARA_MIM").one()
        assert recebida.conversation_id == minha
        assert recebida.conversation_id != do_outro
    finally:
        db.close()


def test_webhook_de_numero_desconhecido_e_recusado(client):
    _conectar()
    corpo = json.dumps(_evento("999999999")).encode()
    resp = client.post(
        "/api/wa/webhook", content=corpo,
        headers={webhook.SIGNATURE_HEADER:
                 webhook.expected_signature(corpo, SEGREDO_DA_CONTA)},
    )
    assert resp.status_code == 403


def test_handshake_aceita_o_token_de_qualquer_conta_conectada(client):
    _conectar()
    resp = client.get("/api/wa/webhook", params={
        "hub.mode": "subscribe", "hub.challenge": "desafio-123",
        "hub.verify_token": f"verify-de-{MEU_USER}",
    })
    assert resp.status_code == 200
    assert resp.text == "desafio-123"


def test_handshake_recusa_token_que_nao_e_de_ninguem(client):
    _conectar()
    resp = client.get("/api/wa/webhook", params={
        "hub.mode": "subscribe", "hub.challenge": "desafio-123",
        "hub.verify_token": "chutado",
    })
    assert resp.status_code == 403


# ── A tela de configuração ───────────────────────────────────────────────────

def test_conectar_confere_com_a_meta_antes_de_gravar(client):
    with patch("services.wa.client.conferir",
               return_value=(True, {"display_phone_number": "+55 11 98888-7777",
                                    "waba_id": "WABA1"})):
        resp = client.post("/api/wa/connection", json={
            "phone_number_id": MEU_PNID, "access_token": "token-novo",
            "app_secret": "s3cr3t", "template_name": "primeiro_contato",
        })

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["conectado"] is True
    assert corpo["origem"] == credenciais.ORIGEM_CONTA
    assert corpo["display_phone_number"] == "+55 11 98888-7777"
    assert corpo["verificado_em"] is not None


def test_token_recusado_pela_meta_nao_grava_nada(client):
    with patch("services.wa.client.conferir",
               return_value=(False, "A Meta recusou: token expirado")):
        resp = client.post("/api/wa/connection", json={
            "phone_number_id": MEU_PNID, "access_token": "token-vencido",
        })

    assert resp.status_code == 422
    assert "token expirado" in resp.json()["detail"]
    db = _Session()
    try:
        assert db.query(WhatsAppConnection).count() == 0
    finally:
        db.close()


def test_a_tela_nunca_devolve_o_segredo(client):
    _conectar()
    corpo = client.get("/api/wa/connection").json()
    assert corpo["tem_token"] is True
    assert corpo["tem_app_secret"] is True
    inteiro = json.dumps(corpo)
    assert f"token-de-{MEU_USER}" not in inteiro
    assert SEGREDO_DA_CONTA not in inteiro


def test_numero_ja_conectado_em_outra_conta_e_recusado(client):
    _conectar(user_id=OUTRO_USER, pnid=MEU_PNID, secret="segredo-do-outro")
    with patch("services.wa.client.conferir", return_value=(True, {})):
        resp = client.post("/api/wa/connection", json={
            "phone_number_id": MEU_PNID, "access_token": "token-meu",
        })
    assert resp.status_code == 409


def test_campo_em_branco_mantem_o_segredo_gravado(client):
    """Trocar só o template não pode exigir recolar o token."""
    _conectar()
    with patch("services.wa.client.conferir", return_value=(True, {})):
        resp = client.post("/api/wa/connection", json={
            "phone_number_id": MEU_PNID, "template_name": "outro_template",
        })

    assert resp.status_code == 200
    db = _Session()
    try:
        conexao = db.query(WhatsAppConnection).filter(
            WhatsAppConnection.user_id == MEU_USER).one()
        assert conexao.access_token == f"token-de-{MEU_USER}"
        assert conexao.app_secret == SEGREDO_DA_CONTA
        assert conexao.template_name == "outro_template"
    finally:
        db.close()


def test_desconectar_apaga_a_credencial_e_preserva_as_conversas(client):
    _conectar()
    conversa_id = _conversa(MEU_USER)

    resp = client.delete("/api/wa/connection")

    assert resp.status_code == 200
    db = _Session()
    try:
        assert db.query(WhatsAppConnection).count() == 0
        assert db.query(Conversation).filter(Conversation.id == conversa_id).count() == 1
    finally:
        db.close()


def test_status_diz_que_o_numero_e_da_conta(client):
    _conectar()
    corpo = client.get("/api/wa/status").json()
    assert corpo["configurado"] is True
    assert corpo["origem"] == credenciais.ORIGEM_CONTA


def test_conexao_ilegivel_recusa_envio_em_vez_de_cair_para_o_servidor(client, monkeypatch):
    """
    Chave de segredos trocada.

    O risco é enviar pelo número do servidor achando que é o do usuário — a
    mensagem sairia com outro remetente, sem erro nenhum.
    """
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "do-servidor")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token-do-servidor")
    _conectar()
    lead_id = _lead()

    from services import crypto
    monkeypatch.setattr(crypto, "ILEGIVEL", f"token-de-{MEU_USER}")
    monkeypatch.setattr(credenciais, "ILEGIVEL", f"token-de-{MEU_USER}")

    resp = client.post("/api/wa/start", json={"lead_id": lead_id})
    assert resp.status_code == 503
    assert "não puderam ser lidas" in resp.json()["detail"]
