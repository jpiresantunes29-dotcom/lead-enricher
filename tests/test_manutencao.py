"""
Testes das rotinas de manutenção: worker da fila e limpeza das sessões demo.

As duas rodam sem ninguém olhando, então o que importa é o que elas NÃO fazem:
não rodar sem segredo, não apagar quem ainda está usando e não tocar no banco
global de contatos.
"""
import pytest
from datetime import timedelta
from unittest.mock import patch

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401

from models.database import (
    Activity, Company, ExtensionToken, Job, Lead, Person, Profile, Reveal, utcnow,
)
from services import demo_cleanup

SEGREDO = "segredo-do-cron-para-teste"


@pytest.fixture(autouse=True)
def cron_env(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", SEGREDO)


def _auth():
    return {"Authorization": f"Bearer {SEGREDO}"}


def _criar_perfil_demo(user_id: str, dias_atras: int, com_dados: bool = True):
    """Perfil demo com data de criação/uso no passado."""
    quando = utcnow() - timedelta(days=dias_atras)
    db = _Session()
    try:
        db.add(Profile(id=user_id, plan="pro", searches_limit=500,
                       created_at=quando, updated_at=quando))
        db.flush()
        if com_dados:
            lead = Lead(user_id=user_id, raw_input_domain="acme.com.br",
                        domain="acme.com.br", status="enriched", created_at=quando)
            db.add(lead)
            db.flush()
            db.add(Activity(lead_id=lead.id, user_id=user_id, type="note",
                            notes="ligação de teste", created_at=quando))
            db.add(Job(user_id=user_id, batch_id="lote-demo",
                       payload={"domain": "acme.com.br"}, status="done"))
            db.add(ExtensionToken(user_id=user_id, token_hash="hash-qualquer"))
            # O ledger de créditos aponta para uma pessoa do banco global: ela
            # sobrevive à limpeza, o registro de quem a revelou não.
            pessoa = Person(dedupe_key=f"pessoa-de-{user_id}", full_name="Contato Teste")
            db.add(pessoa)
            db.flush()
            db.add(Reveal(user_id=user_id, person_id=pessoa.id, kind="both"))
        db.commit()
    finally:
        db.close()


def _existe(user_id: str) -> bool:
    db = _Session()
    try:
        return db.query(Profile).filter(Profile.id == user_id).first() is not None
    finally:
        db.close()


# ── Proteção das rotas internas ──────────────────────────────────────────────

def test_rota_interna_exige_segredo(client):
    assert client.post("/api/internal/jobs/run").status_code == 401
    assert client.post("/api/internal/demo/cleanup").status_code == 401


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


# ── Limpeza das sessões demo ─────────────────────────────────────────────────

def test_limpeza_remove_sessao_demo_antiga_com_seus_dados(client):
    _criar_perfil_demo("demo-antigo", dias_atras=30)

    resp = client.post("/api/internal/demo/cleanup", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["perfis"] == 1

    assert not _existe("demo-antigo")
    db = _Session()
    try:
        assert db.query(Lead).filter(Lead.user_id == "demo-antigo").count() == 0
        assert db.query(Activity).filter(Activity.user_id == "demo-antigo").count() == 0
        assert db.query(Job).filter(Job.user_id == "demo-antigo").count() == 0
        assert db.query(Reveal).filter(Reveal.user_id == "demo-antigo").count() == 0
        assert db.query(ExtensionToken).filter(
            ExtensionToken.user_id == "demo-antigo").count() == 0
    finally:
        db.close()


def test_limpeza_nao_toca_em_sessao_demo_recente(client):
    _criar_perfil_demo("demo-de-ontem", dias_atras=1)
    client.post("/api/internal/demo/cleanup", headers=_auth())
    assert _existe("demo-de-ontem")


def test_limpeza_respeita_quem_voltou_a_usar(client):
    """
    Perfil criado há 30 dias mas com busca de ontem é gente que voltou: apagar
    os leads dela no meio da visita seria o pior tipo de limpeza.
    """
    _criar_perfil_demo("demo-que-voltou", dias_atras=30, com_dados=False)
    db = _Session()
    try:
        db.add(Lead(user_id="demo-que-voltou", raw_input_domain="novo.com.br",
                    domain="novo.com.br", status="enriched",
                    created_at=utcnow() - timedelta(days=1)))
        db.commit()
    finally:
        db.close()

    client.post("/api/internal/demo/cleanup", headers=_auth())
    assert _existe("demo-que-voltou")


def test_limpeza_nunca_toca_em_conta_real(client):
    """Conta de verdade não tem prefixo demo — e não some, por mais antiga que seja."""
    db = _Session()
    try:
        antiga = utcnow() - timedelta(days=400)
        db.add(Profile(id="00000000-0000-0000-0000-000000000001", plan="pro",
                       created_at=antiga, updated_at=antiga))
        db.commit()
    finally:
        db.close()

    client.post("/api/internal/demo/cleanup", headers=_auth())
    assert _existe("00000000-0000-0000-0000-000000000001")


def test_limpeza_preserva_o_banco_global_de_contatos(client):
    """
    Company/Person são conhecimento do produto, pago em tempo de coleta — a
    sessão que os descobriu some, eles ficam.
    """
    _criar_perfil_demo("demo-antigo", dias_atras=30)
    db = _Session()
    try:
        empresa = Company(domain="acme.com.br", name="Acme")
        db.add(empresa)
        db.flush()
        db.add(Person(dedupe_key="acme-joao", full_name="João Silva",
                      company_id=empresa.id, company_domain="acme.com.br"))
        db.commit()
    finally:
        db.close()

    client.post("/api/internal/demo/cleanup", headers=_auth())

    db = _Session()
    try:
        assert db.query(Company).filter(Company.domain == "acme.com.br").count() == 1
        assert db.query(Person).filter(Person.dedupe_key == "acme-joao").count() == 1
    finally:
        db.close()


def test_ttl_configuravel(client):
    _criar_perfil_demo("demo-de-3-dias", dias_atras=3)
    db = _Session()
    try:
        assert demo_cleanup.purge_demo_profiles(db, ttl_days=10)["perfis"] == 0
        assert demo_cleanup.purge_demo_profiles(db, ttl_days=2)["perfis"] == 1
    finally:
        db.close()
