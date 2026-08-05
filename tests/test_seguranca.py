"""
Testes das travas de segurança que não podem regredir.

Cada caso aqui corresponde a uma falha real encontrada em auditoria: se algum
deles voltar a passar "verde por acidente", o produto volta a aceitar token
forjado, a expor a documentação da API ou a servir a área logada sem defesa
de navegador.
"""
import pytest
from fastapi.testclient import TestClient
from jose import jwt

from tests.test_api import clean_db, _Session  # noqa: F401

from main import app

# Segredo com o tamanho de um JWT secret real do Supabase.
SEGREDO_VALIDO = "s" * 48


@pytest.fixture
def raw_client():
    """Cliente SEM a autenticação mockada — é a autenticação que está em teste."""
    with TestClient(app) as c:
        yield c


# ── JWT ──────────────────────────────────────────────────────────────────────

def test_token_forjado_com_segredo_vazio_nao_autentica(raw_client, monkeypatch):
    """
    A falha original: sem SUPABASE_JWT_SECRET o valor caía para "" e o HS256
    validava qualquer token assinado com "" — ou seja, qualquer pessoa
    assumiria o `sub` de qualquer usuário.
    """
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("DEMO_MODE", "0")

    forjado = jwt.encode({"sub": "vitima-uuid", "email": "x@y.z"}, "", algorithm="HS256")
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {forjado}"})

    assert resp.status_code == 503, "token forjado não pode ser aceito"
    assert "vitima-uuid" not in resp.text


def test_token_assinado_com_outro_segredo_e_recusado(raw_client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    monkeypatch.setenv("DEMO_MODE", "0")

    intruso = jwt.encode({"sub": "vitima-uuid"}, "outro-segredo-qualquer", algorithm="HS256")
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {intruso}"})
    assert resp.status_code == 401


def test_token_valido_autentica(raw_client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    monkeypatch.setenv("DEMO_MODE", "0")

    token = jwt.encode({"sub": "user-real", "email": "real@empresa.com"}, SEGREDO_VALIDO,
                       algorithm="HS256")
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "user-real"


def test_segredo_curto_demais_conta_como_ausente(raw_client, monkeypatch):
    """Placeholder do .env.example ('your-supabase-jwt-secret') não é segredo."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "your-supabase-jwt-secret")
    monkeypatch.setenv("DEMO_MODE", "0")

    token = jwt.encode({"sub": "qualquer"}, "your-supabase-jwt-secret", algorithm="HS256")
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 503


def test_sem_token_nao_passa(raw_client):
    assert raw_client.get("/api/me").status_code == 403


# ── Modo demonstração ────────────────────────────────────────────────────────

def test_sessao_demo_funciona_quando_ligada(raw_client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    resp = raw_client.get("/api/me", headers={"Authorization": "Bearer demo-session-abc123"})
    assert resp.status_code == 200
    dados = resp.json()
    assert dados["id"].startswith("demo-")
    assert dados["plan"] == "pro"          # demo explora o produto inteiro


def test_sessoes_demo_nao_compartilham_dados(raw_client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    a = raw_client.get("/api/me", headers={"Authorization": "Bearer demo-session-aaa"}).json()
    b = raw_client.get("/api/me", headers={"Authorization": "Bearer demo-session-bbb"}).json()
    assert a["id"] != b["id"]


def test_demo_desligado_recusa_token_demo(raw_client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    resp = raw_client.get("/api/me", headers={"Authorization": "Bearer demo-session-abc"})
    assert resp.status_code == 401


# ── Cabeçalhos de navegador ──────────────────────────────────────────────────

def test_cabecalhos_de_seguranca_em_toda_resposta(raw_client):
    resp = raw_client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "strict-origin" in resp.headers["Referrer-Policy"]


def test_area_logada_nao_e_indexavel(raw_client):
    resp = raw_client.get("/app")
    assert "noindex" in resp.headers.get("X-Robots-Tag", "")


def test_app_nao_carrega_script_de_cdn_externo(raw_client):
    """
    O SDK do Supabase enxerga o token da sessão: servido por CDN de terceiro,
    um comprometimento lá vira roubo de sessão aqui.
    """
    html = raw_client.get("/app").text
    assert "cdn.jsdelivr.net" not in html
    assert "/static/vendor/supabase.js" in html


# ── Saúde do schema ──────────────────────────────────────────────────────────

def test_health_reporta_schema(raw_client):
    dados = raw_client.get("/health").json()
    assert dados["status"] == "ok"
    assert dados["schema_ok"] is True
