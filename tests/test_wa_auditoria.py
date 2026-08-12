"""
Trilha de auditoria e métricas.

A trilha existe para responder "por que essa conversa continuou depois de eu
ter pausado?", que é a pergunta que `handoff_reason` não responde — ele guarda
só o último motivo. Por isso o que se testa aqui é o **ator**: a mesma mudança
de estado feita por um clique e por um gatilho da automação precisa ficar
distinguível depois.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session  # noqa: F401
from tests.test_wa_rotas import (  # noqa: F401
    whatsapp_configurado, _lead, _envio_ok, _postar_webhook, _evento_mensagem,
    _abrir_conversa, TELEFONE,
)

from models.database import (
    AUDIT_ASSUMIDA, AUDIT_CONVERSA_ABERTA, AUDIT_ENCERRADA, AUDIT_PAUSADA,
    AUDIT_RELACIONAMENTO, AUDIT_RETOMADA,
    AuditLog, Conversation, Lead, RELATIONSHIP_CUSTOMER, utcnow,
)
from services.wa import brain, client as wa_client, orchestrator, states


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    """
    A consulta de qualidade do número é uma chamada externa à Meta.

    Sem este mock, a suíte sairia para a internet com credenciais falsas: lenta,
    dependente de rede e capaz de falhar por motivo nenhum a ver com o código.
    """
    monkeypatch.setattr(wa_client, "phone_quality", lambda: None)


@pytest.fixture(autouse=True)
def sem_turno_automatico(monkeypatch):
    """A automação tem arquivo próprio; aqui o assunto é o registro."""
    monkeypatch.setattr(
        orchestrator, "responder",
        lambda db, conversa, agora=None: orchestrator.Turno(orchestrator.NAO_FEZ_NADA),
    )


def _registros(acao=None):
    db = _Session()
    try:
        q = db.query(AuditLog).order_by(AuditLog.id.asc())
        if acao:
            q = q.filter(AuditLog.acao == acao)
        return q.all()
    finally:
        db.close()


def _conversa_viva(client):
    lead_id = _lead()
    _abrir_conversa(client, lead_id)
    _postar_webhook(client, _evento_mensagem())
    return lead_id, client.get("/api/wa/conversations").json()[0]["id"]


# ── O que fica registrado ────────────────────────────────────────────────────

def test_convite_enviado_entra_na_trilha(client):
    """A única ação do produto que gasta dinheiro fica registrada sempre."""
    _abrir_conversa(client, _lead())
    registros = _registros(AUDIT_CONVERSA_ABERTA)
    assert len(registros) == 1
    assert registros[0].ator == states.ATOR_HUMANO
    assert TELEFONE in registros[0].detalhe


@pytest.mark.parametrize("acao,esperado", [
    ("pausar", AUDIT_PAUSADA),
    ("assumir", AUDIT_ASSUMIDA),
    ("retomar", AUDIT_RETOMADA),
    ("encerrar", AUDIT_ENCERRADA),
])
def test_cada_clique_do_usuario_vira_um_registro(client, acao, esperado):
    lead_id, conversa_id = _conversa_viva(client)
    client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": acao})

    registros = _registros(esperado)
    assert len(registros) == 1
    assert registros[0].ator == states.ATOR_HUMANO
    assert registros[0].conversation_id == conversa_id


def test_a_trilha_guarda_a_sequencia_inteira(client):
    """
    O ponto da tabela: `handoff_reason` guarda só o último motivo, então
    sozinho ele não conta história nenhuma.
    """
    lead_id, conversa_id = _conversa_viva(client)
    for acao in ("pausar", "retomar", "assumir", "encerrar"):
        client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": acao})

    sequencia = [r.acao for r in _registros() if r.conversation_id == conversa_id]
    assert sequencia == [
        AUDIT_CONVERSA_ABERTA, AUDIT_PAUSADA, AUDIT_RETOMADA,
        AUDIT_ASSUMIDA, AUDIT_ENCERRADA,
    ]


def test_handoff_da_automacao_fica_distinguivel_do_clique(client, monkeypatch):
    """
    O teste que dá sentido à coluna `ator`: o mesmo estado final, duas
    histórias diferentes — e depois alguém vai precisar saber qual foi.
    """
    monkeypatch.undo()   # devolve o orquestrador de verdade
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-de-teste")

    db = _Session()
    try:
        lead_id = _lead(db=db)
        conversa = Conversation(
            lead_id=lead_id, user_id="test-user-123", phone_e164=TELEFONE,
            window_expires_at=utcnow() + timedelta(hours=20),
            last_inbound_at=utcnow(),
        )
        db.add(conversa)
        db.commit()
        db.add(__import__("models.database", fromlist=["WaMessage"]).WaMessage(
            conversation_id=conversa.id, direction="in", type="text", body="quanto custa?"))
        db.commit()

        with patch.object(brain, "ler",
                          return_value=brain.Leitura(brain.NEGOCIANDO, 0.95)), \
             patch.object(wa_client, "send_text"):
            orchestrator.responder(db, conversa)
    finally:
        db.close()

    registros = _registros(AUDIT_ASSUMIDA)
    assert len(registros) == 1
    assert registros[0].ator == states.ATOR_IA


def test_virar_cliente_fica_registrado_com_o_motivo(client):
    lead_id, conversa_id = _conversa_viva(client)
    db = _Session()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).one()
        states.mark_customer(db, lead, "Disse que já é cliente.")
        db.commit()
    finally:
        db.close()

    registros = _registros(AUDIT_RELACIONAMENTO)
    assert len(registros) == 1
    assert registros[0].detalhe.startswith(RELATIONSHIP_CUSTOMER)
    assert "já é cliente" in registros[0].detalhe


def test_a_trilha_nao_copia_o_conteudo_das_mensagens(client):
    """
    Conteúdo trocado vive só em `wa_messages`. Copiá-lo aqui criaria uma
    segunda cópia de dado pessoal para manter em dia — e para apagar quando o
    titular pedir.
    """
    lead_id, conversa_id = _conversa_viva(client)
    detalhes = " ".join(r.detalhe or "" for r in _registros())
    assert "quem fala" not in detalhes


# ── Isolamento entre contas ──────────────────────────────────────────────────

def test_auditoria_de_conversa_alheia_nao_vaza(client):
    db = _Session()
    try:
        lead_id = _lead(db=db, user_id="outro-usuario")
        conversa = Conversation(lead_id=lead_id, user_id="outro-usuario",
                                phone_e164="+5511977776666")
        db.add(conversa)
        db.commit()
        alheia = conversa.id
    finally:
        db.close()

    assert client.get(f"/api/wa/conversations/{alheia}/audit").status_code == 404


def test_auditoria_da_propria_conversa_e_visivel(client):
    lead_id, conversa_id = _conversa_viva(client)
    client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": "assumir"})

    corpo = client.get(f"/api/wa/conversations/{conversa_id}/audit").json()
    assert [r["acao"] for r in corpo] == [AUDIT_ASSUMIDA, AUDIT_CONVERSA_ABERTA]


# ── Métricas ─────────────────────────────────────────────────────────────────

def test_metricas_vazias_nao_quebram(client):
    m = client.get("/api/wa/metrics").json()
    assert m["conversas_iniciadas"] == 0
    assert m["taxa_de_resposta"] == 0.0
    assert m["taxa_de_handoff"] == 0.0


def test_metricas_contam_convite_e_resposta(client):
    _conversa_viva(client)
    m = client.get("/api/wa/metrics").json()

    assert m["conversas_iniciadas"] == 1
    assert m["convites_enviados"] == 1
    assert m["leads_que_responderam"] == 1
    assert m["taxa_de_resposta"] == 1.0


def test_convite_sem_resposta_derruba_a_taxa_de_resposta(client):
    _abrir_conversa(client, _lead())
    m = client.get("/api/wa/metrics").json()
    assert m["convites_enviados"] == 1
    assert m["leads_que_responderam"] == 0
    assert m["taxa_de_resposta"] == 0.0


def test_taxa_de_handoff_e_sobre_quem_respondeu(client):
    """
    Sobre o total, a taxa cairia sozinha a cada convite não respondido e
    passaria a medir taxa de resposta em vez do que se quer saber: quanto a
    automação consegue tocar sozinha.
    """
    lead_id, conversa_id = _conversa_viva(client)
    _abrir_conversa(client, _lead())        # segundo convite, sem resposta
    client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": "assumir"})

    m = client.get("/api/wa/metrics").json()
    assert m["conversas_iniciadas"] == 2
    assert m["leads_que_responderam"] == 1
    assert m["taxa_de_handoff"] == 1.0      # 1 handoff sobre 1 que respondeu


def test_metricas_listam_os_motivos_de_handoff(client):
    lead_id, conversa_id = _conversa_viva(client)
    client.patch(f"/api/wa/conversations/{conversa_id}",
                 json={"acao": "assumir", "motivo": "Lead pediu proposta."})

    motivos = client.get("/api/wa/metrics").json()["motivos_de_handoff"]
    assert motivos[0]["motivo"] == "Lead pediu proposta."
    assert motivos[0]["vezes"] == 1


def test_metricas_separam_resposta_da_ia_da_resposta_humana(client):
    lead_id, conversa_id = _conversa_viva(client)
    with patch("services.wa.client.send_text", return_value=_envio_ok("wamid.HUM")):
        client.post(f"/api/wa/conversations/{conversa_id}/reply", json={"texto": "Oi!"})

    m = client.get("/api/wa/metrics").json()
    assert m["respostas_suas"] == 1
    assert m["respostas_da_ia"] == 0
    # O convite é template, não conta como resposta escrita.
    assert m["convites_enviados"] == 1


def test_metricas_nao_veem_conversa_de_outra_conta(client):
    db = _Session()
    try:
        lead_id = _lead(db=db, user_id="outro-usuario")
        db.add(Conversation(lead_id=lead_id, user_id="outro-usuario",
                            phone_e164="+5511977776666"))
        db.commit()
    finally:
        db.close()

    assert client.get("/api/wa/metrics").json()["conversas_iniciadas"] == 0


def test_metricas_respeitam_o_periodo(client):
    lead_id, conversa_id = _conversa_viva(client)
    db = _Session()
    try:
        conversa = db.query(Conversation).filter(Conversation.id == conversa_id).one()
        conversa.created_at = utcnow() - timedelta(days=60)
        db.commit()
    finally:
        db.close()

    assert client.get("/api/wa/metrics", params={"dias": 30}).json()["conversas_iniciadas"] == 0
    assert client.get("/api/wa/metrics", params={"dias": 90}).json()["conversas_iniciadas"] == 1


def test_metricas_nao_inventam_custo_em_reais(client):
    """
    O preço do template muda por país e categoria. Um valor inventado na tela
    vira decisão de negócio errada — a contagem de convites é o que se
    multiplica pela tabela atual da Meta.
    """
    corpo = client.get("/api/wa/metrics").json()
    assert not any("custo" in chave or "reais" in chave for chave in corpo)


# ── Qualidade do número ──────────────────────────────────────────────────────

def test_qualidade_rebaixada_chega_na_tela(client, monkeypatch):
    monkeypatch.setattr(wa_client, "phone_quality", lambda: {
        "rating": "RED", "tom": "critico", "recado": "Pare os convites frios agora.",
        "limite": "TIER_250",
    })
    q = client.get("/api/wa/metrics").json()["qualidade_do_numero"]
    assert q["tom"] == "critico"


def test_meta_indisponivel_nao_quebra_as_metricas(client, monkeypatch):
    monkeypatch.setattr(wa_client, "phone_quality", lambda: None)
    resp = client.get("/api/wa/metrics")
    assert resp.status_code == 200
    assert resp.json()["qualidade_do_numero"] is None


def test_aviso_da_meta_no_webhook_nao_derruba_o_recebimento(client):
    evento = {"entry": [{"changes": [{
        "field": "account_update",
        "value": {"event": "PHONE_NUMBER_QUALITY_UPDATE", "current_limit": "TIER_250"},
    }]}]}
    resp = _postar_webhook(client, evento)
    assert resp.status_code == 200
