"""
Testes de integração dos endpoints de importação de planilha.

Reusa o banco SQLite em memória e o mock de autenticação de tests/test_api.py.
"""
import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from models.database import Lead, Profile
from tests.test_api import (  # noqa: F401  (fixture clean_db é autouse)
    FAKE_USER, _Session, clean_db,
)

# Rate limiter desligado: a suíte estoura o limite por minuto do preview
from routers.imports import limiter as _import_limiter  # noqa: E402
_import_limiter.enabled = False
from routers.enrichment import limiter as _enrich_limiter  # noqa: E402
_enrich_limiter.enabled = False


@pytest.fixture
def client():
    from middleware.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


def _upload(client, content: bytes, filename="empresas.csv"):
    return client.post(
        "/api/import/preview",
        files={"file": (filename, io.BytesIO(content), "text/csv")},
    )


CSV_OK = (
    "Domínio,Empresa,Setor\n"
    "nubank.com.br,Nubank,Fintech\n"
    "totvs.com.br,TOTVS,Tecnologia\n"
).encode("utf-8-sig")


# ── preview ───────────────────────────────────────────────────────────────────
def test_preview_mapeia_colunas_e_conta_importaveis(client):
    resp = _upload(client, CSV_OK)
    assert resp.status_code == 200
    data = resp.json()

    assert data["success"] is True
    assert data["importable"] == 2
    assert data["mapping"]["domain"] == "Domínio"
    assert len(data["rows"]) == 2


def test_preview_nao_grava_nada_no_banco(client):
    _upload(client, CSV_OK)
    db = _Session()
    assert db.query(Lead).count() == 0
    db.close()


def test_preview_marca_dominio_que_ja_esta_no_historico(client):
    db = _Session()
    db.add(Lead(user_id=FAKE_USER["sub"], raw_input_domain="nubank.com.br",
                domain="nubank.com.br", status="enriched"))
    db.commit()
    db.close()

    data = _upload(client, CSV_OK).json()
    por_dominio = {r["data"]["domain"]: r["status"] for r in data["rows"]}
    assert por_dominio["nubank.com.br"] == "duplicate_db"
    assert por_dominio["totvs.com.br"] == "ok"
    assert data["importable"] == 1


def test_preview_de_outro_usuario_nao_marca_duplicado(client):
    db = _Session()
    db.add(Lead(user_id="outro-user", raw_input_domain="nubank.com.br",
                domain="nubank.com.br", status="enriched"))
    db.commit()
    db.close()

    assert _upload(client, CSV_OK).json()["importable"] == 2


def test_preview_rejeita_planilha_sem_dominio(client):
    resp = _upload(client, "Empresa,Setor\nTOTVS,Tech\n".encode())
    assert resp.status_code == 422
    assert "domínio" in resp.json()["detail"]


def test_preview_rejeita_formato_invalido(client):
    resp = _upload(client, b"%PDF-1.4", filename="lista.pdf")
    assert resp.status_code == 422


# ── commit ────────────────────────────────────────────────────────────────────
def _commit(client, rows, skip_existing=True):
    return client.post("/api/import", json={"rows": rows, "skip_existing": skip_existing})


def test_commit_cria_leads_com_status_imported(client):
    resp = _commit(client, [
        {"domain": "nubank.com.br", "company_name": "Nubank", "sector": "Fintech"},
        {"domain": "totvs.com.br", "company_name": "TOTVS"},
    ])
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 2
    assert {lead["status"] for lead in body["leads"]} == {"imported"}

    db = _Session()
    lead = db.query(Lead).filter(Lead.domain == "nubank.com.br").one()
    assert lead.user_id == FAKE_USER["sub"]
    assert lead.company_name == "Nubank"
    assert lead.website == "https://nubank.com.br"
    assert lead.stage == "novo"
    assert lead.score is None  # scoring só depois da coleta
    db.close()


def test_commit_nao_consome_cota(client):
    db = _Session()
    db.add(Profile(id=FAKE_USER["sub"], plan="free", searches_used=0, searches_limit=5))
    db.commit()
    db.close()

    _commit(client, [{"domain": "nubank.com.br"}])

    db = _Session()
    assert db.get(Profile, FAKE_USER["sub"]).searches_used == 0
    db.close()


def test_commit_normaliza_e_deduplica_dominios(client):
    body = _commit(client, [
        {"domain": "https://www.acme.com.br/contato"},
        {"domain": "ACME.com.br"},
    ]).json()

    assert body["created"] == 1
    assert body["leads"][0]["domain"] == "acme.com.br"


def test_commit_pula_dominio_ja_existente(client):
    db = _Session()
    db.add(Lead(user_id=FAKE_USER["sub"], raw_input_domain="nubank.com.br",
                domain="nubank.com.br", status="enriched"))
    db.commit()
    db.close()

    body = _commit(client, [{"domain": "nubank.com.br"}, {"domain": "totvs.com.br"}]).json()
    assert body["created"] == 1
    assert body["skipped"] == 1


def test_commit_com_skip_existing_false_reimporta(client):
    db = _Session()
    db.add(Lead(user_id=FAKE_USER["sub"], raw_input_domain="nubank.com.br",
                domain="nubank.com.br", status="enriched"))
    db.commit()
    db.close()

    body = _commit(client, [{"domain": "nubank.com.br"}], skip_existing=False).json()
    assert body["created"] == 1


def test_commit_rejeita_lista_vazia(client):
    assert _commit(client, []).status_code == 422


def test_commit_rejeita_linhas_sem_dominio_valido(client):
    resp = _commit(client, [{"domain": "não-é-domínio"}, {"domain": ""}])
    assert resp.status_code == 422


def test_commit_acima_do_limite(client):
    rows = [{"domain": f"empresa{i}.com.br"} for i in range(501)]
    assert _commit(client, rows).status_code == 422


# ── enriquecimento do lead importado ──────────────────────────────────────────
MOCK_RESULT = {
    "raw_input_domain": "nubank.com.br",
    "domain": "nubank.com.br",
    "website": "https://nubank.com.br",
    "company_name": "Nu Pagamentos S.A.",
    "description": "Banco digital",
    "location": "São Paulo, Brasil",
    "sector": None,                      # coleta não achou — deve manter o da planilha
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


def _imported_lead(**over):
    db = _Session()
    lead = Lead(
        user_id=FAKE_USER["sub"],
        raw_input_domain="nubank.com.br",
        domain="nubank.com.br",
        company_name="Nubank (planilha)",
        sector="Fintech",
        phone="+55 11 4000-0000",
        status="imported",
    )
    for key, value in over.items():
        setattr(lead, key, value)
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()
    return lead_id


def test_enrich_lead_preenche_o_lead_importado_sem_duplicar(client):
    lead_id = _imported_lead()
    with patch("routers.enrichment.enrich_company", return_value=MOCK_RESULT):
        resp = client.post(f"/api/leads/{lead_id}/enrich")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == lead_id
    assert data["status"] == "enriched"
    assert data["company_name"] == "Nu Pagamentos S.A."   # coleta sobrescreve
    assert data["sector"] == "Fintech"                    # planilha preservada
    assert data["phone"] == "+55 11 4000-0000"

    db = _Session()
    assert db.query(Lead).count() == 1
    db.close()


def test_enrich_lead_consome_cota(client):
    db = _Session()
    db.add(Profile(id=FAKE_USER["sub"], plan="free", searches_used=0, searches_limit=5))
    db.commit()
    db.close()

    lead_id = _imported_lead()
    with patch("routers.enrichment.enrich_company", return_value=MOCK_RESULT):
        client.post(f"/api/leads/{lead_id}/enrich")

    db = _Session()
    assert db.get(Profile, FAKE_USER["sub"]).searches_used == 1
    db.close()


def test_enrich_lead_sem_cota_devolve_402(client):
    db = _Session()
    db.add(Profile(id=FAKE_USER["sub"], plan="free", searches_used=5, searches_limit=5))
    db.commit()
    db.close()

    lead_id = _imported_lead()
    resp = client.post(f"/api/leads/{lead_id}/enrich")
    assert resp.status_code == 402


def test_enrich_lead_devolve_cota_quando_a_coleta_falha(client):
    db = _Session()
    db.add(Profile(id=FAKE_USER["sub"], plan="free", searches_used=0, searches_limit=5))
    db.commit()
    db.close()

    lead_id = _imported_lead()
    with patch("routers.enrichment.enrich_company", side_effect=RuntimeError("timeout")):
        resp = client.post(f"/api/leads/{lead_id}/enrich")

    assert resp.status_code == 500
    db = _Session()
    assert db.get(Profile, FAKE_USER["sub"]).searches_used == 0
    db.close()


def test_enrich_lead_de_outro_usuario_da_404(client):
    db = _Session()
    lead = Lead(user_id="outro-user", raw_input_domain="x.com", domain="x.com", status="imported")
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    assert client.post(f"/api/leads/{lead_id}/enrich").status_code == 404


def test_busca_manual_de_dominio_importado_preenche_o_mesmo_lead(client):
    """Sem isso, buscar um domínio que veio da planilha criaria um lead duplicado."""
    lead_id = _imported_lead()
    with patch("routers.enrichment.enrich_company", return_value=MOCK_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})

    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == lead_id
    db = _Session()
    assert db.query(Lead).count() == 1
    db.close()


def test_lead_importado_nao_e_servido_como_cache(client):
    """O lead importado está vazio — servir como cache devolveria ficha em branco."""
    lead_id = _imported_lead()
    with patch("routers.enrichment.enrich_company", return_value=MOCK_RESULT) as mock:
        client.post("/api/enrich", json={"domain": "nubank.com.br"})
    assert mock.called
    db = _Session()
    assert db.get(Lead, lead_id).status == "enriched"
    db.close()


# ── modelo de planilha ────────────────────────────────────────────────────────
@pytest.mark.parametrize("fmt", ["xlsx", "csv"])
def test_download_do_modelo(client, fmt):
    resp = client.get(f"/api/import/template?format={fmt}")
    assert resp.status_code == 200
    assert resp.content
    assert "modelo_importacao" in resp.headers["content-disposition"]
