"""
Testes de integração da API usando banco SQLite em memória.
Não fazem chamadas externas (enricher e decision_finder são mockados).
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models.database import Base, get_db, Profile, Lead
from services.enricher import ENRICHMENT_VERSION
from main import app

# ── banco em memória para os testes ──────────────────────────────────────────
# StaticPool garante que todas as conexões usam o mesmo banco em memória
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

Base.metadata.create_all(bind=_engine)


def override_get_db():
    db = _Session()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

# Desliga o rate limiter nos testes — o storage em memória persiste entre
# testes e estoura o limite de 10/min com a suíte completa.
from routers.enrichment import limiter as _enrich_limiter  # noqa: E402
from routers.privacy import limiter as _privacy_limiter  # noqa: E402
from routers.extension import limiter as _extension_limiter  # noqa: E402
from routers.batch import limiter as _batch_limiter  # noqa: E402
_enrich_limiter.enabled = False
_privacy_limiter.enabled = False
_extension_limiter.enabled = False
_batch_limiter.enabled = False

# ── token JWT falso (a lógica de decode é mockada) ────────────────────────────
FAKE_USER = {"sub": "test-user-123", "email": "test@example.com"}
FAKE_TOKEN = "Bearer fake.jwt.token"


def _mock_current_user():
    return FAKE_USER


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def client():
    from middleware.auth import get_current_user
    app.dependency_overrides[get_current_user] = _mock_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


# ── testes /api/me ─────────────────────────────────────────────────────────────
def test_me_creates_profile_on_first_call(client):
    resp = client.get("/api/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "test-user-123"
    assert data["email"] == "test@example.com"


def test_me_nao_expoe_plano_nem_cota(client):
    """
    O acesso é aberto. Um campo de plano ou de cota aqui voltaria a aparecer na
    tela — e a tela passaria a prometer um limite que o servidor não aplica.
    """
    client.get("/api/me")
    corpo = client.get("/api/me").json()
    assert set(corpo) == {"id", "email"}


def test_me_returns_existing_profile(client):
    db = _Session()
    db.add(Profile(id="test-user-123"))
    db.commit()
    db.close()

    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["id"] == "test-user-123"


# ── testes /api/enrich ─────────────────────────────────────────────────────────
MOCK_ENRICH_RESULT = {
    "raw_input_domain": "nubank.com.br",
    "domain": "nubank.com.br",
    "website": "https://nubank.com.br",
    "company_name": "Nubank",
    "description": "Banco digital",
    "location": "São Paulo, Brasil",
    "sector": "Fintech",
    "corporate_email": None,
    "phone": None,
    "linkedin_url": "https://linkedin.com/company/nubank",
    "linkedin_confidence": "verified",
    "mx_provider": "Google",
    "mx_provider_confidence": "high",
    "mx_records": [],
    "dns_report": None,
    "hosting_provider": "AWS",
    "employee_count": None,
    "employee_count_linkedin": None,
    "status": "enriched",
    "enrichment_version": ENRICHMENT_VERSION,
}


def test_enrich_creates_profile_and_lead(client):
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["company_name"] == "Nubank"
    assert data["data"]["domain"] == "nubank.com.br"


def test_enrich_nunca_recusa_por_limite_de_uso(client):
    """
    Não existe cota: nenhuma sequência de buscas pode levar a 402. Este teste é
    a trava que impede um limite voltar sem ninguém notar.
    """
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        for i in range(12):
            resp = client.post("/api/enrich", json={"domain": f"empresa{i}.com.br"})
            assert resp.status_code == 200, resp.text


def test_enrich_empty_domain_returns_422(client):
    resp = client.post("/api/enrich", json={"domain": "  "})
    assert resp.status_code == 422


# ── testes /api/export ─────────────────────────────────────────────────────────
def test_export_single_not_found(client):
    resp = client.get("/api/export/9999")
    assert resp.status_code == 404


def test_export_all_empty(client):
    resp = client.get("/api/export")
    assert resp.status_code == 404


# ── testes cache de domínio ───────────────────────────────────────────────────
def test_enrich_cache_hit_skips_external_call(client):
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT) as mock_enrich:
        client.post("/api/enrich", json={"domain": "nubank.com.br"})
        client.post("/api/enrich", json={"domain": "nubank.com.br"})

    # enrich_company deve ter sido chamado apenas 1x (2a request é do cache)
    assert mock_enrich.call_count == 1


def test_enrich_cache_returns_cached_data(client):
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        r1 = client.post("/api/enrich", json={"domain": "nubank.com.br"})
        r2 = client.post("/api/enrich", json={"domain": "nubank.com.br"})

    assert r2.status_code == 200
    assert r2.json()["message"] == "Dados carregados do cache."
    assert r2.json()["data"]["domain"] == r1.json()["data"]["domain"]


def test_enrich_repetido_nao_duplica_a_ficha(client):
    """Buscar o mesmo domínio duas vezes é uma empresa no histórico, não duas."""
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        client.post("/api/enrich", json={"domain": "nubank.com.br"})
        client.post("/api/enrich", json={"domain": "nubank.com.br"})

    assert len(client.get("/api/leads").json()) == 1


def test_ficha_de_versao_antiga_nao_e_servida_do_cache(client):
    """
    Uma correção na coleta precisa alcançar o usuário na busca seguinte. Sem
    isto, a ficha errada continuaria sendo servida por até 7 dias.
    """
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        client.post("/api/enrich", json={"domain": "nubank.com.br"})

    db = _Session()
    db.query(Lead).update({Lead.enrichment_version: ENRICHMENT_VERSION - 1})
    db.commit()
    db.close()

    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT) as mock:
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})

    assert mock.call_count == 1
    assert resp.json()["message"] == "Enriquecimento concluído."


def test_recoleta_por_versao_antiga_reaproveita_a_mesma_ficha(client):
    """
    A ficha ficou obsoleta por correção nossa: a recoleta atualiza a linha que
    já existe. Abrir uma segunda faria a mesma empresa aparecer duas vezes no
    histórico, uma com dados velhos.
    """
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        client.post("/api/enrich", json={"domain": "nubank.com.br"})

    db = _Session()
    db.query(Lead).update({Lead.enrichment_version: ENRICHMENT_VERSION - 1})
    db.commit()
    db.close()

    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        client.post("/api/enrich", json={"domain": "nubank.com.br"})

    assert len(client.get("/api/leads").json()) == 1


# ── testes /health ────────────────────────────────────────────────────────────
def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


# ── testes de rotas de página (split landing/app) ─────────────────────────────
def test_home_serves_landing(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "hero-title" in resp.text  # hero da landing


def test_app_route_serves_product(client):
    resp = client.get("/app")
    assert resp.status_code == 200
    assert "domain-input" in resp.text  # input principal do produto


# ── páginas institucionais (confiança/LGPD) ──────────────────────────────────
@pytest.mark.parametrize(
    "path,marker",
    [
        ("/termos", "Termos de Uso"),
        ("/privacidade", "Política de Privacidade"),
        ("/seguranca", "Segurança"),
    ],
)
def test_institutional_pages(client, path, marker):
    resp = client.get(path)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert marker in resp.text
    # o CSS compartilhado é incluído via Jinja — se o include falhar, some
    assert "lg-wrap" in resp.text


# ── coleta interrompida no meio ───────────────────────────────────────────────

def test_busca_interrompida_e_retomada_na_mesma_ficha(client):
    """
    Quando a coleta morre no meio (timeout da função serverless, por exemplo), a
    ficha fica gravada como "pending". A tentativa seguinte do mesmo domínio
    continua nela — senão a empresa acabaria com duas linhas, uma vazia.
    """
    with patch("services.enrichment_service.enrich_company", side_effect=TimeoutError("estourou")):
        resp = client.post("/api/enrich", json={"domain": "acme.com.br"})
    assert resp.status_code == 500

    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        ok = client.post("/api/enrich", json={"domain": "acme.com.br"})
    assert ok.status_code == 200

    db = _Session()
    try:
        # Conta todas as fichas do usuário: a coleta grava o domínio que ela
        # apurou, então filtrar por "acme.com.br" esconderia a duplicata.
        assert db.query(Lead).count() == 1, (
            "a retentativa abriu uma segunda ficha para o mesmo domínio"
        )
    finally:
        db.close()


def test_ficha_pendente_nao_aparece_no_historico(client):
    """Ficha sem dados é recibo interno, não conteúdo para o usuário ver."""
    with patch("services.enrichment_service.enrich_company", side_effect=TimeoutError):
        client.post("/api/enrich", json={"domain": "acme.com.br"})

    assert client.get("/api/leads").json() == []
