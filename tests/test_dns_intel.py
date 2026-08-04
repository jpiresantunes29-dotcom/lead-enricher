"""
Testes do relatório DNS completo (services/dns_intel.py) e da rota que o
painel usa. Nenhum toca a rede: os parsers recebem registro real copiado de
domínio em produção, e a coleta é mockada na rota.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models.database import Base, get_db, Lead
from main import app
from services.dns_intel import (
    FULL_REPORT_VERSION, _dkim_key_bits, _format_cnpj, classify_txt,
    parse_dkim, parse_dmarc, parse_spf,
)


# ── SPF ───────────────────────────────────────────────────────────────────────

def test_spf_quebra_mecanismos_e_conta_lookups():
    spf = parse_spf(
        "v=spf1 include:spf.skymail.net.br include:_spf.rdstation.com.br "
        "ip4:200.147.0.0/24 -all"
    )
    tipos = [m["type"] for m in spf["mechanisms"]]
    assert tipos == ["include", "include", "ip4"]
    assert spf["mechanisms"][2]["value"] == "200.147.0.0/24"
    # ip4 não gasta consulta DNS; os dois includes gastam
    assert spf["lookups"] == 2
    assert spf["all"] == "-all"
    assert spf["policy"] == "fail"
    assert spf["over_limit"] is False


def test_spf_acima_de_dez_lookups_e_sinalizado():
    """Acima de 10 consultas o SPF é permerror — o domínio acha que protege."""
    spf = parse_spf("v=spf1 " + " ".join(f"include:s{i}.exemplo.com" for i in range(11)) + " ~all")
    assert spf["lookups"] == 11
    assert spf["over_limit"] is True
    assert spf["policy"] == "softfail"


def test_spf_com_redirect_usa_igual_e_nao_dois_pontos():
    spf = parse_spf("v=spf1 redirect=_spf.exemplo.com")
    assert spf["mechanisms"][0]["type"] == "redirect"
    assert spf["mechanisms"][0]["value"] == "_spf.exemplo.com"
    assert spf["lookups"] == 1
    assert spf["all"] is None


def test_spf_ausente_volta_none():
    assert parse_spf(None) is None
    assert parse_spf("") is None


# ── DMARC ─────────────────────────────────────────────────────────────────────

def test_dmarc_le_todas_as_tags():
    dmarc = parse_dmarc(
        "v=DMARC1; p=reject; sp=quarantine; pct=100; "
        "rua=mailto:dmarc@exemplo.com; adkim=s; aspf=r"
    )
    assert dmarc["policy"] == "reject"
    assert dmarc["subdomain_policy"] == "quarantine"
    assert dmarc["percent"] == "100"
    assert dmarc["rua"] == "mailto:dmarc@exemplo.com"
    assert dmarc["alignment_dkim"] == "s"
    assert dmarc["enforced"] is True
    assert dmarc["tags"]["v"] == "DMARC1"


def test_dmarc_p_none_nao_conta_como_protegido():
    dmarc = parse_dmarc("v=DMARC1; p=none;")
    assert dmarc["enforced"] is False
    assert "monitora" in dmarc["policy_label"].lower()


# ── DKIM ──────────────────────────────────────────────────────────────────────

def test_dkim_estima_tamanho_da_chave():
    # SubjectPublicKeyInfo de RSA-2048 tem 294 bytes de DER; de RSA-1024, 162.
    import base64
    assert _dkim_key_bits(base64.b64encode(b"x" * 294).decode()) == 2048
    assert _dkim_key_bits(base64.b64encode(b"x" * 162).decode()) == 1024


def test_dkim_marca_chave_fraca():
    import base64
    fraca = base64.b64encode(b"x" * 162).decode()
    registro = parse_dkim("s1", f"v=DKIM1; k=rsa; p={fraca}")
    assert registro["key_type"] == "rsa"
    assert registro["key_bits"] == 1024
    assert registro["weak_key"] is True
    assert registro["revoked"] is False


def test_dkim_com_p_vazio_e_chave_revogada():
    registro = parse_dkim("selector1", "v=DKIM1; k=rsa; p=")
    assert registro["revoked"] is True


# ── TXT ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("txt,esperado", [
    ("v=spf1 include:_spf.google.com ~all", "spf"),
    ("v=DMARC1; p=reject", "dmarc"),
    ("google-site-verification=abc123", "verify_google"),
    ("atlassian-domain-verification=xyz", "atlassian"),
    ("docusign=6c1a1f0e", "docusign"),
    ("MS=ms12345678", "verify_microsoft"),
])
def test_txt_classificado_por_assinatura(txt, esperado):
    kind, label = classify_txt(txt)
    assert kind == esperado
    assert label


def test_txt_desconhecido_nao_inventa_rotulo():
    assert classify_txt("qualquer coisa sem padrão") == (None, None)


# ── RDAP ──────────────────────────────────────────────────────────────────────

def test_handle_do_registro_br_vira_cnpj_formatado():
    assert _format_cnpj("18236120000158") == "18.236.120/0001-58"
    # handle de gTLD não é CNPJ e não pode virar um
    assert _format_cnpj("78126225_DOMAIN_COM-VRSN") is None
    assert _format_cnpj(None) is None


# ── rota /api/leads/{id}/dns ──────────────────────────────────────────────────

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
Base.metadata.create_all(bind=_engine)


def _override_get_db():
    db = _Session()
    try:
        yield db
    finally:
        db.close()


from routers.dns_intel import limiter as _dns_limiter  # noqa: E402
_dns_limiter.enabled = False

FAKE_USER = {"sub": "test-user-dns", "email": "dns@example.com"}
MOCK_REPORT = {
    "version": FULL_REPORT_VERSION,
    "domain": "nubank.com.br",
    "summary": {"mx_host": "aspmx.l.google.com"},
    "records": {"mx": [{"priority": 1, "host": "aspmx.l.google.com"}]},
    "hosts": [],
}


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def client():
    """
    O override do banco vale só enquanto este teste roda: outros módulos da
    suíte apontam `get_db` para o banco em memória deles, e trocar isso no
    nível do módulo faria os testes vizinhos escreverem no banco errado.
    """
    from middleware.auth import get_current_user
    anterior = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)
    if anterior is not None:
        app.dependency_overrides[get_db] = anterior
    else:
        app.dependency_overrides.pop(get_db, None)


def _cria_lead(user_id=FAKE_USER["sub"], dns_report=None):
    db = _Session()
    lead = Lead(user_id=user_id, raw_input_domain="nubank.com.br",
                domain="nubank.com.br", status="enriched", dns_report=dns_report)
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()
    return lead_id


def test_relatorio_e_coletado_e_guardado_no_lead(client):
    lead_id = _cria_lead(dns_report={"mx": [], "spf": "v=spf1 ~all"})
    with patch("routers.dns_intel.get_full_report", return_value=MOCK_REPORT) as coleta:
        resp = client.get(f"/api/leads/{lead_id}/dns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["report"]["summary"]["mx_host"] == "aspmx.l.google.com"
    coleta.assert_called_once_with("nubank.com.br")

    # guardado sem destruir o que o enriquecimento já tinha posto lá
    db = _Session()
    salvo = db.query(Lead).get(lead_id)
    assert salvo.dns_report["full"]["domain"] == "nubank.com.br"
    assert salvo.dns_report["spf"] == "v=spf1 ~all"
    db.close()


def test_segunda_abertura_vem_do_cache_sem_coletar_de_novo(client):
    lead_id = _cria_lead(dns_report={"full": MOCK_REPORT})
    with patch("routers.dns_intel.get_full_report") as coleta:
        resp = client.get(f"/api/leads/{lead_id}/dns")
    assert resp.json()["cached"] is True
    coleta.assert_not_called()


def test_refresh_ignora_o_relatorio_guardado(client):
    lead_id = _cria_lead(dns_report={"full": MOCK_REPORT})
    with patch("routers.dns_intel.get_full_report", return_value=MOCK_REPORT) as coleta:
        resp = client.get(f"/api/leads/{lead_id}/dns?refresh=true")
    assert resp.json()["cached"] is False
    coleta.assert_called_once()


def test_relatorio_de_versao_antiga_e_recoletado(client):
    """Correção na coleta precisa chegar na próxima abertura, não ficar presa."""
    antigo = {**MOCK_REPORT, "version": FULL_REPORT_VERSION - 1}
    lead_id = _cria_lead(dns_report={"full": antigo})
    with patch("routers.dns_intel.get_full_report", return_value=MOCK_REPORT) as coleta:
        resp = client.get(f"/api/leads/{lead_id}/dns")
    assert resp.json()["cached"] is False
    coleta.assert_called_once()


def test_lead_de_outro_usuario_da_404(client):
    lead_id = _cria_lead(user_id="outro-usuario")
    with patch("routers.dns_intel.get_full_report", return_value=MOCK_REPORT):
        resp = client.get(f"/api/leads/{lead_id}/dns")
    assert resp.status_code == 404


def test_listagem_nao_carrega_o_relatorio_completo(client):
    """Cem leads com relatório completo seriam megabytes que a tabela não usa."""
    _cria_lead(dns_report={"full": MOCK_REPORT, "spf": "v=spf1 ~all"})
    resp = client.get("/api/leads")
    assert resp.status_code == 200
    assert "dns_report" not in resp.json()[0]
