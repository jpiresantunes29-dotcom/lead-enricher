"""
Conferência de prontidão e guardas de boot.

O que se testa aqui não é uma funcionalidade do produto — é o que impede
alguém de ligar o sistema pela metade e descobrir pelo silêncio. Cada achado
precisa dizer **a consequência**, porque uma checagem que só diz "faltando"
faz a pessoa preencher a variável sem entender o risco que correu.
"""
import pytest

from tests.test_api import client, clean_db  # noqa: F401

from services import preflight


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    """Parte de um servidor sem nenhuma integração configurada."""
    for var in ("WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN",
                "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_TEMPLATE_NAME", "ANTHROPIC_API_KEY",
                "RESEND_API_KEY", "SITE_URL", "CRON_SECRET",
                "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("DEMO_MODE", "0")


def _titulos(rel, severidade=None):
    return [a.titulo for a in rel.achados
            if severidade is None or a.severidade == severidade]


def _achado(rel, trecho):
    for a in rel.achados:
        if trecho.lower() in a.titulo.lower():
            return a
    return None


# ── A forma do relatório ─────────────────────────────────────────────────────

def test_todo_achado_diz_a_consequencia_e_como_resolver():
    """
    "Faltando" não é informação suficiente: a pessoa preenche a variável e
    segue sem entender o que quase aconteceu.
    """
    rel = preflight.verificar(producao=True)
    assert rel.achados
    for a in rel.achados:
        assert a.consequencia.strip(), f"{a.titulo} sem consequência"
        assert a.como_resolver.strip(), f"{a.titulo} sem como resolver"
        assert a.severidade in (preflight.IMPEDE, preflight.PERIGOSO, preflight.ATENCAO)


def test_pronto_olha_so_para_o_que_impede():
    """`perigoso` é decisão de quem liga; `impede` não é negociável."""
    rel = preflight.Relatorio(producao=True, achados=[
        preflight.Achado(preflight.PERIGOSO, "t", "c", "r"),
        preflight.Achado(preflight.ATENCAO, "t", "c", "r"),
    ])
    assert rel.pronto is True

    rel.achados.append(preflight.Achado(preflight.IMPEDE, "t", "c", "r"))
    assert rel.pronto is False
    assert len(rel.bloqueios) == 1


def test_o_dicionario_ordena_do_mais_grave_para_o_menos():
    ordem = [a["severidade"] for a in preflight.verificar(producao=True).como_dict()["achados"]]
    assert ordem == sorted(ordem, key=lambda s: preflight._ORDEM[s])


# ── Fundação ─────────────────────────────────────────────────────────────────

def test_falta_de_cron_secret_impede():
    a = _achado(preflight.verificar(producao=True), "CRON_SECRET")
    assert a.severidade == preflight.IMPEDE
    assert "madrugada" in a.consequencia


def test_jwt_ausente_impede(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "curto")
    a = _achado(preflight.verificar(producao=True), "SUPABASE_JWT_SECRET")
    assert a.severidade == preflight.IMPEDE


def test_jwt_de_outro_projeto_impede():
    """
    O caso que passou despercebido: segredo presente, longo, e de outro
    projeto Supabase. O boot passa, o login pelo Google termina bem e o app
    volta deslogado — sem erro em lugar nenhum. A conferência tem que gritar.
    """
    # A fixture já deixa um segredo de 40 caracteres que não é o do projeto.
    a = _achado(preflight.verificar(producao=True), "outro projeto Supabase")
    assert a.severidade == preflight.IMPEDE
    assert "401" in a.consequencia


def test_jwt_do_projeto_certo_nao_vira_achado(monkeypatch):
    """Com o segredo certo, a conferência precisa ficar quieta."""
    from middleware import auth as auth_mod

    monkeypatch.setenv("SUPABASE_JWT_SECRET", "segredo-de-teste-com-tamanho-suficiente")
    # Chave "anon" assinada com esse segredo — o gabarito que a checagem usa.
    from jose import jwt as jose_jwt
    anon = jose_jwt.encode(
        {"iss": "supabase", "ref": "projeto-x", "role": "anon"},
        "segredo-de-teste-com-tamanho-suficiente",
        algorithm="HS256",
    )
    monkeypatch.setenv("SUPABASE_ANON_KEY", anon)
    assert auth_mod.secret_confere_com_projeto() is True
    assert _achado(preflight.verificar(producao=True), "outro projeto Supabase") is None


def test_producao_com_sqlite_impede(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./lead_enricher.db")
    a = _achado(preflight.verificar(producao=True), "SQLite")
    assert a.severidade == preflight.IMPEDE
    assert "efêmero" in a.consequencia


def test_sqlite_em_desenvolvimento_nao_e_problema(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./lead_enricher.db")
    assert _achado(preflight.verificar(producao=False), "SQLite") is None


def test_demo_aberto_em_producao_e_perigoso(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    a = _achado(preflight.verificar(producao=True), "DEMO_MODE")
    assert a.severidade == preflight.PERIGOSO


def test_demo_em_desenvolvimento_e_silencioso(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    assert _achado(preflight.verificar(producao=False), "DEMO_MODE") is None


# ── WhatsApp: o caso perigoso é meio ligado ──────────────────────────────────

def _envio_ligado(monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "primeiro_contato")


def test_whatsapp_totalmente_desligado_e_so_um_aviso():
    a = _achado(preflight.verificar(producao=True), "WhatsApp desligado")
    assert a.severidade == preflight.ATENCAO


def test_envio_ligado_sem_assinatura_e_perigoso(monkeypatch):
    """
    O convite sai e é cobrado; a resposta do lead é recusada no webhook por
    falta de assinatura. Ninguém fica sabendo, e não aparece erro em lugar
    nenhum — é o modo de falha mais caro do produto.
    """
    _envio_ligado(monkeypatch)
    a = _achado(preflight.verificar(producao=True), "não valida o que recebe")
    assert a.severidade == preflight.PERIGOSO
    assert "cobrado" in a.consequencia


def test_recebimento_ligado_sem_envio_e_so_um_aviso(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "segredo")
    a = _achado(preflight.verificar(producao=True), "não envia")
    assert a.severidade == preflight.ATENCAO


def test_whatsapp_completo_nao_gera_achado_de_meia_configuracao(monkeypatch):
    _envio_ligado(monkeypatch)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "segredo")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "token-de-verificacao")
    rel = preflight.verificar(producao=True)
    assert _achado(rel, "não valida o que recebe") is None
    assert _achado(rel, "WhatsApp desligado") is None


def test_envio_sem_template_e_perigoso(monkeypatch):
    _envio_ligado(monkeypatch)
    monkeypatch.delenv("WHATSAPP_TEMPLATE_NAME", raising=False)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "segredo")
    a = _achado(preflight.verificar(producao=True), "template de abertura")
    assert a.severidade == preflight.PERIGOSO


def test_whatsapp_ligado_sem_ia_e_apenas_aviso(monkeypatch):
    """Sem IA nada se perde: toda resposta vira pendência humana."""
    _envio_ligado(monkeypatch)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "segredo")
    a = _achado(preflight.verificar(producao=True), "IA desligada")
    assert a.severidade == preflight.ATENCAO
    assert "pendência humana" in a.consequencia


# ── LGPD ─────────────────────────────────────────────────────────────────────

def test_email_desligado_e_perigoso_em_producao():
    """
    Sem e-mail, o pedido de remoção fica pendente para sempre: o titular nunca
    recebe o link e o bloqueio nunca se confirma.
    """
    a = _achado(preflight.verificar(producao=True), "E-mail transacional")
    assert a.severidade == preflight.PERIGOSO
    assert "titular" in a.consequencia


def test_email_desligado_em_desenvolvimento_e_so_aviso():
    a = _achado(preflight.verificar(producao=False), "E-mail transacional")
    assert a.severidade == preflight.ATENCAO


# ── A rota ───────────────────────────────────────────────────────────────────

def test_rota_de_prontidao_exige_o_segredo_do_cron(client, monkeypatch):
    """A resposta é um mapa do que está configurado — não é para o público."""
    monkeypatch.setenv("CRON_SECRET", "segredo-do-cron")
    assert client.get("/api/internal/preflight").status_code == 401
    assert client.get("/api/internal/preflight",
                      headers={"Authorization": "Bearer errado"}).status_code == 401

    resp = client.get("/api/internal/preflight",
                      headers={"Authorization": "Bearer segredo-do-cron"})
    assert resp.status_code == 200
    assert "achados" in resp.json()


def test_sem_cron_secret_a_rota_nem_existe_para_o_mundo(client):
    """Sem segredo configurado, as rotas internas respondem 503 — não 200."""
    assert client.get("/api/internal/preflight").status_code == 503


# ── Guarda de boot ───────────────────────────────────────────────────────────

def test_boot_em_producao_recusa_whatsapp_meio_configurado(monkeypatch):
    """
    A guarda que existe para ninguém descobrir o problema pelo silêncio: em
    produção, meia configuração impede o boot, e a Vercel mostra o erro no
    deploy em vez de o lead sumir sem resposta.
    """
    import main
    _envio_ligado(monkeypatch)
    monkeypatch.setattr(main, "IS_PRODUCTION", True)

    with pytest.raises(RuntimeError, match="WHATSAPP_APP_SECRET"):
        main._check_whatsapp_configuration()


def test_boot_em_desenvolvimento_apenas_avisa(monkeypatch):
    import main
    _envio_ligado(monkeypatch)
    monkeypatch.setattr(main, "IS_PRODUCTION", False)
    main._check_whatsapp_configuration()   # não levanta


def test_boot_aceita_whatsapp_completo(monkeypatch):
    import main
    _envio_ligado(monkeypatch)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "segredo")
    monkeypatch.setattr(main, "IS_PRODUCTION", True)
    main._check_whatsapp_configuration()   # não levanta


def test_boot_aceita_whatsapp_totalmente_desligado(monkeypatch):
    import main
    monkeypatch.setattr(main, "IS_PRODUCTION", True)
    main._check_whatsapp_configuration()   # não levanta
