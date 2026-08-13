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


def test_credencial_que_nao_e_jwt_e_reportada_como_ilegivel(raw_client, monkeypatch):
    """
    Sem o modo demonstração, um token inventado não tem tratamento especial: o
    diagnóstico precisa dizer o que ele é (texto que não é JWT), não deixar a
    pessoa procurando erro de chave.
    """
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SEGREDO_VALIDO)
    v = _veredito(raw_client.get(ROTA, headers=_com("nao-e-um-jwt")))
    assert v["situacao"] == "token_ilegivel"


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
    As constantes do projeto são editadas à mão. Trocou a anon key do código
    para outro projeto e esqueceu do JWKS? Então a chave gravada aqui não é
    dele, e oferecê-la só produziria um `kid` que nunca casa.
    """
    outra = jwt.encode({"iss": "supabase", "ref": "projetodiferente", "role": "anon"},
                       "qualquer", algorithm="HS256")
    monkeypatch.setattr(auth_mod, "SUPABASE_ANON_KEY_PADRAO", outra)
    assert auth_mod._jwks_embutido() == []


# ── Um projeto só, e ele está no código ──────────────────────────────────────
# O bug que fechou este ciclo: variável de um projeto antigo no ambiente do
# deploy vencia o projeto do código. O frontend logava num projeto e o servidor
# verificava contra outro — login perfeito no Google, 401 no /api/me, e a tela
# acusando "projeto diferente" com o código todo certo.

def _anon_de(ref: str) -> str:
    return jwt.encode({"iss": "supabase", "ref": ref, "role": "anon"},
                      "qualquer", algorithm="HS256")


@pytest.fixture(autouse=True)
def _sem_variaveis_ignoradas_de_outro_teste():
    auth_mod._env_ignoradas.clear()
    yield
    auth_mod._env_ignoradas.clear()


def test_anon_key_de_outro_projeto_no_ambiente_e_ignorada(monkeypatch):
    monkeypatch.setenv("SUPABASE_ANON_KEY", _anon_de("projetoantigo"))

    assert auth_mod.anon_key() == auth_mod.SUPABASE_ANON_KEY_PADRAO
    assert auth_mod.supabase_url() == f"https://{auth_mod.PROJETO_REF}.supabase.co"
    # E a chave pública do projeto continua de pé: o login segue funcionando.
    assert auth_mod._jwks_embutido()
    assert auth_mod.variaveis_de_outro_projeto()["SUPABASE_ANON_KEY"] == "projetoantigo"


def test_anon_key_do_mesmo_projeto_no_ambiente_continua_valendo(monkeypatch):
    """Rotacionar a chave pelo ambiente, sim — trocar de projeto, não."""
    rotacionada = _anon_de(auth_mod.PROJETO_REF)
    monkeypatch.setenv("SUPABASE_ANON_KEY", rotacionada)

    assert auth_mod.anon_key() == rotacionada
    assert auth_mod.variaveis_de_outro_projeto() == {}


def test_supabase_url_de_outro_projeto_no_ambiente_e_ignorada(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://projetoantigo.supabase.co")

    assert auth_mod.supabase_url() == f"https://{auth_mod.PROJETO_REF}.supabase.co"
    assert "SUPABASE_URL" in auth_mod.variaveis_de_outro_projeto()


def test_diagnostico_nomeia_a_variavel_que_precisa_ser_apagada(raw_client, monkeypatch):
    """Ignorar em silêncio resolveria o login e deixaria a armadilha no painel:
    a variável continua lá dizendo que o projeto é outro."""
    monkeypatch.setenv("SUPABASE_ANON_KEY", _anon_de("projetoantigo"))
    ignoradas = raw_client.get(ROTA).json()["projeto"]["variaveis_ignoradas"]
    assert ignoradas == {"SUPABASE_ANON_KEY": "projetoantigo"}


def test_front_e_back_apontam_para_o_mesmo_projeto():
    """
    A trava: o navegador loga no projeto de `static/js/app.js` e o servidor
    verifica no projeto de `middleware/auth.py`. Se os dois divergirem, todo
    login real vira 401 — e nenhum teste de unidade pega isso, porque cada lado
    está certo sozinho.
    """
    from pathlib import Path
    import re

    app_js = (Path(__file__).resolve().parents[1] / "static/js/app.js").read_text(
        encoding="utf-8")
    url = re.search(r"_SB_URL\s*=\s*'([^']+)'", app_js).group(1)
    anon = re.search(r"_SB_ANON\s*=\s*'([^']+)'", app_js).group(1)

    assert url == f"https://{auth_mod.PROJETO_REF}.supabase.co"
    assert anon == auth_mod.SUPABASE_ANON_KEY_PADRAO
    assert auth_mod._ref_da_anon(anon) == auth_mod.PROJETO_REF
