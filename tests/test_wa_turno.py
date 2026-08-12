"""
O turno automático: a IA lendo, o código decidindo.

Nenhum teste aqui chama a API da Anthropic nem a da Meta — as duas são
mockadas. O que se verifica é a costura: que cada intenção leve à ação certa,
que nada saia quando há dúvida, e que uma falha vire pendência humana em vez
de silêncio.

O teste mais importante do arquivo é
`test_assumir_durante_a_leitura_cancela_o_envio`: ele reproduz o intervalo real
entre a IA responder e a mensagem sair, que é onde o usuário clica em "Assumir
agora".
"""
from datetime import timedelta
from unittest.mock import patch

import pytest

from tests.test_api import clean_db, _Session  # noqa: F401

from models.database import (
    AI_ACTIVE, AI_PAUSED, HUMAN_HANDOFF, STOPPED,
    Conversation, Lead, WaMessage, RELATIONSHIP_CUSTOMER,
    RELATIONSHIP_DO_NOT_CONTACT, utcnow,
)
from services.people import optout
from services.wa import brain, client as wa_client, gate, orchestrator, states

TELEFONE = "+5511988887777"


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    """Servidor configurado, horário comercial, IA ligada — tudo mockado."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-de-teste")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token-de-teste")
    monkeypatch.setattr(gate, "service_window", lambda agora=None: (True, False))


@pytest.fixture
def db():
    sessao = _Session()
    try:
        yield sessao
    finally:
        sessao.close()


def _conversa(db, texto_do_lead="oi, quem fala?", ai_status=AI_ACTIVE,
              relationship="LEAD"):
    """Conversa com um convite enviado e uma resposta do lead esperando turno."""
    lead = Lead(user_id="test-user-123", raw_input_domain="acme.com.br",
                domain="acme.com.br", company_name="Acme", status="enriched",
                relationship=relationship)
    db.add(lead)
    db.commit()

    conversa = Conversation(
        lead_id=lead.id, user_id="test-user-123", phone_e164=TELEFONE,
        ai_status=ai_status,
        window_expires_at=utcnow() + timedelta(hours=20),
        last_outbound_at=utcnow() - timedelta(hours=2),
        last_inbound_at=utcnow(),
    )
    db.add(conversa)
    db.commit()

    db.add(WaMessage(conversation_id=conversa.id, direction="out",
                     type="template", body="[template]", sent_by="human"))
    db.add(WaMessage(conversation_id=conversa.id, direction="in",
                     type="text", body=texto_do_lead))
    db.commit()
    return lead, conversa


def _leitura(intencao, confianca=0.95, rascunho="Claro! Posso te ligar amanhã às 10h?"):
    return brain.Leitura(intencao, confianca, rascunho=rascunho)


def _enviou(db, conversa):
    return (db.query(WaMessage)
            .filter(WaMessage.conversation_id == conversa.id,
                    WaMessage.sent_by == "ai").count())


# ── O caminho em que a automação responde ───────────────────────────────────

@pytest.mark.parametrize("intencao", [brain.CONFIRMOU_PESSOA, brain.CONVERSANDO])
def test_intencoes_inofensivas_recebem_resposta_automatica(db, intencao):
    lead, conversa = _conversa(db)
    with patch.object(brain, "ler", return_value=_leitura(intencao)), \
         patch.object(wa_client, "send_text",
                      return_value=wa_client.SendResult(True, "wamid.IA")) as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.ENVIOU
    assert envio.call_args[0][0] == TELEFONE

    msg = db.query(WaMessage).filter(WaMessage.wa_message_id == "wamid.IA").one()
    assert msg.sent_by == "ai"
    assert msg.intent_detected == intencao


# ── Os casos em que ela chama o humano ──────────────────────────────────────

@pytest.mark.parametrize("intencao", [
    brain.QUER_HUMANO, brain.NEGOCIANDO, brain.PESSOA_ERRADA,
    brain.FORA_DA_BASE, brain.AMBIGUO,
])
def test_intencoes_delicadas_passam_para_o_humano_sem_enviar(db, intencao):
    """
    Tudo que envolve dinheiro, compromisso ou desconforto sai do automático —
    mesmo com a IA dizendo que tem certeza.
    """
    lead, conversa = _conversa(db)
    with patch.object(brain, "ler", return_value=_leitura(intencao)), \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.CHAMOU_HUMANO
    assert envio.call_count == 0
    assert conversa.ai_status == HUMAN_HANDOFF
    assert conversa.handoff_reason


def test_motivo_do_handoff_explica_o_que_aconteceu(db):
    lead, conversa = _conversa(db)
    with patch.object(brain, "ler", return_value=_leitura(brain.NEGOCIANDO)), \
         patch.object(wa_client, "send_text"):
        turno = orchestrator.responder(db, conversa)
    assert "interesse" in turno.motivo.lower()


# ── As duas marcações estruturais ───────────────────────────────────────────

def test_lead_que_diz_ser_cliente_e_marcado_e_a_conversa_encerra(db):
    lead, conversa = _conversa(db, "opa, já somos clientes de vocês")
    with patch.object(brain, "ler", return_value=_leitura(brain.JA_E_CLIENTE)), \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.ENCERROU
    assert envio.call_count == 0
    db.refresh(lead); db.refresh(conversa)
    assert lead.relationship == RELATIONSHIP_CUSTOMER
    assert conversa.ai_status == STOPPED


def test_pedido_para_parar_bloqueia_o_numero(db):
    """Não basta encerrar a conversa: o número não pode voltar por outra porta."""
    lead, conversa = _conversa(db, "não quero mais receber mensagens")
    with patch.object(brain, "ler", return_value=_leitura(brain.PEDIU_PARAR)), \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.ENCERROU
    assert envio.call_count == 0
    db.refresh(lead)
    assert lead.relationship == RELATIONSHIP_DO_NOT_CONTACT
    assert optout.is_blocked(db, "phone", TELEFONE) is True


def test_quem_pediu_para_parar_nao_recebe_nem_confirmacao(db):
    """Responder "ok, não mando mais" ainda é mandar mais uma."""
    lead, conversa = _conversa(db, "para de me mandar mensagem")
    with patch.object(brain, "ler", return_value=_leitura(brain.PEDIU_PARAR)), \
         patch.object(wa_client, "send_text") as envio:
        orchestrator.responder(db, conversa)
    assert envio.call_count == 0
    assert _enviou(db, conversa) == 0


# ── Dúvida nunca vira envio ─────────────────────────────────────────────────

def test_confianca_baixa_nao_envia(db):
    lead, conversa = _conversa(db)
    with patch.object(brain, "ler",
                      return_value=_leitura(brain.CONVERSANDO, confianca=0.3)), \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.CHAMOU_HUMANO
    assert envio.call_count == 0


def test_ia_fora_do_ar_vira_pendencia_e_nao_silencio(db):
    """
    O pior resultado possível é o lead escrever e ninguém ficar sabendo. Falha
    da automação tem que aparecer na tela como conversa esperando resposta.
    """
    lead, conversa = _conversa(db)
    with patch.object(brain, "ler",
                      return_value=brain.Leitura(brain.AMBIGUO, 0.0,
                                                 erro="A IA não respondeu.")), \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.CHAMOU_HUMANO
    assert conversa.ai_status == HUMAN_HANDOFF
    assert envio.call_count == 0


def test_excecao_inesperada_tambem_vira_pendencia(db):
    """Exceção que subisse daqui viraria 500 no webhook — e reentrega da Meta."""
    lead, conversa = _conversa(db)
    with patch.object(brain, "ler", side_effect=RuntimeError("estourou")), \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.CHAMOU_HUMANO
    assert envio.call_count == 0
    db.refresh(conversa)
    assert conversa.ai_status == HUMAN_HANDOFF


def test_rascunho_vazio_nao_envia_mensagem_em_branco(db):
    lead, conversa = _conversa(db)
    with patch.object(brain, "ler",
                      return_value=_leitura(brain.CONVERSANDO, rascunho=None)), \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.CHAMOU_HUMANO
    assert envio.call_count == 0


def test_ia_nao_configurada_passa_para_o_humano(db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lead, conversa = _conversa(db)
    with patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)
    assert turno.acao == orchestrator.CHAMOU_HUMANO
    assert envio.call_count == 0


def test_meta_recusando_a_resposta_vira_pendencia(db):
    lead, conversa = _conversa(db)
    recusa = wa_client.SendResult(False, error="131047: fora da janela")
    with patch.object(brain, "ler", return_value=_leitura(brain.CONVERSANDO)), \
         patch.object(wa_client, "send_text", return_value=recusa):
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.CHAMOU_HUMANO
    assert _enviou(db, conversa) == 0


# ── A corrida entre a IA e o usuário ────────────────────────────────────────

def test_assumir_durante_a_leitura_cancela_o_envio(db):
    """
    Entre a IA ler e a mensagem sair passam segundos de rede — e é exatamente
    aí que o usuário clica em "Assumir agora". Sem o segundo portão, a
    automação responderia por cima de quem acabou de tomar a conversa.
    """
    lead, conversa = _conversa(db)

    def ler_e_assumir(*args, **kwargs):
        # Simula o clique acontecendo enquanto a chamada da IA está em curso.
        outra = _Session()
        try:
            copia = outra.query(Conversation).filter(Conversation.id == conversa.id).one()
            copia.ai_status = HUMAN_HANDOFF
            outra.commit()
        finally:
            outra.close()
        return _leitura(brain.CONVERSANDO)

    with patch.object(brain, "ler", side_effect=ler_e_assumir), \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.NAO_FEZ_NADA
    assert envio.call_count == 0


# ── O portão barrando antes mesmo da IA ─────────────────────────────────────

def test_cliente_atual_nao_chega_a_ser_lido_pela_ia(db):
    lead, conversa = _conversa(db, relationship=RELATIONSHIP_CUSTOMER)
    with patch.object(brain, "ler") as leitura, \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.NAO_FEZ_NADA
    assert leitura.call_count == 0    # nem gastou a chamada
    assert envio.call_count == 0


def test_conversa_pausada_nao_gera_turno(db):
    lead, conversa = _conversa(db, ai_status=AI_PAUSED)
    with patch.object(brain, "ler") as leitura:
        turno = orchestrator.responder(db, conversa)
    assert turno.acao == orchestrator.NAO_FEZ_NADA
    assert leitura.call_count == 0


def test_janela_fechada_vira_pendencia(db):
    """Aqui o silêncio seria definitivo, então precisa aparecer para alguém."""
    lead, conversa = _conversa(db)
    conversa.window_expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    with patch.object(brain, "ler") as leitura:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.CHAMOU_HUMANO
    assert leitura.call_count == 0


def test_madrugada_nao_responde_nem_marca_pendencia(db, monkeypatch):
    """Recusa temporária: o cron retoma no horário, ninguém precisa ser chamado."""
    monkeypatch.setattr(gate, "service_window", lambda agora=None: (False, False))
    lead, conversa = _conversa(db)
    with patch.object(brain, "ler") as leitura:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.NAO_FEZ_NADA
    assert leitura.call_count == 0
    assert conversa.ai_status == AI_ACTIVE   # continua no automático


def test_turno_nao_roda_quando_a_ultima_palavra_foi_nossa(db):
    """Protege contra responder duas vezes à mesma mensagem."""
    lead, conversa = _conversa(db)
    db.add(WaMessage(conversation_id=conversa.id, direction="out",
                     type="text", body="já respondi", sent_by="ai"))
    db.commit()

    with patch.object(brain, "ler") as leitura:
        turno = orchestrator.responder(db, conversa)
    assert turno.acao == orchestrator.NAO_FEZ_NADA
    assert leitura.call_count == 0


# ── Fora do expediente ──────────────────────────────────────────────────────

def test_fora_do_expediente_responde_em_modo_curto(db, monkeypatch):
    monkeypatch.setattr(gate, "service_window", lambda agora=None: (True, True))
    lead, conversa = _conversa(db)

    with patch.object(brain, "ler", return_value=_leitura(brain.CONVERSANDO)) as leitura, \
         patch.object(wa_client, "send_text",
                      return_value=wa_client.SendResult(True, "wamid.X")):
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.ENVIOU
    assert leitura.call_args.kwargs["fora_do_horario"] is True
    assert conversa.after_hours_turns == 1


def test_teto_de_trocas_fora_do_expediente_silencia(db, monkeypatch):
    """O limite é uma regra em código, não uma instrução no prompt."""
    monkeypatch.setattr(gate, "service_window", lambda agora=None: (True, True))
    lead, conversa = _conversa(db)
    conversa.after_hours_turns = orchestrator.MAX_TURNOS_FORA_DO_HORARIO
    db.commit()

    with patch.object(brain, "ler") as leitura, \
         patch.object(wa_client, "send_text") as envio:
        turno = orchestrator.responder(db, conversa)

    assert turno.acao == orchestrator.NAO_FEZ_NADA
    assert leitura.call_count == 0
    assert envio.call_count == 0


def test_turno_em_horario_comercial_zera_o_contador(db):
    """O teto é por noite ou fim de semana, não para a vida da conversa."""
    lead, conversa = _conversa(db)
    conversa.after_hours_turns = 2
    db.commit()

    with patch.object(brain, "ler", return_value=_leitura(brain.CONVERSANDO)), \
         patch.object(wa_client, "send_text",
                      return_value=wa_client.SendResult(True, "wamid.Y")):
        orchestrator.responder(db, conversa)

    assert conversa.after_hours_turns == 0


# ── Retomada pelo cron ──────────────────────────────────────────────────────

def test_conversa_sem_resposta_entra_na_fila_de_retomada(db):
    lead, conversa = _conversa(db)
    assert [c.id for c in orchestrator.conversas_pendentes(db)] == [conversa.id]


def test_conversa_ja_respondida_nao_entra_na_retomada(db):
    lead, conversa = _conversa(db)
    conversa.last_outbound_at = utcnow() + timedelta(minutes=1)
    db.commit()
    assert orchestrator.conversas_pendentes(db) == []


def test_conversa_assumida_pelo_humano_nao_entra_na_retomada(db):
    lead, conversa = _conversa(db)
    states.handoff(db, conversa, "assumida")
    db.commit()
    assert orchestrator.conversas_pendentes(db) == []


def test_rodada_de_retomada_responde_o_que_ficou_para_tras(db):
    lead, conversa = _conversa(db)
    with patch.object(brain, "ler", return_value=_leitura(brain.CONVERSANDO)), \
         patch.object(wa_client, "send_text",
                      return_value=wa_client.SendResult(True, "wamid.Z")):
        resumo = orchestrator.rodar_pendentes(db)

    assert resumo[orchestrator.ENVIOU] == 1


# ── A leitura da IA, isolada ────────────────────────────────────────────────

def test_json_quebrado_vira_ambiguo(monkeypatch):
    monkeypatch.setattr(brain, "_chamar", lambda prompt: "desculpa, não consegui")
    leitura = brain.ler([{"direction": "in", "body": "oi"}])
    assert leitura.intencao == brain.AMBIGUO
    assert leitura.confiavel is False


def test_json_dentro_de_cerca_de_codigo_e_lido(monkeypatch):
    """O modelo às vezes embrulha em ```json apesar da instrução."""
    monkeypatch.setattr(brain, "_chamar", lambda prompt:
                        '```json\n{"intencao":"CONVERSANDO","confianca":0.9,'
                        '"rascunho":"Podemos falar amanhã?"}\n```')
    leitura = brain.ler([{"direction": "in", "body": "oi"}])
    assert leitura.intencao == brain.CONVERSANDO
    assert leitura.confiavel is True


def test_intencao_inventada_pelo_modelo_vira_ambiguo(monkeypatch):
    monkeypatch.setattr(brain, "_chamar", lambda prompt:
                        '{"intencao":"QUER_DESCONTO","confianca":0.99}')
    leitura = brain.ler([{"direction": "in", "body": "oi"}])
    assert leitura.intencao == brain.AMBIGUO


def test_confianca_fora_da_escala_e_contida(monkeypatch):
    monkeypatch.setattr(brain, "_chamar", lambda prompt:
                        '{"intencao":"CONVERSANDO","confianca":42,"rascunho":"oi"}')
    leitura = brain.ler([{"direction": "in", "body": "oi"}])
    assert leitura.confianca == 1.0


def test_rede_fora_vira_ambiguo(monkeypatch):
    monkeypatch.setattr(brain, "_chamar", lambda prompt: None)
    leitura = brain.ler([{"direction": "in", "body": "oi"}])
    assert leitura.intencao == brain.AMBIGUO
    assert leitura.erro
