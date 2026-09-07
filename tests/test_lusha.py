"""
Integração Lusha (BYOA — chave do próprio usuário).

O que estes testes protegem, em ordem de importância:
  1. Sem chave conectada, NENHUMA requisição sai e nada custa.
  2. Sem identificador forte, não gastamos o crédito mínimo da Lusha à toa.
  3. Falha da Lusha (401/402/429/rede) nunca derruba a revelação.
  4. Telefone só é entregue quando dá para montar o E.164 sem inventar DDI.
"""
import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base
from services.people import repository as repo, waterfall
from services.providers import lusha


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


class _RespostaFalsa:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _proibir_rede(monkeypatch):
    """Qualquer requisição vira erro de teste — prova que nada foi chamado."""
    def explode(*a, **k):
        raise AssertionError("Nenhuma requisição deveria sair sem chave/identidade")
    monkeypatch.setattr(requests, "get", explode)


# ── 1. Sem chave: custo zero ────────────────────────────────────────────────

def test_sem_chave_nao_faz_requisicao(monkeypatch):
    _proibir_rede(monkeypatch)
    assert lusha.find_contacts(full_name="João Silva", domain="acme.com") is None
    assert lusha.find_contacts(full_name="João Silva", domain="acme.com", api_key="") is None
    assert lusha.find_contacts(full_name="João Silva", domain="acme.com", api_key="   ") is None


def test_reveal_sem_chave_nao_chama_lusha(db, monkeypatch):
    """A revelação inteira roda no gratuito quando o usuário não conectou Lusha."""
    monkeypatch.setattr(
        lusha, "find_contacts",
        lambda **k: (_ for _ in ()).throw(AssertionError("Lusha não deveria ser chamada")),
    )
    company = repo.upsert_company(db, "acme.com", name="Acme")
    person = repo.upsert_person(db, full_name="João Silva", slug="joao-silva",
                                company_domain="acme.com", company=company)
    db.flush()

    resultado = waterfall.reveal(db, person, company=company, budget_seconds=0.1)
    assert "lusha" not in resultado["chain"]


# ── 2. Não queimar o crédito mínimo à toa ───────────────────────────────────

def test_sem_identificador_forte_nao_gasta_credito(monkeypatch):
    """
    A Lusha cobra 1 crédito mesmo sem achar ninguém. Só nome, ou só domínio,
    não é identidade suficiente para valer a chamada.
    """
    _proibir_rede(monkeypatch)
    assert lusha.find_contacts(full_name="João Silva", api_key="k") is None
    assert lusha.find_contacts(domain="acme.com", api_key="k") is None
    assert lusha.find_contacts(company_name="Acme", api_key="k") is None


@pytest.mark.parametrize("kwargs", [
    {"linkedin_url": "https://www.linkedin.com/in/joao-silva"},
    {"full_name": "João Silva", "domain": "acme.com"},
    {"full_name": "João Silva", "company_name": "Acme"},
])
def test_identificador_forte_dispara_chamada(monkeypatch, kwargs):
    chamou = []
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: chamou.append(k) or _RespostaFalsa(404))
    lusha.find_contacts(api_key="k", **kwargs)
    assert len(chamou) == 1
    assert chamou[0]["headers"]["api_key"] == "k"


# ── 3. Falha da Lusha nunca derruba a revelação ─────────────────────────────

@pytest.mark.parametrize("status", [400, 401, 402, 404, 429, 500])
def test_erro_da_lusha_vira_none(monkeypatch, status):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _RespostaFalsa(status))
    assert lusha.find_contacts(
        full_name="João Silva", domain="acme.com", api_key="k") is None


def test_rede_fora_vira_none(monkeypatch):
    def cai(*a, **k):
        raise requests.ConnectionError("sem rede")
    monkeypatch.setattr(requests, "get", cai)
    assert lusha.find_contacts(
        full_name="João Silva", domain="acme.com", api_key="k") is None


def test_resposta_vazia_ou_ilegivel_vira_none(monkeypatch):
    for payload in ({}, {"data": {}}, {"data": None}, []):
        monkeypatch.setattr(requests, "get", lambda *a, **k: _RespostaFalsa(200, payload))
        assert lusha.find_contacts(
            full_name="João Silva", domain="acme.com", api_key="k") is None


# ── 4. Extração tolerante a formato ─────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    {"data": {"emailAddresses": [{"email": "joao@acme.com"}]}},
    {"data": {"emails": [{"address": "joao@acme.com"}]}},
    {"emailAddresses": ["joao@acme.com"]},
    {"data": {"contact": {"email": "joao@acme.com"}}},
])
def test_email_e_encontrado_em_varios_formatos(monkeypatch, payload):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _RespostaFalsa(200, payload))
    r = lusha.find_contacts(full_name="João Silva", domain="acme.com", api_key="k")
    assert r is not None
    assert r["emails"][0]["email"] == "joao@acme.com"
    assert r["emails"][0]["confidence"] == lusha.CONF_LUSHA


def test_celular_vem_antes_do_fixo(monkeypatch):
    payload = {"data": {"phoneNumbers": [
        {"number": "+55 11 3333-4444", "phoneType": "work"},
        {"number": "+55 11 98765-4321", "phoneType": "mobile"},
    ]}}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _RespostaFalsa(200, payload))
    r = lusha.find_contacts(full_name="João Silva", domain="acme.com", api_key="k")
    assert r["phones"][0]["type"] == "mobile"
    assert r["phones"][0]["e164"] == "+5511987654321"


@pytest.mark.parametrize("bruto,esperado", [
    ("+55 11 98765-4321", "+5511987654321"),
    ("11987654321", "+5511987654321"),
    ("(11) 3333-4444", "+551133334444"),
    ("5511987654321", "+5511987654321"),
    ("+1 415 555 2671", "+14155552671"),
    # Comprimento irreconhecível: melhor não entregar do que inventar DDI e
    # mandar o vendedor ligar para um estranho.
    ("12345", None),
    ("", None),
])
def test_e164_nunca_inventa_ddi(bruto, esperado):
    assert lusha._digits_to_e164(bruto) == esperado


# ── 5. Cascata: grava e-mail, telefone e cargo ──────────────────────────────

def test_reveal_com_chave_grava_contato_da_lusha(db, monkeypatch):
    monkeypatch.setattr(lusha, "find_contacts", lambda **k: {
        "provider": "lusha",
        "emails": [{"email": "joao@acme.com", "status": "unknown", "confidence": 92}],
        "phones": [{"e164": "+5511987654321", "formatted": "+55 11 98765-4321",
                    "type": "mobile", "confidence": 92}],
        "title": "Chief Technology Officer",
    })
    company = repo.upsert_company(db, "acme.com", name="Acme")
    person = repo.upsert_person(db, full_name="João Silva", slug="joao-silva",
                                company_domain="acme.com", company=company)
    db.flush()

    resultado = waterfall.reveal(db, person, company=company,
                                 budget_seconds=0.1, lusha_api_key="k")
    assert "lusha" in resultado["chain"]
    assert any(e["email"] == "joao@acme.com" for e in resultado["emails"])
    telefone = next(p for p in resultado["phones"] if p["e164"] == "+5511987654321")
    assert telefone["type"] == "mobile"
    assert telefone["source"] == "lusha"
    assert person.title == "Chief Technology Officer"


def test_credencial_invalida_e_recusada(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _RespostaFalsa(401))
    assert lusha.credencial_valida("chave-errada") is False


def test_lusha_fora_do_ar_nao_reprova_chave(monkeypatch):
    """Indisponibilidade deles não pode impedir o usuário de conectar."""
    def cai(*a, **k):
        raise requests.ConnectionError("timeout")
    monkeypatch.setattr(requests, "get", cai)
    assert lusha.credencial_valida("chave-boa") is True
