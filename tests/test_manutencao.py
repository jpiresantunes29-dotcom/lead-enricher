"""
Testes das rotinas de manutenção: o worker da fila e a proteção das rotas
internas.

Elas rodam sem ninguém olhando, então o que importa é o que elas NÃO fazem:
não rodar sem segredo e não aceitar segredo errado. Um endpoint de manutenção
aberto é um endpoint que qualquer um usa para gastar o tempo de função do
deploy.
"""
import pytest
from unittest.mock import patch

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401

from models.database import Job

SEGREDO = "segredo-do-cron-para-teste"


@pytest.fixture(autouse=True)
def cron_env(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", SEGREDO)


def _auth():
    return {"Authorization": f"Bearer {SEGREDO}"}


# ── Proteção das rotas internas ──────────────────────────────────────────────

def test_rota_interna_exige_segredo(client):
    assert client.post("/api/internal/jobs/run").status_code == 401


def test_rota_interna_recusa_segredo_errado(client):
    resp = client.post("/api/internal/jobs/run",
                       headers={"Authorization": "Bearer chute"})
    assert resp.status_code == 401


def test_rota_interna_sem_segredo_configurado_responde_503(client, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.post("/api/internal/jobs/run", headers=_auth()).status_code == 503


# ── Worker da fila ───────────────────────────────────────────────────────────

def test_worker_processa_a_fila_de_qualquer_usuario(client):
    batch_id = client.post("/api/batches", json={"domains": ["nubank.com.br"]}).json()["batch_id"]

    with patch("services.enrichment_service.enrich_company",
               return_value={**MOCK_ENRICH_RESULT, "domain": "nubank.com.br"}):
        resp = client.post("/api/internal/jobs/run", headers=_auth())

    assert resp.status_code == 200
    assert resp.json()["processed"] == 1

    db = _Session()
    try:
        assert db.query(Job).filter(Job.batch_id == batch_id).first().status == "done"
    finally:
        db.close()
