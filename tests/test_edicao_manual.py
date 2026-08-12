"""
Correção manual da ficha: telefone do lead e celular do decisor.

O que se protege aqui é a promessa do campo — que o número guardado dá para
discar. Aceitar o que veio digitado seria mais simples e só revelaria o erro
na hora da ligação.
"""
from unittest.mock import patch

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401

from models.database import DecisionMaker, Lead


def _make_lead(client):
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})
    return resp.json()["data"]


def _make_decisor(lead_id: int, phone=None) -> int:
    db = _Session()
    try:
        dm = DecisionMaker(lead_id=lead_id, name="Ana Souza",
                           title_searched="Diretora de TI", phone=phone)
        db.add(dm)
        db.commit()
        return dm.id
    finally:
        db.close()


# ── telefone do lead ─────────────────────────────────────────────────────────

def test_telefone_e_normalizado_ao_salvar(client):
    lead = _make_lead(client)
    resp = client.patch(f"/api/leads/{lead['id']}", json={"phone": "(11) 98888-7777"})
    assert resp.status_code == 200
    assert resp.json()["phone"] == "+55 11 9 8888 7777"


def test_celular_e_reconhecido_como_celular(client):
    lead = _make_lead(client)
    resp = client.patch(f"/api/leads/{lead['id']}", json={"phone": "11988887777"})
    assert resp.json()["phone_is_mobile"] is True


def test_fixo_e_marcado_como_numero_de_central(client):
    """É o aviso que impede o usuário de esperar WhatsApp de uma recepção."""
    lead = _make_lead(client)
    resp = client.patch(f"/api/leads/{lead['id']}", json={"phone": "11 4002-8922"})
    assert resp.json()["phone_is_mobile"] is False


def test_telefone_invalido_e_recusado(client):
    lead = _make_lead(client)
    resp = client.patch(f"/api/leads/{lead['id']}", json={"phone": "1234"})
    assert resp.status_code == 422
    assert "inválido" in resp.json()["detail"].lower()


def test_telefone_invalido_nao_apaga_o_que_ja_estava_salvo(client):
    lead = _make_lead(client)
    client.patch(f"/api/leads/{lead['id']}", json={"phone": "11988887777"})
    client.patch(f"/api/leads/{lead['id']}", json={"phone": "abc"})
    resp = client.get(f"/api/leads/{lead['id']}")
    assert resp.json()["phone"] == "+55 11 9 8888 7777"


def test_string_vazia_apaga_o_telefone(client):
    lead = _make_lead(client)
    client.patch(f"/api/leads/{lead['id']}", json={"phone": "11988887777"})
    resp = client.patch(f"/api/leads/{lead['id']}", json={"phone": ""})
    assert resp.status_code == 200
    assert resp.json()["phone"] is None
    assert resp.json()["phone_is_mobile"] is None


def test_corpo_sem_campo_algum_e_recusado(client):
    """PATCH vazio apagaria o telefone se `None` fosse lido como "limpar"."""
    lead = _make_lead(client)
    client.patch(f"/api/leads/{lead['id']}", json={"phone": "11988887777"})
    resp = client.patch(f"/api/leads/{lead['id']}", json={})
    assert resp.status_code == 422
    assert client.get(f"/api/leads/{lead['id']}").json()["phone"] == "+55 11 9 8888 7777"


def test_lead_de_outro_usuario_nao_e_editavel(client):
    db = _Session()
    try:
        alheio = Lead(user_id="outro-usuario", raw_input_domain="acme.com.br",
                      domain="acme.com.br", status="enriched")
        db.add(alheio)
        db.commit()
        lead_id = alheio.id
    finally:
        db.close()

    resp = client.patch(f"/api/leads/{lead_id}", json={"phone": "11988887777"})
    assert resp.status_code == 404


# ── celular do decisor ───────────────────────────────────────────────────────

def test_celular_do_decisor_e_normalizado(client):
    lead = _make_lead(client)
    decisor_id = _make_decisor(lead["id"])
    resp = client.patch(f"/api/decisores/{decisor_id}", json={"phone": "(11) 98888-7777"})
    assert resp.status_code == 200
    assert resp.json()["phone"] == "+55 11 9 8888 7777"
    assert resp.json()["phone_is_mobile"] is True


def test_celular_do_decisor_invalido_e_recusado(client):
    lead = _make_lead(client)
    decisor_id = _make_decisor(lead["id"])
    resp = client.patch(f"/api/decisores/{decisor_id}", json={"phone": "999"})
    assert resp.status_code == 422


def test_decisor_de_lead_alheio_nao_e_editavel(client):
    db = _Session()
    try:
        alheio = Lead(user_id="outro-usuario", raw_input_domain="acme.com.br",
                      domain="acme.com.br", status="enriched")
        db.add(alheio)
        db.commit()
        lead_id = alheio.id
    finally:
        db.close()

    decisor_id = _make_decisor(lead_id)
    resp = client.patch(f"/api/decisores/{decisor_id}", json={"phone": "11988887777"})
    assert resp.status_code == 404
