"""
Testes da API da extensão: pareamento, resolve (grátis), reveal (cobrança)
e as travas de LGPD. Nenhuma chamada externa — a cascata é mockada.
"""
import pytest
from unittest.mock import patch

from tests.test_api import client, clean_db, _Session  # noqa: F401

from main import app
from models.database import Person, Reveal
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
    assert resp.json()["user_id"] == FAKE_USER["sub"]


def test_rotas_exigem_autenticacao(client):
    assert client.get("/api/extension/me").status_code == 403


# ── resolve: rápido e sem rede pesada ────────────────────────────────────────

def test_resolve_cria_pessoa(ext_client):
    resp = _resolve(ext_client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["person_id"]
    assert data["full_name"] == "João Silva"
    assert data["company_domain"] == "acme.com"
    assert data["seniority"] in ("c_level", "founder")

    db = _Session()
    try:
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


# ── reveal ───────────────────────────────────────────────────────────────────

def test_reveal_entrega_contato_quando_encontra(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    with patch("routers.extension.waterfall.reveal", return_value=FOUND_RESULT):
        resp = ext_client.post("/api/extension/reveal", json={"person_id": person_id})

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["emails"][0]["email"] == "joao.silva@acme.com"
    assert data["phones"][0]["is_company_phone"] is True


def test_reveal_diz_quando_nao_encontra(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    with patch("routers.extension.waterfall.reveal", return_value=EMPTY_RESULT):
        data = ext_client.post("/api/extension/reveal", json={"person_id": person_id}).json()

    assert data["success"] is False
    assert "Não encontramos contato confiável" in data["message"]


def test_reveal_repetido_nunca_e_recusado(ext_client):
    """
    Revelar é livre: repetir a mesma pessoa quantas vezes for não pode voltar
    402 nem parar de entregar o contato.
    """
    person_id = _resolve(ext_client).json()["person_id"]
    with patch("routers.extension.waterfall.reveal", return_value=FOUND_RESULT):
        for _ in range(8):
            resp = ext_client.post("/api/extension/reveal", json={"person_id": person_id})
            assert resp.status_code == 200
            assert resp.json()["success"] is True


def test_reveal_registra_o_historico(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    with patch("routers.extension.waterfall.reveal", return_value=FOUND_RESULT):
        ext_client.post("/api/extension/reveal", json={"person_id": person_id})

    db = _Session()
    try:
        row = db.query(Reveal).first()
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


def test_optout_publico_nao_exige_login_mas_exige_confirmacao(client):
    """
    O pedido entra sem cadastro (é um direito), mas só bloqueia depois que o
    titular abre o link enviado por e-mail. Sem essa trava, qualquer pessoa
    apagaria contatos alheios em massa pelo formulário público.
    """
    with patch("routers.privacy.mailer.send", return_value=True) as enviado:
        resp = client.post("/api/privacidade/opt-out", json={
            "kind": "email", "value": "pessoa@empresa.com", "reason": "não quero",
        })
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    enviado.assert_called_once()
    destino, _assunto, corpo = enviado.call_args.args[:3]
    assert destino == "pessoa@empresa.com"

    db = _Session()
    try:
        assert not optout.is_blocked(db, "email", "pessoa@empresa.com"), \
            "pedido pendente não pode bloquear"
    finally:
        db.close()

    # O link do e-mail confirma, e só então o bloqueio passa a valer.
    link = [w for w in corpo.split() if "token=" in w][0]
    token = link.split("token=", 1)[1]
    pagina = client.get("/privacidade/confirmar", params={"token": token})
    assert pagina.status_code == 200
    assert "Remoção confirmada" in pagina.text

    db = _Session()
    try:
        assert optout.is_blocked(db, "email", "pessoa@empresa.com")
    finally:
        db.close()


def test_optout_com_token_invalido_nao_bloqueia(client):
    pagina = client.get("/privacidade/confirmar", params={"token": "token-inventado"})
    assert pagina.status_code == 200
    assert "Link inválido ou expirado" in pagina.text


def test_optout_de_telefone_exige_email_de_contato(client):
    """Telefone não recebe link: sem e-mail de contato, o pedido não sai do lugar."""
    with patch("routers.privacy.mailer.send") as enviado:
        resp = client.post("/api/privacidade/opt-out", json={
            "kind": "phone", "value": "+55 11 98888-7777",
        })
    assert resp.status_code == 200          # resposta genérica, sempre
    enviado.assert_not_called()

    db = _Session()
    try:
        assert not optout.is_blocked(db, "phone", "+5511988887777")
    finally:
        db.close()


def test_optout_confirmado_apaga_o_contato_da_base(client):
    """A confirmação não só bloqueia: remove o que já estava gravado."""
    from models.database import Person, PersonEmail

    db = _Session()
    try:
        person = Person(dedupe_key="k-optout", full_name="Alvo Teste",
                        company_domain="empresa.com")
        db.add(person)
        db.flush()
        db.add(PersonEmail(person_id=person.id, email="apagar@empresa.com",
                           status="valid", confidence=97))
        db.commit()
    finally:
        db.close()

    with patch("routers.privacy.mailer.send", return_value=True) as enviado:
        client.post("/api/privacidade/opt-out",
                    json={"kind": "email", "value": "apagar@empresa.com"})
    corpo = enviado.call_args.args[2]
    token = [w for w in corpo.split() if "token=" in w][0].split("token=", 1)[1]
    client.get("/privacidade/confirmar", params={"token": token})

    db = _Session()
    try:
        assert db.query(PersonEmail).filter(
            PersonEmail.email == "apagar@empresa.com"
        ).count() == 0
        # E o valor em claro do pedido não fica guardado depois de cumprido.
        from models.database import OptOut
        pedido = db.query(OptOut).filter(OptOut.kind == "email").first()
        assert pedido.pending_value is None
        assert pedido.status == "confirmed"
    finally:
        db.close()


def test_optout_publico_nao_revela_se_o_dado_existia(client):
    a = client.post("/api/privacidade/opt-out", json={"kind": "email", "value": "existe@x.com"})
    b = client.post("/api/privacidade/opt-out", json={"kind": "email", "value": "naoexiste@y.com"})
    c = client.post("/api/privacidade/opt-out", json={"kind": "invalido", "value": "z"})
    assert a.json() == b.json() == c.json()


def test_reveal_de_pessoa_bloqueada_nao_entrega(ext_client):
    person_id = _resolve(ext_client).json()["person_id"]
    blocked = dict(EMPTY_RESULT, blocked=True, chain=["optout"])
    with patch("routers.extension.waterfall.reveal", return_value=blocked):
        data = ext_client.post("/api/extension/reveal", json={"person_id": person_id}).json()

    assert data["success"] is False
    assert data["emails"] == []
    assert "LGPD" in data["message"]


def test_pagina_publica_de_remocao_responde(client):
    resp = client.get("/remover-meus-dados")
    assert resp.status_code == 200
    assert "Remover meus dados" in resp.text
