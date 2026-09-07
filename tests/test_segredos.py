"""
Credenciais de CRM cifradas em repouso.

O que se protege aqui não é dado do produto: é a credencial de terceiro. O
`webhook_secret` assina o payload que chega no Dynamics do usuário — quem o
tiver forja um lead que o CRM dele aceita como nosso.

Os dois testes que realmente importam são os de falha: **o que sai no banco**
(porque é a cópia do banco que vaza) e **o que acontece quando a chave muda**
(porque a saída fácil ali seria enviar sem assinatura).
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import StatementError

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401
from unittest.mock import patch

from models.database import CRMConnection
from services import crypto, preflight

CHAVE = "k" * 40
OUTRA_CHAVE = "z" * 40


@pytest.fixture(autouse=True)
def chave_limpa(monkeypatch):
    """Cada teste declara a própria chave; nenhum herda a do ambiente."""
    monkeypatch.delenv("SECRETS_KEY", raising=False)
    crypto._fernet_para.cache_clear()
    yield
    crypto._fernet_para.cache_clear()


def _gravar(secret="segredo-do-hmac", token=None, provider="webhook"):
    db = _Session()
    conn = CRMConnection(
        user_id="test-user-123", provider=provider,
        webhook_url="https://hooks.exemplo.com/x",
        webhook_secret=secret, access_token=token,
    )
    db.add(conn)
    db.commit()
    db.close()


def _bruto(coluna="webhook_secret"):
    """O que está gravado de fato, sem passar pelo tipo da coluna."""
    db = _Session()
    try:
        return db.execute(text(f"SELECT {coluna} FROM crm_connections")).scalar()
    finally:
        db.close()


# ── o que sai no banco ───────────────────────────────────────────────────────

def test_segredo_nao_aparece_em_claro_no_banco(monkeypatch):
    """
    O teste central: é a cópia do banco que vaza, não a memória do processo.
    """
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")

    gravado = _bruto()
    assert gravado.startswith(crypto.PREFIXO)
    assert "segredo-do-hmac" not in gravado


def test_ida_e_volta_devolve_o_valor_original(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac", token="tok-dynamics-123")

    db = _Session()
    conn = db.query(CRMConnection).first()
    assert conn.webhook_secret == "segredo-do-hmac"
    assert conn.access_token == "tok-dynamics-123"
    db.close()


def test_url_e_account_id_ficam_em_claro():
    """
    Cifrar o destino quebraria a checagem anti-SSRF, que precisa resolver o
    host antes de enviar. Não é esquecimento.
    """
    _gravar()
    assert _bruto("webhook_url") == "https://hooks.exemplo.com/x"


def test_sem_chave_grava_em_claro_e_continua_funcionando():
    """
    Recusar a gravação quebraria desenvolvimento por uma variável que ninguém
    define na própria máquina. Quem cobra é o preflight.
    """
    _gravar(secret="visivel")
    assert _bruto() == "visivel"

    db = _Session()
    assert db.query(CRMConnection).first().webhook_secret == "visivel"
    db.close()


def test_valor_antigo_em_claro_continua_legivel_depois_da_chave(monkeypatch):
    """Ligar a variável não pode derrubar quem já tinha conexão configurada."""
    _gravar(secret="gravado-antes")
    monkeypatch.setenv("SECRETS_KEY", CHAVE)

    db = _Session()
    assert db.query(CRMConnection).first().webhook_secret == "gravado-antes"
    db.close()


# ── quando a chave muda ──────────────────────────────────────────────────────

def test_chave_trocada_devolve_marcador_e_nao_o_texto(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")

    crypto._fernet_para.cache_clear()
    monkeypatch.setenv("SECRETS_KEY", OUTRA_CHAVE)

    db = _Session()
    assert db.query(CRMConnection).first().webhook_secret == crypto.ILEGIVEL
    db.close()


def test_chave_removida_tambem_da_marcador(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")
    monkeypatch.delenv("SECRETS_KEY")

    db = _Session()
    assert db.query(CRMConnection).first().webhook_secret == crypto.ILEGIVEL
    db.close()


def test_gravar_o_marcador_e_recusado(monkeypatch):
    """
    Cifrar a falha apagaria de vez o segredo que ainda voltaria com a chave
    certa. Melhor estourar do que perder.
    """
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    # O SQLAlchemy embrulha o erro do tipo em StatementError; o que importa é
    # que a gravação não acontece.
    with pytest.raises(StatementError) as erro:
        _gravar(secret=crypto.ILEGIVEL)
    assert isinstance(erro.value.orig, ValueError)


def test_chave_curta_nao_conta_como_chave(monkeypatch):
    """SHA-256 de uma senha curta não resiste a força bruta."""
    monkeypatch.setenv("SECRETS_KEY", "curta")
    assert not crypto.chave_configurada()
    _gravar(secret="visivel")
    assert _bruto() == "visivel"


# ── o push recusa em vez de enviar sem assinatura ────────────────────────────

def _lead(client):
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        return client.post("/api/enrich", json={"domain": "nubank.com.br"}).json()["data"]


def test_push_com_segredo_ilegivel_recusa(client, monkeypatch):
    """
    A saída fácil aqui seria enviar sem assinar: o receptor que valida o HMAC
    descartaria em silêncio, e o que não valida passaria a aceitar payload de
    qualquer origem. Nos dois casos a proteção some sem ninguém notar.
    """
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")
    lead = _lead(client)

    crypto._fernet_para.cache_clear()
    monkeypatch.setenv("SECRETS_KEY", OUTRA_CHAVE)

    with patch("services.crm.webhook.requests.post") as post:
        resp = client.post(f"/api/leads/{lead['id']}/push")

    assert resp.status_code == 409
    assert not post.called, "não pode ter saído nada pela rede"


def test_push_assina_com_o_segredo_decifrado(client, monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")
    lead = _lead(client)

    with patch("services.crm.webhook.requests.post") as post:
        post.return_value.status_code = 200
        resp = client.post(f"/api/leads/{lead['id']}/push")

    assert resp.status_code == 200
    import hashlib
    import hmac as _hmac
    corpo = post.call_args.kwargs["data"]
    esperado = _hmac.new(b"segredo-do-hmac", corpo, hashlib.sha256).hexdigest()
    assert post.call_args.kwargs["headers"]["X-LeadEnricher-Signature"] == f"sha256={esperado}"


# ── a tela de configurações não quebra junto ─────────────────────────────────

def test_listagem_funciona_com_segredo_ilegivel(client, monkeypatch):
    """
    A listagem nunca devolve segredo; carregar a linha inteira faria a tela de
    Configurações morrer justamente quando alguém precisa dela para consertar.
    """
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")

    crypto._fernet_para.cache_clear()
    monkeypatch.setenv("SECRETS_KEY", OUTRA_CHAVE)

    resp = client.get("/api/crm/connections")
    assert resp.status_code == 200
    assert resp.json()[0]["webhook_configured"] is True


def test_regravar_conserta_conexao_ilegivel(client, monkeypatch):
    """É por aqui que o usuário sai do estado ruim — não pode falhar."""
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-antigo")

    crypto._fernet_para.cache_clear()
    monkeypatch.setenv("SECRETS_KEY", OUTRA_CHAVE)

    resp = client.post("/api/crm/connections", json={
        "provider": "webhook",
        "webhook_url": "https://hooks.exemplo.com/x",
        "webhook_secret": "segredo-novo",
    })
    assert resp.status_code == 200

    db = _Session()
    assert db.query(CRMConnection).first().webhook_secret == "segredo-novo"
    db.close()


def test_campo_em_branco_preserva_o_que_estava_la(client, monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")

    resp = client.post("/api/crm/connections", json={
        "provider": "webhook",
        "webhook_url": "https://hooks.exemplo.com/novo",
    })
    assert resp.status_code == 200

    db = _Session()
    conn = db.query(CRMConnection).first()
    assert conn.webhook_secret == "segredo-do-hmac"
    assert conn.webhook_url == "https://hooks.exemplo.com/novo"
    db.close()


# ── inventário e recriptografia ──────────────────────────────────────────────

def test_inventario_separa_os_tres_estados(monkeypatch):
    _gravar(secret="em-claro", provider="webhook")
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="cifrado", provider="hubspot")

    db = _Session()
    inv = crypto.inventario(db)
    db.close()
    assert inv == {"em_claro": 1, "cifrados": 1, "ilegiveis": 0}


def test_recriptografar_converte_o_que_estava_em_claro(monkeypatch):
    _gravar(secret="em-claro", token="tok-em-claro")
    monkeypatch.setenv("SECRETS_KEY", CHAVE)

    db = _Session()
    resultado = crypto.recriptografar(db)
    db.commit()
    db.close()

    assert resultado["convertidos"] == 2
    assert _bruto().startswith(crypto.PREFIXO)
    assert "em-claro" not in _bruto()

    db = _Session()
    assert db.query(CRMConnection).first().webhook_secret == "em-claro"
    db.close()


def test_recriptografar_e_idempotente(monkeypatch):
    _gravar(secret="em-claro")
    monkeypatch.setenv("SECRETS_KEY", CHAVE)

    db = _Session()
    crypto.recriptografar(db)
    db.commit()
    segunda = crypto.recriptografar(db)
    db.commit()
    db.close()

    assert segunda["convertidos"] == 0
    assert segunda["ja_cifrados"] == 1


def test_recriptografar_nao_reescreve_o_ilegivel(monkeypatch):
    """
    Reescrever com a chave nova apagaria a chance de recuperar o valor com a
    chave antiga — que é exatamente o que se faz para consertar.
    """
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")
    antes = _bruto()

    crypto._fernet_para.cache_clear()
    monkeypatch.setenv("SECRETS_KEY", OUTRA_CHAVE)

    db = _Session()
    resultado = crypto.recriptografar(db)
    db.commit()
    db.close()

    assert resultado["ilegiveis"] == 1
    assert resultado["convertidos"] == 0
    assert _bruto() == antes


def test_recriptografar_sem_chave_recusa():
    db = _Session()
    with pytest.raises(RuntimeError):
        crypto.recriptografar(db)
    db.close()


# ── o preflight cobra ────────────────────────────────────────────────────────

def _achado(rel, trecho):
    for a in rel.achados:
        if trecho.lower() in a.titulo.lower():
            return a
    return None


def test_preflight_avisa_quando_nao_ha_chave():
    db = _Session()
    rel = preflight.verificar(producao=False, db=db)
    db.close()
    assert _achado(rel, "SECRETS_KEY ausente")


def test_preflight_agrava_quando_ha_segredo_real_em_claro():
    """
    Sem conexão nenhuma, a variável ausente é aviso sobre o futuro. Com
    segredo gravado, é exposição que já aconteceu.
    """
    db = _Session()
    leve = _achado(preflight.verificar(producao=False, db=db), "SECRETS_KEY ausente")
    db.close()

    _gravar(secret="segredo-do-hmac")

    db = _Session()
    grave = _achado(preflight.verificar(producao=False, db=db), "SECRETS_KEY ausente")
    db.close()

    assert leve.severidade == preflight.ATENCAO
    assert grave.severidade == preflight.PERIGOSO


def test_preflight_pede_a_recriptografia_do_que_ficou_para_tras(monkeypatch):
    _gravar(secret="em-claro")
    monkeypatch.setenv("SECRETS_KEY", CHAVE)

    db = _Session()
    rel = preflight.verificar(producao=False, db=db)
    db.close()

    achado = _achado(rel, "ainda em claro")
    assert achado and "recriptografar_segredos" in achado.como_resolver


def test_preflight_alerta_chave_trocada(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")

    crypto._fernet_para.cache_clear()
    monkeypatch.setenv("SECRETS_KEY", OUTRA_CHAVE)

    db = _Session()
    rel = preflight.verificar(producao=False, db=db)
    db.close()

    achado = _achado(rel, "não abrem com a SECRETS_KEY atual")
    assert achado and achado.severidade == preflight.PERIGOSO


def test_chave_configurada_e_sem_pendencia_nao_gera_achado(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", CHAVE)
    _gravar(secret="segredo-do-hmac")

    db = _Session()
    rel = preflight.verificar(producao=False, db=db)
    db.close()

    assert not _achado(rel, "SECRETS_KEY")
    assert not _achado(rel, "em claro")
