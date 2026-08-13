"""
O diagnóstico de login: o servidor dizendo POR QUE recusou.

Existe porque as cinco causas de um 401 no login produzem a mesma tela, e a
resposta só ficava no log de uma função serverless — que ninguém abre no meio
de um problema. Cada teste aqui fixa uma dessas causas na frase que a pessoa
vai ler, e o último fixa a regra que não pode ser quebrada: o diagnóstico
explica a configuração sem entregar o segredo dela.
"""
import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from tests.test_api import clean_db, _Session  # noqa: F401
from tests.test_seguranca import SEGREDO_VALIDO, _par_de_chaves_ec

from main import app
from middleware import auth as auth_mod

ROTA = "/api/auth/diagnostico"


@pytest.fixture
def raw_client():
    with TestClient(app) as c:
        yield c


def _veredito(resp) -> dict:
    assert resp.status_code == 200
    return resp.json()["veredito"]


def _com(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── As causas, uma a uma ─────────────────────────────────────────────────────

def test_sem_token_diz_se_o_servidor_tem_como_verificar_login(raw_client, monkeypatch):
    """A metade da resposta que falta quando nada funciona: dá para logar aqui?"""
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    resp = raw_client.get(ROTA)
    assert _veredito(resp)["situacao"] == "servidor_sem_chave"
    assert resp.json()["chaves_publicas"]["quantidade"] == 0


def test_aponta_segredo_de_outro_projeto(raw_client, monkeypatch):
    """
    A causa mais comum: a variável do deploy é o JWT Secret de outro projeto
    Supabase. O login termina bem no Google e o app volta deslogado.
    """
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)   # não é o do projeto
    token = jwt.encode({"sub": "u1"}, "o-segredo-de-verdade-do-projeto", algorithm="HS256")

    v = _veredito(raw_client.get(ROTA, headers=_com(token)))
    assert v["situacao"] == "segredo_de_outro_projeto"
    assert "MESMO projeto" in v["como_resolver"]


def test_aponta_chave_de_assinatura_que_o_projeto_nao_publica(raw_client, monkeypatch):
    """
    Token assinado por uma signing key ausente do JWKS: material que o Supabase
    não entrega, então nenhum valor de variável resolve. A saída é trocar a
    chave do projeto para assimétrica — e é isso que a frase precisa dizer.
    """
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    publico, _ = _par_de_chaves_ec()
    monkeypatch.setattr(auth_mod, "_jwks", lambda forcar=False: [publico])

    token = jwt.encode({"sub": "u1"}, "outro", algorithm="HS256",
                       headers={"kid": "chave-que-o-jwks-nao-tem"})
    v = _veredito(raw_client.get(ROTA, headers=_com(token)))
    assert v["situacao"] == "chave_de_assinatura_nao_publicada"
    assert "ECC P-256" in v["como_resolver"]


def test_aponta_login_feito_em_outro_projeto(raw_client, monkeypatch):
    """Anon key de um projeto e login em outro: nenhuma chave valida o token."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    token = jwt.encode(
        {"sub": "u1", "iss": "https://outroprojetoqualquer.supabase.co/auth/v1"},
        "seja-la-qual-for", algorithm="HS256",
    )
    v = _veredito(raw_client.get(ROTA, headers=_com(token)))
    assert v["situacao"] == "projeto_diferente"
    assert "outroprojetoqualquer" in v["resumo"]


def test_separa_sessao_expirada_de_chave_errada(raw_client, monkeypatch):
    """São o mesmo 401 na tela e pedem ações opostas: entrar de novo x mexer na
    configuração. Confundir os dois é o que faz alguém passar horas no lugar
    errado."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    vencido = jwt.encode({"sub": "u1", "exp": int(time.time()) - 60},
                         SEGREDO_VALIDO, algorithm="HS256")
    assert _veredito(raw_client.get(ROTA, headers=_com(vencido)))["situacao"] == "expirado"


def test_reconhece_token_bom(raw_client, monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    bom = jwt.encode({"sub": "u1"}, SEGREDO_VALIDO, algorithm="HS256")
    assert _veredito(raw_client.get(ROTA, headers=_com(bom)))["situacao"] == "ok"


def test_reconhece_sessao_demo(raw_client, monkeypatch):
    """Demo não é login real: sem isto, quem está no modo demo lê um
    diagnóstico de chave e vai mexer numa configuração que está certa."""
    monkeypatch.setenv("DEMO_MODE", "1")
    v = _veredito(raw_client.get(ROTA, headers=_com("demo-session-abc")))
    assert v["situacao"] == "demo"


# ── A trava ──────────────────────────────────────────────────────────────────

def test_diagnostico_nao_entrega_o_segredo(raw_client, monkeypatch):
    """
    A rota é pública por necessidade — quem não consegue entrar precisa dela.
    Então ela pode dizer "existe", "tem 48 caracteres" e "não é o do projeto",
    e nunca o valor.
    """
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    token = jwt.encode({"sub": "u1"}, "outro", algorithm="HS256")
    resp = raw_client.get(ROTA, headers=_com(token))

    assert SEGREDO_VALIDO not in resp.text
    assert resp.json()["segredo_legado"] == {
        "configurado": True, "tamanho": len(SEGREDO_VALIDO),
        "confere_com_o_projeto": False,
    }


def test_kid_de_quem_chama_nao_volta_cru_na_resposta(raw_client, monkeypatch):
    """O `kid` vem do token, que é de quem chama: entra na mensagem sanitizado."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    token = jwt.encode({"sub": "u1"}, "outro", algorithm="HS256",
                       headers={"kid": "<script>alert(1)</script>"})
    resp = raw_client.get(ROTA, headers=_com(token))
    assert "<script>" not in resp.text
    assert resp.json()["token"]["kid"] == "(kid ilegível)"


# ── Chave pública embutida ───────────────────────────────────────────────────

def test_chave_embutida_sustenta_a_verificacao_quando_a_busca_falha(monkeypatch):
    """
    Em serverless, cada instância fria vai buscar o JWKS de novo. Um timeout
    não pode virar 401 para quem estava entrando: a chave pública do projeto
    está no código e verifica igual.
    """
    from tests.conftest import JWKS_REAL

    monkeypatch.setattr(auth_mod, "_jwks", JWKS_REAL)      # a busca de verdade
    monkeypatch.setattr(auth_mod.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout")))
    auth_mod._jwks_cache.update({"em": 0.0, "chaves": []})

    chaves = auth_mod._jwks(forcar=True)
    assert [c["kid"] for c in chaves] == [c["kid"] for c in auth_mod.JWKS_PADRAO]
    assert auth_mod._jwks_cache["origem"] == "embutida"


def test_chave_embutida_nao_vale_para_outro_projeto(monkeypatch):
    """
    Apontou a anon key para outro projeto? Então a chave gravada aqui não é
    dele, e oferecê-la só produziria um `kid` que nunca casa.
    """
    outra = jwt.encode({"iss": "supabase", "ref": "projetodiferente", "role": "anon"},
                       "qualquer", algorithm="HS256")
    monkeypatch.setenv("SUPABASE_ANON_KEY", outra)
    assert auth_mod._jwks_embutido() == []
