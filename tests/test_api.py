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
_enrich_limiter.enabled = False

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
    assert data["plan"] == "free"
    assert data["searches_used"] == 0
    assert data["searches_limit"] == 5


def test_me_returns_existing_profile(client):
    db = _Session()
    db.add(Profile(id="test-user-123", plan="pro", searches_used=3, searches_limit=500))
    db.commit()
    db.close()

    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["plan"] == "pro"
    assert resp.json()["searches_used"] == 3


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
}


def test_enrich_creates_profile_and_lead(client):
    with patch("routers.enrichment.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["data"]["company_name"] == "Nubank"
    assert data["data"]["domain"] == "nubank.com.br"


def test_enrich_increments_searches_used(client):
    with patch("routers.enrichment.enrich_company", return_value=MOCK_ENRICH_RESULT):
        client.post("/api/enrich", json={"domain": "nubank.com.br"})

    resp = client.get("/api/me")
    assert resp.json()["searches_used"] == 1


def test_enrich_quota_exceeded(client):
    db = _Session()
    db.add(Profile(id="test-user-123", plan="free", searches_used=5, searches_limit=5))
    db.commit()
    db.close()

    with patch("routers.enrichment.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})
    assert resp.status_code == 402


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
    with patch("routers.enrichment.enrich_company", return_value=MOCK_ENRICH_RESULT) as mock_enrich:
        client.post("/api/enrich", json={"domain": "nubank.com.br"})
        client.post("/api/enrich", json={"domain": "nubank.com.br"})

    # enrich_company deve ter sido chamado apenas 1x (2a request é do cache)
    assert mock_enrich.call_count == 1


def test_enrich_cache_returns_cached_data(client):
    with patch("routers.enrichment.enrich_company", return_value=MOCK_ENRICH_RESULT):
        r1 = client.post("/api/enrich", json={"domain": "nubank.com.br"})
        r2 = client.post("/api/enrich", json={"domain": "nubank.com.br"})

    assert r2.status_code == 200
    assert r2.json()["message"] == "Dados carregados do cache."
    assert r2.json()["data"]["domain"] == r1.json()["data"]["domain"]


def test_enrich_cache_does_not_increment_quota_twice(client):
    with patch("routers.enrichment.enrich_company", return_value=MOCK_ENRICH_RESULT):
        client.post("/api/enrich", json={"domain": "nubank.com.br"})
        client.post("/api/enrich", json={"domain": "nubank.com.br"})

    # Apenas 1 pesquisa deve ter sido contabilizada
    assert client.get("/api/me").json()["searches_used"] == 1


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


def test_landing_alias_redirects_home(client):
    resp = client.get("/landing", follow_redirects=False)
    assert resp.status_code == 308
    assert resp.headers["location"] == "/"


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
