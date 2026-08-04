"""
Testes da API da extensão: pareamento, resolve (grátis), reveal (cobrança)
e as travas de LGPD. Nenhuma chamada externa — a cascata é mockada.
"""
import pytest
from unittest.mock import patch

from tests.test_api import client, clean_db, _Session  # noqa: F401

from main import app
from models.database import Person, Profile, Reveal
from routers.extension import get_ext_user
from services.people import optout, repository as repo

FAKE_USER = {"sub": "test-user-123", "email": "test@example.com"}

EMPTY_RESULT = {
    "emails": [], "phones": [], "chain": ["pattern"], "from_cache": False,
    "blocked": False, "found_email": False, "found_phone": False,
}
FOUND_RESULT = {
    "emails": [{"email": "joao.silva@acme.com", "status": "valid", "confidence": 97,
                "source": "pattern", "pattern": "{first}.{last}"}],
    "phones": [{"e164": "+551140028922", "formatted": "+55 11 4002 8922", "type": "company",
                "confidence": 65, "source": "site", "is_company_phone": True}],
    "chain": ["company_context", "pattern+smtp"], "from_cache": False,
    "blocked": False, "found_email": True, "found_phone": True,
}


@pytest.fixture
def ext_client(client):
    """Cliente com a autenticação da extensão resolvida."""
    app.dependency_overrides[get_ext_user] = lambda: FAKE_USER
    yield client
    app.dependency_overrides.pop(get_ext_user, None)


def _resolve(ext_client, **overrides):
    payload = {
        "linkedin_slug": "joao-silva",
        "linkedin_url": "https://www.linkedin.com/in/joao-silva",
        "full_name": "João Silva",
        "headline": "CTO na Acme",
        "company_name": "Acme",
        "company_domain": "acme.com",
    }
    payload.update(overrides)
    with patch("services.people.waterfall.has_mx", return_value=True):
        return ext_client.post("/api/extension/resolve", json=payload)


# ── pareamento ───────────────────────────────────────────────────────────────

def test_pair_code_e_troca_por_token(client):
    resp = client.post("/api/extension/pair-code", json={})
    assert resp.status_code == 200
    code = resp.json()["code"]
    assert len(code) >= 8

    paired = client.post("/api/extension/pair", json={"code": code, "device_label": "Chrome"})
    assert paired.status_code == 200
    token = paired.json()["token"]
    assert token.startswith("ext_")

    # O código é de uso único
    again = client.post("/api/extension/pair", json={"code": code})
    assert again.status_code == 404


def test_pair_com_codigo_invalido(client):
    assert client.post("/api/extension/pair", json={"code": "XXXX-YYYY"}).status_code == 404


def test_token_da_extensao_autentica_as_rotas(client):
    code = client.post("/api/extension/pair-code", json={}).json()["code"]
    token = client.post("/api/extension/pair", json={"code": code}).json()["token"]

    resp = client.get("/api/extension/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["credits_left"] == 5   # plano free


def test_rotas_exigem_autenticacao(client):
    assert client.get("/api/extension/me").status_code == 403


# ── resolve: rápido e sem cobrar ─────────────────────────────────────────────

def test_resolve_cria_pessoa_e_nao_cobra(ext_client):
    resp = _resolve(ext_client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["person_id"]
    assert data["full_name"] == "João Silva"
    assert data["company_domain"] == "acme.com"
    assert data["seniority"] in ("c_level", "founder")
    assert data["credits_cost"] == 1          # o que custaria revelar
    assert data["credits_left"] == 5          # nada foi consumido

    db = _Session()
    try:
        assert db.query(Profile).filter(Profile.id == FAKE_USER["sub"]).first().reveals_used == 0
        assert db.query(Person).count() == 1
    finally:
        db.close()


def test_resolve_sem_dominio_pede_o_site(ext_client):
    resp = _resolve(ext_client, company_domain=None, company_name="Empresa Desconhecida XPTO")
    assert resp.status_code == 200
    assert resp.json()["needs_domain"] is True


def test_resolve_repetido_nao_duplica_pessoa(ext_client):
    _resolve(ext_client)
    _resolve(ext_client, headline="CTO e sócio na Acme")
    db = _Session()
    try:
        assert db.query(Person).count() == 1
    finally:
        db.close()


def test_resolve_de_pessoa_com_optout(ext_client):
    db = _Session()
    try:
        optout.register(db, "linkedin", "joao-silva")
        db.commit()
    finally:
        db.close()

    data = _resolve(ext_client).json()
    assert data["blocked"] is True
    assert data["person_id"] is None


# ── reveal: cobrança ─────────────────────────────────────────────────────────

def test_reveal_cobra_um_credito_quando_encontra(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    with patch("routers.extension.waterfall.reveal", return_value=FOUND_RESULT):
        resp = ext_client.post("/api/extension/reveal", json={"person_id": person_id})

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["emails"][0]["email"] == "joao.silva@acme.com"
    assert data["phones"][0]["is_company_phone"] is True
    assert data["credits_charged"] == 1
    assert data["credits_left"] == 4


def test_reveal_nao_cobra_quando_nao_encontra(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    with patch("routers.extension.waterfall.reveal", return_value=EMPTY_RESULT):
        data = ext_client.post("/api/extension/reveal", json={"person_id": person_id}).json()

    assert data["success"] is False
    assert data["credits_charged"] == 0
    assert data["credits_left"] == 5
    assert "nada foi cobrado" in data["message"]


def test_segunda_revelacao_da_mesma_pessoa_e_gratis(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    with patch("routers.extension.waterfall.reveal", return_value=FOUND_RESULT):
        ext_client.post("/api/extension/reveal", json={"person_id": person_id})
        segunda = ext_client.post("/api/extension/reveal", json={"person_id": person_id}).json()

    assert segunda["credits_charged"] == 0
    assert segunda["credits_left"] == 4      # continua o mesmo saldo


def test_reveal_bloqueia_sem_creditos(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    db = _Session()
    try:
        profile = db.query(Profile).filter(Profile.id == FAKE_USER["sub"]).first()
        profile.reveals_used = profile.reveals_limit
        db.commit()
    finally:
        db.close()

    with patch("routers.extension.waterfall.reveal", return_value=FOUND_RESULT) as mocked:
        resp = ext_client.post("/api/extension/reveal", json={"person_id": person_id})
    assert resp.status_code == 402
    mocked.assert_not_called()               # não gasta rede se não pode entregar


def test_reveal_registra_no_ledger(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    with patch("routers.extension.waterfall.reveal", return_value=FOUND_RESULT):
        ext_client.post("/api/extension/reveal", json={"person_id": person_id})

    db = _Session()
    try:
        row = db.query(Reveal).first()
        assert row.credits_charged == 1
        assert row.found_email is True
        assert row.provider_chain == ["company_context", "pattern+smtp"]
    finally:
        db.close()


def test_reveal_de_pessoa_inexistente(ext_client):
    assert ext_client.post("/api/extension/reveal", json={"person_id": 999}).status_code == 404


# ── salvar na pipeline ───────────────────────────────────────────────────────

def test_save_cria_lead_e_decisor(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    with patch("routers.extension.waterfall.reveal", return_value=FOUND_RESULT):
        ext_client.post("/api/extension/reveal", json={"person_id": person_id})

    db = _Session()
    try:
        person = db.query(Person).filter(Person.id == person_id).first()
        repo.add_email(db, person, "joao.silva@acme.com", status="valid", confidence=97)
        db.commit()
    finally:
        db.close()

    resp = ext_client.post("/api/extension/save", json={"person_id": person_id})
    assert resp.status_code == 200
    lead_id = resp.json()["lead_id"]

    leads = ext_client.get("/api/leads").json()
    alvo = [l for l in leads if l["id"] == lead_id]
    assert alvo and alvo[0]["domain"] == "acme.com"

    db = _Session()
    try:
        from models.database import DecisionMaker
        decisores = db.query(DecisionMaker).filter(DecisionMaker.lead_id == lead_id).all()
        assert any(d.name == "João Silva" for d in decisores)
        assert any(d.match_confidence == "high" for d in decisores)
    finally:
        db.close()


def test_save_duas_vezes_nao_duplica_decisor(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    ext_client.post("/api/extension/save", json={"person_id": person_id})
    ext_client.post("/api/extension/save", json={"person_id": person_id})

    db = _Session()
    try:
        from models.database import DecisionMaker, Lead
        assert db.query(Lead).count() == 1
        assert db.query(DecisionMaker).count() == 1
    finally:
        db.close()


# ── LGPD ─────────────────────────────────────────────────────────────────────

def test_report_remove_e_bloqueia(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    resp = ext_client.post("/api/extension/report", json={
        "person_id": person_id, "kind": "linkedin", "reason": "não sou eu",
    })
    assert resp.status_code == 200
    assert _resolve(ext_client).json()["blocked"] is True


def test_optout_publico_nao_exige_login(client):
    resp = client.post("/api/privacidade/opt-out", json={
        "kind": "email", "value": "pessoa@empresa.com", "reason": "não quero",
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    db = _Session()
    try:
        assert optout.is_blocked(db, "email", "pessoa@empresa.com")
    finally:
        db.close()


def test_optout_publico_nao_revela_se_o_dado_existia(client):
    a = client.post("/api/privacidade/opt-out", json={"kind": "email", "value": "existe@x.com"})
    b = client.post("/api/privacidade/opt-out", json={"kind": "email", "value": "naoexiste@y.com"})
    c = client.post("/api/privacidade/opt-out", json={"kind": "invalido", "value": "z"})
    assert a.json() == b.json() == c.json()


def test_reveal_de_pessoa_bloqueada_nao_entrega_nem_cobra(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    blocked = dict(EMPTY_RESULT, blocked=True, chain=["optout"])
    with patch("routers.extension.waterfall.reveal", return_value=blocked):
        data = ext_client.post("/api/extension/reveal", json={"person_id": person_id}).json()

    assert data["success"] is False
    assert data["emails"] == []
    assert data["credits_left"] == 5
    assert "LGPD" in data["message"]


def test_pagina_publica_de_remocao_responde(client):
    resp = client.get("/remover-meus-dados")
    assert resp.status_code == 200
    assert "Remover meus dados" in resp.text
