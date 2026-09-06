"""
Atualização automática de leads antigos: um lead não revisitado há
`STALE_LEAD_DAYS` volta sozinho para a fila de enriquecimento.

O que se protege aqui: só lead ativo (relationship == LEAD) entra na fila,
ninguém entra duas vezes enquanto já tem job pendente, e o teto por rodada
não deixa a primeira execução numa base grande enfileirar tudo de uma vez.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401

from models.database import (
    Job, Lead, Profile, RELATIONSHIP_CUSTOMER, RELATIONSHIP_LEAD, utcnow,
)
from services import jobs

SEGREDO = "segredo-do-cron-para-teste"


@pytest.fixture(autouse=True)
def cron_env(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", SEGREDO)


def _auth():
    return {"Authorization": f"Bearer {SEGREDO}"}


def _lead(dias_atras: int, relationship: str = RELATIONSHIP_LEAD,
          refreshed_dias_atras=None, user_id="user-1") -> int:
    db = _Session()
    try:
        db.merge(Profile(id=user_id))
        lead = Lead(
            user_id=user_id, raw_input_domain="acme.com.br", domain="acme.com.br",
            status="enriched", relationship=relationship,
            created_at=utcnow() - timedelta(days=dias_atras),
            refreshed_at=(utcnow() - timedelta(days=refreshed_dias_atras))
                         if refreshed_dias_atras is not None else None,
        )
        db.add(lead)
        db.commit()
        return lead.id
    finally:
        db.close()


def test_lead_antigo_e_reenfileirado(clean_db):
    lead_id = _lead(dias_atras=31)
    db = _Session()
    try:
        n = jobs.enqueue_stale_refreshes(db)
        assert n == 1
        job = db.query(Job).filter(Job.kind == "refresh").one()
        assert job.payload == {"lead_id": lead_id}
        assert job.status == jobs.STATUS_QUEUED
    finally:
        db.close()


def test_lead_recente_nao_entra(clean_db):
    _lead(dias_atras=5)
    db = _Session()
    try:
        assert jobs.enqueue_stale_refreshes(db) == 0
    finally:
        db.close()


def test_lead_ja_atualizado_recentemente_nao_entra(clean_db):
    """Criado há 60 dias, mas recoletado há 2 — o que importa é a última coleta."""
    _lead(dias_atras=60, refreshed_dias_atras=2)
    db = _Session()
    try:
        assert jobs.enqueue_stale_refreshes(db) == 0
    finally:
        db.close()


def test_cliente_atual_nao_entra(clean_db):
    _lead(dias_atras=60, relationship=RELATIONSHIP_CUSTOMER)
    db = _Session()
    try:
        assert jobs.enqueue_stale_refreshes(db) == 0
    finally:
        db.close()


def test_lead_ja_na_fila_nao_e_duplicado(clean_db):
    lead_id = _lead(dias_atras=40)
    db = _Session()
    try:
        assert jobs.enqueue_stale_refreshes(db) == 1
        assert jobs.enqueue_stale_refreshes(db) == 0  # segunda chamada: já está na fila
        assert db.query(Job).filter(Job.kind == "refresh").count() == 1
    finally:
        db.close()


def test_teto_por_rodada_e_respeitado(clean_db):
    for _ in range(5):
        _lead(dias_atras=40)
    db = _Session()
    try:
        assert jobs.enqueue_stale_refreshes(db, max_leads=3) == 3
    finally:
        db.close()


def test_job_de_refresh_atualiza_a_mesma_ficha_sem_duplicar(clean_db):
    lead_id = _lead(dias_atras=40)
    db = _Session()
    try:
        jobs.enqueue_stale_refreshes(db)
    finally:
        db.close()

    resultado_novo = {**MOCK_ENRICH_RESULT, "domain": "acme.com.br",
                      "company_name": "Acme Atualizada"}
    db = _Session()
    try:
        with patch("services.enrichment_service.enrich_company", return_value=resultado_novo):
            resumo = jobs.run_pending(db)
    finally:
        db.close()

    assert resumo["done"] == 1
    db = _Session()
    try:
        assert db.query(Lead).count() == 1  # não duplicou
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        assert lead.company_name == "Acme Atualizada"
        assert lead.refreshed_at is not None
    finally:
        db.close()


def test_cron_enfileira_e_processa_na_mesma_rodada(client):
    _lead(dias_atras=40, user_id="test-user-123")
    with patch("services.enrichment_service.enrich_company",
               return_value={**MOCK_ENRICH_RESULT, "domain": "acme.com.br"}):
        resp = client.post("/api/internal/jobs/run", headers=_auth())
    assert resp.status_code == 200
    dados = resp.json()
    assert dados["leads_reenfileirados"] == 1
    assert dados["processed"] == 1
