"""
A tela de conversas: listagem, controle humano e resposta manual.

O que se protege aqui é o controle: que o usuário consiga assumir uma conversa
e que assumir signifique alguma coisa — a automação cala na hora. E que ele não
consiga ver nem controlar conversa de outra conta.
"""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session  # noqa: F401
from tests.test_wa_rotas import (  # noqa: F401
    whatsapp_configurado, _lead, _envio_ok, _postar_webhook, _evento_mensagem,
    _abrir_conversa, TELEFONE,
)

from models.database import (
    AI_ACTIVE, AI_PAUSED, HUMAN_HANDOFF, STOPPED, Conversation, WaMessage, utcnow,
)
from services.people import optout
from services.wa import client as wa_client, gate, orchestrator


@pytest.fixture(autouse=True)
def sem_turno_automatico(monkeypatch):
    """
    A automação não roda nestes testes.

    Aqui o assunto é o controle humano: o que o usuário vê e o que os botões
    dele fazem. Com o turno ligado, cada mensagem recebida mudaria o estado da
    conversa por conta própria e os testes passariam a medir a automação em vez
    da tela. O turno tem arquivo próprio: `test_wa_turno.py`.
    """
    monkeypatch.setattr(
        orchestrator, "responder",
        lambda db, conversa, agora=None: orchestrator.Turno(orchestrator.NAO_FEZ_NADA),
    )


def _conversa_com_resposta(client):
    """Fluxo real até o ponto em que existe conversa viva: convite + resposta."""
    lead_id = _lead()
    _abrir_conversa(client, lead_id)
    _postar_webhook(client, _evento_mensagem())
    conversa_id = client.get("/api/wa/conversations").json()[0]["id"]
    return lead_id, conversa_id


def _estado(conversa_id):
    db = _Session()
    try:
        return db.query(Conversation).filter(Conversation.id == conversa_id).one().ai_status
    finally:
        db.close()


# ── Listagem ─────────────────────────────────────────────────────────────────

def test_lista_vazia_quando_nao_ha_conversa(client):
    assert client.get("/api/wa/conversations").json() == []


def test_conversa_recem_aberta_aparece_com_selo_de_ia_ativa(client):
    lead_id = _lead()
    _abrir_conversa(client, lead_id)

    cards = client.get("/api/wa/conversations").json()
    assert len(cards) == 1
    assert cards[0]["ai_status"] == AI_ACTIVE
    assert cards[0]["selo"]["tom"] == "ativa"
    assert cards[0]["company_name"] == "Acme"


def test_todo_selo_traz_uma_explicacao(client):
    """Selo sem frase não diz se o lead ainda recebe resposta."""
    lead_id = _lead()
    _abrir_conversa(client, lead_id)
    for acao in ("pausar", "assumir", "retomar", "encerrar"):
        conversa_id = client.get("/api/wa/conversations").json()[0]["id"]
        card = client.patch(f"/api/wa/conversations/{conversa_id}",
                            json={"acao": acao}).json()
        assert card["selo"]["explicacao"].strip()


def _conversa_alheia() -> int:
    """
    Conversa de outra conta, criada direto no banco.

    Pela API não dá: `/api/wa/start` já recusa lead que não é do usuário. O
    ponto aqui é a camada seguinte — se a linha existir, ela continua invisível.
    """
    db = _Session()
    try:
        lead_id = _lead(db=db, user_id="outro-usuario")
        conversa = Conversation(lead_id=lead_id, user_id="outro-usuario",
                                phone_e164="+5511977776666")
        db.add(conversa)
        db.commit()
        return conversa.id
    finally:
        db.close()


def test_conversa_de_outro_usuario_nao_aparece(client):
    _conversa_alheia()
    assert client.get("/api/wa/conversations").json() == []


def test_conversa_de_outro_usuario_nao_pode_ser_aberta_nem_controlada(client):
    alheia = _conversa_alheia()

    assert client.get(f"/api/wa/conversations/{alheia}").status_code == 404
    assert client.patch(f"/api/wa/conversations/{alheia}",
                        json={"acao": "assumir"}).status_code == 404
    assert client.post(f"/api/wa/conversations/{alheia}/reply",
                       json={"texto": "oi"}).status_code == 404


# ── Detalhe ──────────────────────────────────────────────────────────────────

def test_detalhe_traz_as_mensagens_na_ordem(client):
    lead_id, conversa_id = _conversa_com_resposta(client)
    det = client.get(f"/api/wa/conversations/{conversa_id}").json()

    tipos = [(m["direction"], m["type"]) for m in det["messages"]]
    assert tipos == [("out", "template"), ("in", "text")]
    assert det["card"]["janela_aberta"] is True


# ── Controle humano ──────────────────────────────────────────────────────────

def test_assumir_cala_a_automacao_na_hora(client):
    """
    O teste que dá sentido ao botão: depois de assumir, o portão — que é quem
    autoriza cada envio — passa a recusar.
    """
    lead_id, conversa_id = _conversa_com_resposta(client)

    db = _Session()
    try:
        conversa = db.query(Conversation).filter(Conversation.id == conversa_id).one()
        assert gate.can_send(db, conversa, agora=_dentro_do_horario(conversa)).allowed is True
    finally:
        db.close()

    card = client.patch(f"/api/wa/conversations/{conversa_id}",
                        json={"acao": "assumir"}).json()
    assert card["ai_status"] == HUMAN_HANDOFF

    db = _Session()
    try:
        conversa = db.query(Conversation).filter(Conversation.id == conversa_id).one()
        decisao = gate.can_send(db, conversa, agora=_dentro_do_horario(conversa))
        assert decisao.allowed is False
        assert decisao.reason == gate.DENY_AI_NOT_ACTIVE
    finally:
        db.close()


def _dentro_do_horario(conversa):
    """
    Um instante que cai no horário comercial e dentro da janela da conversa.

    Sem isto o teste passaria ou falharia conforme a hora em que a suíte roda.
    """
    from datetime import datetime, UTC
    base = conversa.created_at or utcnow()
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return base.replace(hour=17, minute=0, second=0, microsecond=0) + timedelta(
        days=(0 if base.weekday() < 5 else 7 - base.weekday())
    )


def test_pausar_e_retomar_voltam_ao_estado_anterior(client):
    lead_id, conversa_id = _conversa_com_resposta(client)

    client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": "pausar"})
    assert _estado(conversa_id) == AI_PAUSED

    client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": "retomar"})
    assert _estado(conversa_id) == AI_ACTIVE


def test_encerrar_fecha_a_conversa(client):
    lead_id, conversa_id = _conversa_com_resposta(client)
    client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": "encerrar"})
    assert _estado(conversa_id) == STOPPED


def test_acao_desconhecida_e_recusada(client):
    """A tela não escreve `ai_status`: só manda verbos que o serviço conhece."""
    lead_id, conversa_id = _conversa_com_resposta(client)
    resp = client.patch(f"/api/wa/conversations/{conversa_id}",
                        json={"acao": "desligar_tudo"})
    assert resp.status_code == 422
    assert _estado(conversa_id) == AI_ACTIVE


def test_motivo_do_handoff_chega_na_tela(client):
    lead_id, conversa_id = _conversa_com_resposta(client)
    card = client.patch(f"/api/wa/conversations/{conversa_id}",
                        json={"acao": "assumir", "motivo": "Cliente pediu proposta."}).json()
    assert card["handoff_reason"] == "Cliente pediu proposta."


# ── Aviso de pendência ───────────────────────────────────────────────────────

def test_conversa_com_ia_ativa_nao_conta_como_pendencia(client):
    """Com a automação ligada, ninguém precisa ser chamado."""
    _conversa_com_resposta(client)
    assert client.get("/api/wa/status").json()["aguardando"] == 0


def test_lead_que_escreveu_e_nao_teve_resposta_vira_pendencia(client):
    lead_id, conversa_id = _conversa_com_resposta(client)
    client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": "assumir"})

    assert client.get("/api/wa/status").json()["aguardando"] == 1
    card = client.get("/api/wa/conversations").json()[0]
    assert card["aguardando_voce"] is True
    assert card["selo"]["tom"] == "aguardando"


def test_conversa_parada_sem_mensagem_nova_nao_e_pendencia(client):
    """Parar é uma escolha do usuário; escolha não vira cobrança na barra."""
    lead_id = _lead()
    _abrir_conversa(client, lead_id)
    conversa_id = client.get("/api/wa/conversations").json()[0]["id"]
    client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": "pausar"})

    assert client.get("/api/wa/status").json()["aguardando"] == 0


def test_responder_tira_a_conversa_da_pendencia(client):
    lead_id, conversa_id = _conversa_com_resposta(client)
    client.patch(f"/api/wa/conversations/{conversa_id}", json={"acao": "assumir"})
    assert client.get("/api/wa/status").json()["aguardando"] == 1

    with patch("services.wa.client.send_text", return_value=_envio_ok("wamid.RESP")):
        client.post(f"/api/wa/conversations/{conversa_id}/reply", json={"texto": "Oi, Ana!"})

    assert client.get("/api/wa/status").json()["aguardando"] == 0


# ── Resposta manual ──────────────────────────────────────────────────────────

def test_responder_envia_e_registra_como_humano(client):
    lead_id, conversa_id = _conversa_com_resposta(client)
    with patch("services.wa.client.send_text", return_value=_envio_ok("wamid.RESP")) as envio:
        resp = client.post(f"/api/wa/conversations/{conversa_id}/reply",
                           json={"texto": "Oi, Ana! Aqui é o João."})

    assert resp.status_code == 200
    assert envio.call_args[0] == (TELEFONE, "Oi, Ana! Aqui é o João.")

    db = _Session()
    try:
        msg = (db.query(WaMessage)
               .filter(WaMessage.wa_message_id == "wamid.RESP").one())
        assert msg.sent_by == "human"
        assert msg.direction == "out"
    finally:
        db.close()


def test_responder_assume_a_conversa_sozinho(client):
    """
    Quem digita a resposta está assumindo. Deixar a IA continuar respondendo
    por cima de uma conversa que o humano entrou seria o pior dos mundos.
    """
    lead_id, conversa_id = _conversa_com_resposta(client)
    assert _estado(conversa_id) == AI_ACTIVE

    with patch("services.wa.client.send_text", return_value=_envio_ok("wamid.RESP")):
        client.post(f"/api/wa/conversations/{conversa_id}/reply", json={"texto": "Oi!"})

    assert _estado(conversa_id) == HUMAN_HANDOFF


def test_responder_fora_da_janela_e_recusado(client):
    """Regra da Meta, não nossa: fora das 24 h só template reabre."""
    lead_id, conversa_id = _conversa_com_resposta(client)
    db = _Session()
    try:
        conversa = db.query(Conversation).filter(Conversation.id == conversa_id).one()
        conversa.window_expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    with patch("services.wa.client.send_text", return_value=_envio_ok()) as envio:
        resp = client.post(f"/api/wa/conversations/{conversa_id}/reply", json={"texto": "oi"})

    assert resp.status_code == 409
    assert envio.call_count == 0


def test_responder_para_quem_pediu_para_parar_e_recusado(client):
    """A promessa de LGPD vale também para a mão do usuário."""
    lead_id, conversa_id = _conversa_com_resposta(client)
    db = _Session()
    try:
        optout.register(db, "phone", TELEFONE, source="teste")
        db.commit()
    finally:
        db.close()

    with patch("services.wa.client.send_text", return_value=_envio_ok()) as envio:
        resp = client.post(f"/api/wa/conversations/{conversa_id}/reply", json={"texto": "oi"})

    assert resp.status_code == 409
    assert envio.call_count == 0


def test_resposta_vazia_e_recusada(client):
    lead_id, conversa_id = _conversa_com_resposta(client)
    resp = client.post(f"/api/wa/conversations/{conversa_id}/reply", json={"texto": "   "})
    assert resp.status_code == 422


def test_falha_da_meta_nao_registra_mensagem_que_nao_saiu(client):
    lead_id, conversa_id = _conversa_com_resposta(client)
    recusa = wa_client.SendResult(False, error="131026: destinatário indisponível")
    with patch("services.wa.client.send_text", return_value=recusa):
        resp = client.post(f"/api/wa/conversations/{conversa_id}/reply", json={"texto": "oi"})

    assert resp.status_code == 502
    db = _Session()
    try:
        assert db.query(WaMessage).filter(WaMessage.sent_by == "human",
                                          WaMessage.type == "text").count() == 0
    finally:
        db.close()
