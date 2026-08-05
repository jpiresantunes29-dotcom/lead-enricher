"""
Testes do enriquecimento em lote: parsing da lista, fila, cota e as rodadas
que fazem o lote andar sem estourar o tempo da função.
"""
import pytest
from unittest.mock import patch

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401

from models.database import Job, Lead, Profile
from services import domain_list, jobs


def _resultado(domain: str) -> dict:
    """Resultado de coleta para um domínio específico."""
    return {**MOCK_ENRICH_RESULT, "domain": domain, "raw_input_domain": domain,
            "company_name": domain.split(".")[0].title()}


def _mock_enrich(domain_input: str):
    return _resultado(domain_input.strip().lower())


def _perfil(user_id="test-user-123") -> Profile:
    db = _Session()
    try:
        return db.query(Profile).filter(Profile.id == user_id).first()
    finally:
        db.close()


def _set_limite(limite: int, usadas: int = 0, user_id="test-user-123"):
    db = _Session()
    try:
        perfil = db.query(Profile).filter(Profile.id == user_id).first()
        perfil.searches_limit = limite
        perfil.searches_used = usadas
        db.commit()
    finally:
        db.close()


# ── Leitura da lista ─────────────────────────────────────────────────────────

def test_parser_aceita_uma_coluna_por_linha():
    assert domain_list.parse("nubank.com.br\nstone.com.br") == ["nubank.com.br", "stone.com.br"]


def test_parser_escolhe_a_coluna_do_site_no_csv_com_cabecalho():
    texto = "Empresa;Site;Telefone\nNubank;https://nubank.com.br/;11 4002-8922"
    assert domain_list.parse(texto) == ["nubank.com.br"]


def test_parser_ignora_repeticao_e_www():
    texto = "nubank.com.br\nNUBANK.COM.BR\nwww.nubank.com.br"
    assert domain_list.parse(texto) == ["nubank.com.br"]


def test_parser_extrai_dominio_de_email():
    assert domain_list.parse("joao@acme.com.br") == ["acme.com.br"]


def test_parser_ignora_linha_sem_dominio():
    assert domain_list.parse("Nubank\nStone Pagamentos") == []


# ── Criação do lote ──────────────────────────────────────────────────────────

def test_lote_enfileira_um_job_por_dominio(client):
    resp = client.post("/api/batches", json={"text": "nubank.com.br\nstone.com.br"})
    assert resp.status_code == 200
    dados = resp.json()
    assert dados["total"] == 2

    db = _Session()
    try:
        criados = db.query(Job).filter(Job.batch_id == dados["batch_id"]).all()
        assert [j.status for j in criados] == ["queued", "queued"]
        assert {j.payload["domain"] for j in criados} == {"nubank.com.br", "stone.com.br"}
    finally:
        db.close()


def test_lote_sem_dominio_reconhecido_e_recusado(client):
    resp = client.post("/api/batches", json={"text": "Nubank\nStone Pagamentos"})
    assert resp.status_code == 422


def test_lote_avisa_quando_nao_cabe_na_cota(client):
    client.get("/api/me")
    _set_limite(limite=5, usadas=4)
    resp = client.post("/api/batches", json={
        "domains": ["a.com.br", "b.com.br", "c.com.br"],
    })
    dados = resp.json()
    assert dados["cabe_na_quota"] is False
    assert dados["quota_restante"] == 1
    # O lote é criado mesmo assim: o resto espera a renovação do ciclo.
    assert dados["total"] == 3


def test_lote_conta_entradas_ignoradas(client):
    resp = client.post("/api/batches", json={
        "domains": ["nubank.com.br", "www.nubank.com.br", "sem-ponto", "stone.com.br"],
    })
    dados = resp.json()
    assert dados["total"] == 2
    assert dados["ignorados"] == 2


# ── Execução ─────────────────────────────────────────────────────────────────

def test_rodada_processa_a_fila_e_cria_leads(client):
    batch_id = client.post("/api/batches", json={
        "domains": ["nubank.com.br", "stone.com.br"],
    }).json()["batch_id"]

    with patch("services.enrichment_service.enrich_company", side_effect=_mock_enrich):
        resp = client.post(f"/api/batches/{batch_id}/run")

    assert resp.status_code == 200
    dados = resp.json()
    assert dados["processed"] == 2
    assert dados["remaining"] == 0
    assert dados["progresso"]["finalizado"] is True

    leads = client.get("/api/leads").json()
    assert {l["domain"] for l in leads} == {"nubank.com.br", "stone.com.br"}


def test_rodada_respeita_o_orcamento_de_tempo(client):
    """
    Cada rodada precisa terminar bem antes do limite da função. Com orçamento
    curto, a rodada devolve o que deu e deixa o resto na fila.
    """
    batch_id = client.post("/api/batches", json={
        "domains": [f"empresa{i}.com.br" for i in range(5)],
    }).json()["batch_id"]

    db = _Session()
    try:
        with patch("services.enrichment_service.enrich_company", side_effect=_mock_enrich):
            resumo = jobs.run_pending(db, budget_seconds=0.1, batch_id=batch_id)
    finally:
        db.close()

    assert resumo["processed"] == 0
    assert resumo["remaining"] == 5


def test_lote_para_quando_a_cota_acaba_e_guarda_o_resto(client):
    client.get("/api/me")
    _set_limite(limite=2, usadas=0)
    batch_id = client.post("/api/batches", json={
        "domains": ["a.com.br", "b.com.br", "c.com.br", "d.com.br"],
    }).json()["batch_id"]

    with patch("services.enrichment_service.enrich_company", side_effect=_mock_enrich):
        dados = client.post(f"/api/batches/{batch_id}/run").json()

    assert dados["quota_reached"] is True
    assert dados["done"] == 2
    # Os domínios que não couberam continuam na fila, não viram erro.
    assert dados["progresso"]["na_fila"] == 2
    assert dados["progresso"]["com_erro"] == 0


def test_falha_de_coleta_e_retentada_e_depois_desiste(client):
    batch_id = client.post("/api/batches", json={"domains": ["quebra.com.br"]}).json()["batch_id"]

    with patch("services.enrichment_service.enrich_company", side_effect=RuntimeError("rede caiu")):
        for _ in range(jobs.MAX_ATTEMPTS):
            client.post(f"/api/batches/{batch_id}/run")

    db = _Session()
    try:
        job = db.query(Job).filter(Job.batch_id == batch_id).first()
        assert job.attempts == jobs.MAX_ATTEMPTS
        assert job.status == "failed"
        assert "rede caiu" in (job.error or "")
    finally:
        db.close()


def test_dominio_ja_pesquisado_sai_do_cache_sem_gastar_cota(client):
    with patch("services.enrichment_service.enrich_company", side_effect=_mock_enrich):
        client.post("/api/enrich", json={"domain": "nubank.com.br"})
    usadas = client.get("/api/me").json()["searches_used"]

    batch_id = client.post("/api/batches", json={"domains": ["nubank.com.br"]}).json()["batch_id"]
    with patch("services.enrichment_service.enrich_company", side_effect=_mock_enrich) as coleta:
        client.post(f"/api/batches/{batch_id}/run")

    coleta.assert_not_called()
    assert client.get("/api/me").json()["searches_used"] == usadas


def test_dois_workers_nao_processam_o_mesmo_job(client):
    """A reserva é um UPDATE condicional: quem perde a corrida pega o próximo."""
    batch_id = client.post("/api/batches", json={"domains": ["unico.com.br"]}).json()["batch_id"]

    db_a, db_b = _Session(), _Session()
    try:
        job_a = db_a.query(Job).filter(Job.batch_id == batch_id).first()
        job_b = db_b.query(Job).filter(Job.batch_id == batch_id).first()
        assert jobs._claim(db_a, job_a) is True
        assert jobs._claim(db_b, job_b) is False
    finally:
        db_a.close()
        db_b.close()


# ── Isolamento entre contas ──────────────────────────────────────────────────

def test_lote_de_outro_usuario_nao_e_visivel(client):
    db = _Session()
    try:
        db.add(Job(user_id="outro-usuario", batch_id="lote-alheio",
                   payload={"domain": "secreto.com.br"}, status="queued"))
        db.commit()
    finally:
        db.close()

    assert client.get("/api/batches/lote-alheio").status_code == 404
    assert client.post("/api/batches/lote-alheio/run").status_code == 404


def test_listagem_traz_os_lotes_do_usuario(client):
    client.post("/api/batches", json={"domains": ["a.com.br"]})
    client.post("/api/batches", json={"domains": ["b.com.br", "c.com.br"]})

    lotes = client.get("/api/batches").json()
    assert len(lotes) == 2
    assert lotes[0]["total"] == 2      # mais recente primeiro
    assert "itens" not in lotes[0]     # listagem é resumo
