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

    forjado = jwt.encode({"sub": "vitima-uuid", "email": "x@y.z"}, "", algorithm="HS256")
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {forjado}"})

    assert resp.status_code == 503, "token forjado não pode ser aceito"
    assert "vitima-uuid" not in resp.text


def test_token_assinado_com_outro_segredo_e_recusado(raw_client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)

    intruso = jwt.encode({"sub": "vitima-uuid"}, "outro-segredo-qualquer", algorithm="HS256")
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {intruso}"})
    assert resp.status_code == 401


def test_token_valido_autentica(raw_client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)

    token = jwt.encode({"sub": "user-real", "email": "real@empresa.com"}, SEGREDO_VALIDO,
                       algorithm="HS256")
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "user-real"


def test_segredo_curto_demais_conta_como_ausente(raw_client, monkeypatch):
    """Placeholder do .env.example ('your-supabase-jwt-secret') não é segredo."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "your-supabase-jwt-secret")

    token = jwt.encode({"sub": "qualquer"}, "your-supabase-jwt-secret", algorithm="HS256")
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 503


def test_sem_token_nao_passa(raw_client):
    assert raw_client.get("/api/me").status_code == 403


# ── Sessão só com credencial real ────────────────────────────────────────────

def test_token_que_nao_e_jwt_e_recusado(raw_client, monkeypatch):
    """
    O modo demonstração aceitava tokens `demo-session-…` inventados pelo próprio
    navegador. Ele saiu: qualquer credencial que não seja um JWT verificável
    agora vira 401, sem atalho.
    """
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    resp = raw_client.get("/api/me", headers={"Authorization": "Bearer demo-session-abc"})
    assert resp.status_code == 401


# ── Verificação por chave pública (JWKS) ─────────────────────────────────────

def test_jwks_vazio_nao_apaga_chave_ja_conhecida(monkeypatch):
    """
    O JWKS do Supabase é servido por borda: durante uma rotação, uma borda
    responde a lista nova e a vizinha ainda responde vazio. Trocar a chave boa
    por uma lista vazia derrubaria logins válidos de forma intermitente.
    """
    from middleware import auth as auth_mod
    from tests.conftest import JWKS_REAL

    publico, _ = _par_de_chaves_ec()
    monkeypatch.setattr(auth_mod, "_jwks", JWKS_REAL)  # a busca de verdade
    auth_mod._jwks_cache["chaves"] = [publico]
    auth_mod._jwks_cache["em"] = 0.0

    class RespostaVazia:
        def raise_for_status(self): pass
        def json(self): return {"keys": []}

    monkeypatch.setattr(auth_mod.requests, "get", lambda *a, **k: RespostaVazia())
    assert auth_mod._jwks() == [publico]

    # E uma falha de rede também não descarta o que já funciona.
    def explode(*a, **k):
        raise RuntimeError("rede caiu")
    monkeypatch.setattr(auth_mod.requests, "get", explode)
    assert auth_mod._jwks(forcar=True) == [publico]


def test_chaves_vem_do_projeto_da_anon_key(monkeypatch):
    """
    Uma `SUPABASE_URL` esquecida de um projeto antigo não pode desviar a busca
    das chaves: quem define o projeto é a anon key com que o usuário logou.
    """
    from middleware import auth as auth_mod

    monkeypatch.setenv("SUPABASE_URL", "https://projeto-errado.supabase.co")
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    ref = jwt.get_unverified_claims(auth_mod.anon_key())["ref"]

    assert auth_mod.supabase_url() == f"https://{ref}.supabase.co"

def _par_de_chaves_ec():
    """Gera um par EC P-256 e devolve (jwk_publico, pem_privado)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from jose import jwk as jose_jwk

    privada = ec.generate_private_key(ec.SECP256R1())
    pem = privada.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    publico = jose_jwk.construct(privada.public_key(), "ES256").to_dict()
    publico = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in publico.items()}
    publico.update({"kid": "chave-de-teste", "alg": "ES256", "use": "sig"})
    return publico, pem


@pytest.fixture
def jwks_com_chave(monkeypatch):
    """Projeto que publica chave pública, como o Supabase faz com ECC P-256."""
    publico, pem = _par_de_chaves_ec()
    from middleware import auth as auth_mod
    monkeypatch.setattr(auth_mod, "_jwks", lambda forcar=False: [publico])
    return pem


def test_token_assinado_pela_chave_do_projeto_autentica(raw_client, monkeypatch, jwks_com_chave):
    """
    O caso que quebrou o login em produção: o Supabase passou a assinar com uma
    signing key própria (o token traz `kid`), e o servidor só sabia conferir o
    JWT Secret legado — todo login virava 401.
    """
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    token = jwt.encode(
        {"sub": "usuario-real-uuid", "email": "a@b.c", "aud": "authenticated"},
        jwks_com_chave, algorithm="ES256", headers={"kid": "chave-de-teste"},
    )
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["id"] == "usuario-real-uuid"


def test_token_de_outra_chave_com_o_mesmo_kid_nao_autentica(raw_client, monkeypatch, jwks_com_chave):
    """Assinar com chave própria e reivindicar o `kid` do projeto não pode colar."""
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    _, pem_do_atacante = _par_de_chaves_ec()
    forjado = jwt.encode(
        {"sub": "vitima-uuid"}, pem_do_atacante,
        algorithm="ES256", headers={"kid": "chave-de-teste"},
    )
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {forjado}"})

    assert resp.status_code == 401
    assert "vitima-uuid" not in resp.text


def test_kid_desconhecido_nao_cai_em_verificacao_fraca(raw_client, monkeypatch, jwks_com_chave):
    """
    `kid` que o JWKS não conhece cai no caminho legado — que sem segredo real
    responde 503, nunca "aceito".
    """
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    forjado = jwt.encode({"sub": "vitima-uuid"}, "", algorithm="HS256",
                         headers={"kid": "kid-que-nao-existe"})
    resp = raw_client.get("/api/me", headers={"Authorization": f"Bearer {forjado}"})

    assert resp.status_code == 503
    assert "vitima-uuid" not in resp.text


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
