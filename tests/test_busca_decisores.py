"""
Busca de decisores (POST /api/decisores) — a rota que aciona decision_finder
para um lead já enriquecido e grava os resultados como DecisionMaker.

A busca em si (motores de busca, LinkedIn, banco global) é mockada: o que se
testa aqui é o contrato da rota — validação, isolamento por usuário, e o que
é persistido a partir do que decision_finder devolve.
"""
from unittest.mock import patch

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401

from models.database import DecisionMaker


def _make_lead(client) -> dict:
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})
    return resp.json()["data"]


_RESULTADO_MOCK = [{
    "name": "Ana Souza",
    "title_searched": "CTO",
    "title_found": "Chief Technology Officer",
    "snippet": "Ana Souza - CTO na Nubank",
    "linkedin_url": "https://www.linkedin.com/in/ana-souza",
    "probable_emails": [{"email": "ana@nubank.com.br", "status": "valid", "confidence": 97}],
    "match_confidence": "high",
    "phone": None,
}]


def test_busca_decisores_grava_e_devolve_os_encontrados(client):
    lead = _make_lead(client)

    with patch("routers.enrichment.find_decision_makers", return_value=_RESULTADO_MOCK) as mock:
        resp = client.post("/api/decisores", json={"lead_id": lead["id"], "roles": ["CTO"]})

    assert resp.status_code == 200
    dados = resp.json()
    assert dados["success"] is True
    assert len(dados["decisores"]) == 1
    assert dados["decisores"][0]["name"] == "Ana Souza"
    assert dados["decisores"][0]["probable_emails"][0]["email"] == "ana@nubank.com.br"

    mock.assert_called_once()
    assert mock.call_args.kwargs["roles"] == ["CTO"]
    assert mock.call_args.kwargs["domain"] == lead["domain"]

    db = _Session()
    try:
        salvos = db.query(DecisionMaker).filter(DecisionMaker.lead_id == lead["id"]).all()
        assert len(salvos) == 1
        assert salvos[0].name == "Ana Souza"
    finally:
        db.close()


def test_busca_decisores_sem_resultado_nao_falha(client):
    lead = _make_lead(client)

    with patch("routers.enrichment.find_decision_makers", return_value=[]):
        resp = client.post("/api/decisores", json={"lead_id": lead["id"], "roles": ["CTO"]})

    assert resp.status_code == 200
    dados = resp.json()
    assert dados["decisores"] == []
    assert "nenhum" in dados["message"].lower()


def test_busca_decisores_sem_cargo_e_recusada(client):
    lead = _make_lead(client)
    resp = client.post("/api/decisores", json={"lead_id": lead["id"], "roles": []})
    assert resp.status_code == 422


def test_busca_decisores_lead_inexistente_e_404(client):
    resp = client.post("/api/decisores", json={"lead_id": 999999, "roles": ["CTO"]})
    assert resp.status_code == 404


def test_busca_decisores_de_outro_usuario_e_404(client):
    db = _Session()
    try:
        from models.database import Lead
        outro = Lead(user_id="outro-usuario", raw_input_domain="secreto.com.br",
                     domain="secreto.com.br", status="enriched")
        db.add(outro)
        db.commit()
        outro_id = outro.id
    finally:
        db.close()

    resp = client.post("/api/decisores", json={"lead_id": outro_id, "roles": ["CTO"]})
    assert resp.status_code == 404


def test_busca_decisores_erro_interno_vira_502(client):
    lead = _make_lead(client)
    with patch("routers.enrichment.find_decision_makers", side_effect=RuntimeError("motor de busca fora do ar")):
        resp = client.post("/api/decisores", json={"lead_id": lead["id"], "roles": ["CTO"]})
    assert resp.status_code == 500
