"""
Coexistência: o WhatsApp Business do celular conectado e espelhado aqui.

O que estes testes guardam:

  **Histórico não é atividade.** Seis meses de conversas importadas não podem
  acender "aguardando você", abrir janela de 24 h nem acordar a IA — senão a
  conexão do WhatsApp dispararia respostas automáticas a mensagens antigas.

  **Nem todo contato é lead.** Quem escreve sem ter sido convidado vira
  conversa sem lead, só humana, e pode ser vinculada a um lead depois.

  **O celular manda.** O que o dono responde pelo app tira a IA da conversa.

  **O popup conecta e pede a sincronização no mesmo clique** — a Meta só
  aceita esse pedido uma vez, nas 24 h seguintes à conexão.
"""
import json
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session  # noqa: F401

from models.database import (
    AI_ACTIVE, HUMAN_HANDOFF,
    HISTORICO_CONCLUIDO, HISTORICO_IMPORTANDO, HISTORICO_RECUSADO, HISTORICO_SOLICITADO,
    ONBOARDING_COEXISTENCIA,
    Conversation, DecisionMaker, Lead, WaContact, WaMessage, WhatsAppConnection, utcnow,
)
from services.wa import client as wa_client, coexistencia, embedded, gate, orchestrator, webhook

MEU_USER = "test-user-123"
MEU_PNID = "333000333"
SEGREDO_DO_APP = "segredo-do-app-na-meta"
CONTATO = "5511977776666"
CONTATO_E164 = "+5511977776666"


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    """
    Servidor configurado para o popup da Meta: um App Secret só, o do app, que
    assina o webhook de todas as contas conectadas pelo popup.
    """
    monkeypatch.setattr(gate, "service_window", lambda agora=None: (True, False))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    for var in ("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_TEMPLATE_NAME", "WHATSAPP_VERIFY_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SEGREDO_DO_APP)
    monkeypatch.setenv("META_APP_ID", "app-123")
    monkeypatch.setenv("META_ES_CONFIG_ID", "config-456")


def _conectar_coexistencia():
    db = _Session()
    try:
        db.add(WhatsAppConnection(
            user_id=MEU_USER, phone_number_id=MEU_PNID, access_token="token-coex",
            waba_id="WABA9", template_name="primeiro_contato",
            onboarding=ONBOARDING_COEXISTENCIA, verified_at=utcnow(),
        ))
        db.commit()
    finally:
        db.close()


def _postar(client, campo, valor):
    corpo = json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA9", "changes": [{"field": campo, "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "5511900000000",
                         "phone_number_id": MEU_PNID},
            **valor,
        }}]}],
    }).encode()
    return client.post(
        "/api/wa/webhook", content=corpo,
        headers={webhook.SIGNATURE_HEADER: webhook.expected_signature(corpo, SEGREDO_DO_APP)},
    )


def _historico(threads, progresso=100):
    return {"history": [{"metadata": {"phase": 0, "chunk_order": 1, "progress": progresso},
                         "threads": threads}]}


def _msg(wamid, de, ts, texto):
    return {"from": de, "id": wamid, "timestamp": str(ts), "type": "text",
            "text": {"body": texto}, "history_context": {"status": "READ"}}


def _conversas():
    db = _Session()
    try:
        return db.query(Conversation).all()
    finally:
        db.close()


# ── Histórico ────────────────────────────────────────────────────────────────

def test_historico_vira_conversa_sem_lead_e_nao_acende_pendencia(client):
    _conectar_coexistencia()
    resp = _postar(client, "history", _historico([{
        "id": CONTATO,
        "messages": [
            _msg("wamid.H1", "5511900000000", 1700000000, "Bom dia, segue o orçamento"),
            _msg("wamid.H2", CONTATO, 1700000100, "Obrigado! Vou analisar"),
        ],
    }]))
    assert resp.status_code == 200

    [conversa] = _conversas()
    assert conversa.lead_id is None
    assert conversa.ai_status == HUMAN_HANDOFF
    # Histórico não é atividade: sem janela, sem "aguardando você".
    assert conversa.last_inbound_at is None
    assert conversa.window_expires_at is None
    assert gate.aguardando_voce(conversa) is False
    assert conversa.last_message_body == "Obrigado! Vou analisar"

    db = _Session()
    try:
        direcoes = [m.direction for m in db.query(WaMessage).order_by(WaMessage.created_at)]
        assert direcoes == ["out", "in"]
        assert db.query(WhatsAppConnection).one().historico_status == HISTORICO_CONCLUIDO
    finally:
        db.close()


def test_historico_reentregue_nao_duplica(client):
    _conectar_coexistencia()
    lote = _historico([{"id": CONTATO, "messages": [_msg("wamid.H1", CONTATO, 1700000000, "oi")]}])
    _postar(client, "history", lote)
    _postar(client, "history", lote)

    db = _Session()
    try:
        assert db.query(WaMessage).count() == 1
        assert db.query(Conversation).count() == 1
    finally:
        db.close()


def test_progresso_do_historico_so_anda_para_frente(client):
    _conectar_coexistencia()
    _postar(client, "history", _historico(
        [{"id": CONTATO, "messages": [_msg("wamid.A", CONTATO, 1700000000, "a")]}], progresso=60))
    _postar(client, "history", _historico(
        [{"id": CONTATO, "messages": [_msg("wamid.B", CONTATO, 1690000000, "b")]}], progresso=30))

    db = _Session()
    try:
        conexao = db.query(WhatsAppConnection).one()
        assert conexao.historico_progresso == 60
        assert conexao.historico_status == HISTORICO_IMPORTANDO
    finally:
        db.close()


def test_historico_recusado_no_celular_fica_registrado(client):
    _conectar_coexistencia()
    _postar(client, "history", {"history": [{"errors": [
        {"code": coexistencia.ERRO_HISTORICO_RECUSADO, "title": "declined"}]}]})

    db = _Session()
    try:
        assert db.query(WhatsAppConnection).one().historico_status == HISTORICO_RECUSADO
    finally:
        db.close()


def test_mensagem_antiga_do_historico_nao_vira_ultima_para_a_ia(client):
    """
    Um lote de histórico gravado agora tem id maior que a resposta da IA de
    ontem. Se "a última" fosse pelo id, a IA responderia a algo de meses atrás.
    """
    _conectar_coexistencia()
    db = _Session()
    try:
        lead = Lead(user_id=MEU_USER, raw_input_domain="acme.com.br", domain="acme.com.br",
                    company_name="Acme", status="enriched", relationship="LEAD")
        db.add(lead)
        db.flush()
        conversa = Conversation(lead_id=lead.id, user_id=MEU_USER, phone_e164=CONTATO_E164,
                                ai_status=AI_ACTIVE)
        db.add(conversa)
        db.flush()
        db.add(WaMessage(conversation_id=conversa.id, direction="out", body="resposta da IA",
                         sent_by="ai", created_at=utcnow()))
        db.commit()
        conversa_id = conversa.id
    finally:
        db.close()

    _postar(client, "history", _historico([{"id": CONTATO, "messages": [
        _msg("wamid.VELHA", CONTATO, 1600000000, "mensagem de 2020")]}]))

    db = _Session()
    try:
        conversa = db.get(Conversation, conversa_id)
        assert orchestrator._ultima_e_do_lead(db, conversa) is False
    finally:
        db.close()


# ── Ecos do celular ──────────────────────────────────────────────────────────

def test_resposta_pelo_celular_tira_a_ia_da_conversa(client):
    _conectar_coexistencia()
    db = _Session()
    try:
        lead = Lead(user_id=MEU_USER, raw_input_domain="acme.com.br", domain="acme.com.br",
                    company_name="Acme", status="enriched", relationship="LEAD")
        db.add(lead)
        db.flush()
        conversa = Conversation(lead_id=lead.id, user_id=MEU_USER, phone_e164=CONTATO_E164,
                                ai_status=AI_ACTIVE, last_inbound_at=utcnow())
        db.add(conversa)
        db.commit()
        conversa_id = conversa.id
    finally:
        db.close()

    resp = _postar(client, "smb_message_echoes", {"message_echoes": [{
        "from": "5511900000000", "to": CONTATO, "id": "wamid.ECO1",
        "timestamp": "1770000000", "type": "text", "text": {"body": "Te ligo em 5 min"},
    }]})
    assert resp.status_code == 200

    db = _Session()
    try:
        conversa = db.get(Conversation, conversa_id)
        assert conversa.ai_status == HUMAN_HANDOFF
        assert gate.aguardando_voce(conversa) is False
        eco = db.query(WaMessage).filter(WaMessage.wa_message_id == "wamid.ECO1").one()
        assert (eco.direction, eco.sent_by) == ("out", "human")
    finally:
        db.close()


def test_eco_para_numero_novo_abre_conversa_sem_lead(client):
    _conectar_coexistencia()
    _postar(client, "smb_message_echoes", {"message_echoes": [{
        "from": "5511900000000", "to": CONTATO, "id": "wamid.ECO2",
        "timestamp": "1770000000", "type": "text", "text": {"body": "Oi!"},
    }]})
    [conversa] = _conversas()
    assert conversa.lead_id is None
    assert conversa.phone_e164 == CONTATO_E164


# ── Agenda ───────────────────────────────────────────────────────────────────

def test_agenda_da_nome_as_conversas_sem_mexer_na_ordem(client):
    _conectar_coexistencia()
    _postar(client, "history", _historico([{"id": CONTATO, "messages": [
        _msg("wamid.H1", CONTATO, 1700000000, "oi")]}]))
    [antes] = _conversas()

    _postar(client, "smb_app_state_sync", {"state_sync": [{
        "type": "contact", "action": "add",
        "contact": {"full_name": "Maria Fornecedora", "first_name": "Maria",
                    "phone_number": CONTATO},
        "metadata": {"timestamp": "1770000000"},
    }]})

    [depois] = _conversas()
    assert depois.contact_name == "Maria Fornecedora"
    assert depois.updated_at == antes.updated_at

    db = _Session()
    try:
        assert db.query(WaContact).one().name == "Maria Fornecedora"
    finally:
        db.close()


def test_conversa_nova_ja_nasce_com_o_nome_da_agenda(client):
    _conectar_coexistencia()
    _postar(client, "smb_app_state_sync", {"state_sync": [{
        "type": "contact", "action": "add",
        "contact": {"full_name": "João Cliente", "phone_number": CONTATO},
    }]})
    _postar(client, "history", _historico([{"id": CONTATO, "messages": [
        _msg("wamid.H1", CONTATO, 1700000000, "oi")]}]))

    [conversa] = _conversas()
    assert conversa.contact_name == "João Cliente"

    card = client.get("/api/wa/conversations").json()[0]
    assert card["lead_id"] is None
    assert card["contato"] == "João Cliente"
    assert card["selo"]["tom"] == "contato"


# ── Mensagem de quem não é lead ──────────────────────────────────────────────

def test_mensagem_de_numero_desconhecido_entra_sem_lead_e_sem_ia(client):
    _conectar_coexistencia()
    with patch("services.wa.orchestrator.responder") as turno:
        resp = _postar(client, "messages", {"messages": [{
            "from": CONTATO, "id": "wamid.IN1", "timestamp": "1770000000",
            "type": "text", "text": {"body": "Vocês entregam sábado?"},
        }]})

    assert resp.status_code == 200
    turno.assert_not_called()
    [conversa] = _conversas()
    assert conversa.lead_id is None
    assert conversa.ai_status == HUMAN_HANDOFF
    # Mensagem ao vivo abre janela: dá para responder de graça pela tela.
    assert conversa.window_expires_at is not None
    assert gate.aguardando_voce(conversa) is True


def test_conversa_sem_lead_nao_volta_para_a_ia(client):
    _conectar_coexistencia()
    _postar(client, "smb_message_echoes", {"message_echoes": [{
        "from": "5511900000000", "to": CONTATO, "id": "wamid.ECO3",
        "timestamp": "1770000000", "type": "text", "text": {"body": "Oi"},
    }]})
    [conversa] = _conversas()
    resp = client.patch(f"/api/wa/conversations/{conversa.id}", json={"acao": "retomar"})
    assert resp.status_code == 409


def test_metricas_ignoram_contatos_que_nao_sao_lead(client):
    _conectar_coexistencia()
    _postar(client, "history", _historico([{"id": CONTATO, "messages": [
        _msg("wamid.H1", CONTATO, 1700000000, "oi")]}]))
    with patch("services.wa.client.phone_quality", return_value=None):
        metricas = client.get("/api/wa/metrics").json()
    assert metricas["conversas_iniciadas"] == 0


# ── Vincular a um lead ───────────────────────────────────────────────────────

def _lead(nome, phone=None):
    db = _Session()
    try:
        lead = Lead(user_id=MEU_USER, raw_input_domain=f"{nome.lower()}.com.br",
                    domain=f"{nome.lower()}.com.br", company_name=nome, status="enriched",
                    phone=phone, relationship="LEAD")
        db.add(lead)
        db.commit()
        return lead.id
    finally:
        db.close()


def _conversa_sem_lead(client):
    _postar(client, "smb_message_echoes", {"message_echoes": [{
        "from": "5511900000000", "to": CONTATO, "id": "wamid.ECO9",
        "timestamp": "1770000000", "type": "text", "text": {"body": "Oi"},
    }]})
    return _conversas()[0].id


def test_sugestoes_poem_primeiro_quem_tem_o_mesmo_telefone(client):
    _conectar_coexistencia()
    conversa_id = _conversa_sem_lead(client)
    _lead("Alfa")
    outro = _lead("Beta")
    db = _Session()
    try:
        db.add(DecisionMaker(lead_id=outro, name="Fulano", phone="(11) 97777-6666"))
        db.commit()
    finally:
        db.close()

    sugestoes = client.get(
        f"/api/wa/conversations/{conversa_id}/leads-sugeridos", params={"q": "alfa"}).json()
    assert [s["company_name"] for s in sugestoes] == ["Beta", "Alfa"]
    assert sugestoes[0]["mesmo_telefone"] is True


def test_vincular_liga_a_conversa_ao_lead_e_mantem_humana(client):
    _conectar_coexistencia()
    conversa_id = _conversa_sem_lead(client)
    lead_id = _lead("Acme")

    resp = client.patch(f"/api/wa/conversations/{conversa_id}/lead", json={"lead_id": lead_id})
    assert resp.status_code == 200
    card = resp.json()
    assert card["lead_id"] == lead_id
    assert card["ai_status"] == HUMAN_HANDOFF

    de_novo = client.patch(f"/api/wa/conversations/{conversa_id}/lead", json={"lead_id": lead_id})
    assert de_novo.status_code == 409


def test_convite_adota_a_conversa_sem_lead_do_mesmo_numero(client):
    """Convidar quem já conversa pelo celular não pode abrir uma segunda conversa."""
    _conectar_coexistencia()
    # Pelo histórico, e não por um eco: um eco recente é um envio sem
    # resposta, e o portão recusa convite a quem não respondeu.
    _postar(client, "history", _historico([{"id": CONTATO, "messages": [
        _msg("wamid.H1", CONTATO, 1700000000, "oi")]}]))
    conversa_id = _conversas()[0].id
    lead_id = _lead("Acme", phone=CONTATO_E164)

    with patch("services.wa.client._post",
               return_value=wa_client.SendResult(True, wa_message_id="wamid.T1")):
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})

    assert resp.status_code == 200, resp.text
    conversas = _conversas()
    assert len(conversas) == 1
    assert conversas[0].id == conversa_id
    assert conversas[0].lead_id == lead_id


# ── O popup da Meta ──────────────────────────────────────────────────────────

def test_config_do_popup_diz_o_que_falta(client, monkeypatch):
    monkeypatch.delenv("META_ES_CONFIG_ID")
    cfg = client.get("/api/wa/connection/embedded/config").json()
    assert cfg["disponivel"] is False
    assert cfg["faltando"] == ["META_ES_CONFIG_ID"]
    assert cfg["feature_type"] == embedded.FEATURE_COEXISTENCIA


def test_popup_conecta_assina_e_pede_agenda_e_historico(client):
    numero = {"phone_number_id": MEU_PNID, "display_phone_number": "+55 11 90000-0000",
              "waba_id": "WABA9", "coexistencia": True}
    with patch.object(embedded, "trocar_codigo", return_value=(True, "token-novo")), \
         patch.object(embedded, "numero_da_conta", return_value=(True, numero)), \
         patch.object(embedded, "assinar_webhooks", return_value=(True, None)) as assinar, \
         patch.object(embedded, "sincronizar", return_value=(True, "req-1")) as sinc:
        resp = client.post("/api/wa/connection/embedded", json={
            "code": "codigo-do-popup", "waba_id": "WABA9",
            "evento": embedded.EVENTO_COEXISTENCIA,
        })

    assert resp.status_code == 200, resp.text
    dados = resp.json()
    assert dados["onboarding"] == ONBOARDING_COEXISTENCIA
    assert dados["historico_status"] == HISTORICO_SOLICITADO
    assert dados["avisos"] == []
    assinar.assert_called_once_with("WABA9", "token-novo")
    # Agenda antes do histórico: as conversas importadas já nascem com nome.
    assert [c.args[2] for c in sinc.call_args_list] == [
        embedded.SYNC_CONTATOS, embedded.SYNC_HISTORICO]

    db = _Session()
    try:
        conexao = db.query(WhatsAppConnection).one()
        assert conexao.access_token == "token-novo"
        # Assinado pelo App Secret do servidor, não por um da conta.
        assert not conexao.app_secret
    finally:
        db.close()


def test_popup_com_codigo_invalido_nao_grava_nada(client):
    with patch.object(embedded, "trocar_codigo",
                      return_value=(False, "A Meta recusou a autorização: code expired")):
        resp = client.post("/api/wa/connection/embedded",
                           json={"code": "velho", "waba_id": "WABA9"})
    assert resp.status_code == 422
    assert "expired" in resp.json()["detail"]
    db = _Session()
    try:
        assert db.query(WhatsAppConnection).count() == 0
    finally:
        db.close()


def test_falha_ao_pedir_historico_nao_desfaz_a_conexao(client):
    numero = {"phone_number_id": MEU_PNID, "waba_id": "WABA9", "coexistencia": True}
    with patch.object(embedded, "trocar_codigo", return_value=(True, "token-novo")), \
         patch.object(embedded, "numero_da_conta", return_value=(True, numero)), \
         patch.object(embedded, "assinar_webhooks", return_value=(True, None)), \
         patch.object(embedded, "sincronizar", return_value=(False, "janela de 24h expirou")):
        resp = client.post("/api/wa/connection/embedded",
                           json={"code": "c", "waba_id": "WABA9"})

    assert resp.status_code == 200
    dados = resp.json()
    assert dados["conectado"] is True
    assert dados["historico_status"] == "falhou"
    assert len(dados["avisos"]) == 2
