"""
O portão de envio e as transições de estado da conversa.

Este é o arquivo de teste mais importante do contato automatizado. Cada caso
aqui corresponde a um jeito concreto de o sistema falar com quem não devia —
um cliente atual, alguém que pediu para parar, uma conversa que o usuário
acabou de assumir, um número às três da manhã. O portão nega por padrão; os
testes existem para provar que ele continua negando quando alguém mexer nele.
"""
from datetime import datetime, timedelta, UTC

import pytest

from tests.test_api import clean_db, _Session  # noqa: F401

from models.database import (
    AI_ACTIVE, AI_PAUSED, HUMAN_HANDOFF, STOPPED,
    Conversation, Lead, RELATIONSHIP_BLOCKED, RELATIONSHIP_CUSTOMER,
    RELATIONSHIP_DO_NOT_CONTACT, utcnow,
)
from services.people import optout
from services.wa import gate, states, webhook

TELEFONE = "+5511988887777"

# Todos os instantes são fixos e explícitos: horário de atendimento e janela de
# 24 h dependem do relógio, e teste que usa "agora" de verdade passa às 14h e
# falha às 23h. O fuso de referência é America/Sao_Paulo (UTC-3 o ano todo,
# desde o fim do horário de verão em 2019).
COMERCIAL = datetime(2026, 8, 11, 17, 0, tzinfo=UTC)        # terça, 14h SP
FORA_DO_HORARIO = datetime(2026, 8, 11, 11, 30, tzinfo=UTC)  # terça, 8h30 SP
MADRUGADA = datetime(2026, 8, 11, 6, 0, tzinfo=UTC)          # terça, 3h SP
SABADO = datetime(2026, 8, 15, 17, 0, tzinfo=UTC)            # sábado, 14h SP


@pytest.fixture
def db():
    sessao = _Session()
    try:
        yield sessao
    finally:
        sessao.close()


def _conversa(db, relationship="LEAD", ai_status=AI_ACTIVE,
              janela_horas=12, phone=TELEFONE, agora=COMERCIAL):
    """
    Uma conversa pronta para enviar — cada teste estraga só uma condição.

    A janela nasce ancorada em `agora`, não no relógio real: senão o teste de
    sábado montaria uma janela que vence antes do instante que ele simula.
    """
    lead = Lead(user_id="test-user-123", raw_input_domain="acme.com.br",
                domain="acme.com.br", status="enriched", relationship=relationship)
    db.add(lead)
    db.commit()

    conversa = Conversation(
        lead_id=lead.id, user_id="test-user-123", phone_e164=phone,
        ai_status=ai_status,
        window_expires_at=agora + timedelta(hours=janela_horas) if janela_horas else None,
    )
    db.add(conversa)
    db.commit()
    return lead, conversa


# ── O caminho feliz, para os outros testes significarem alguma coisa ─────────

def test_lead_com_ia_ativa_e_janela_aberta_pode_receber(db):
    _, conversa = _conversa(db)
    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is True
    assert decisao.after_hours is False


# ── A trava estrutural: quem não é lead nunca recebe ────────────────────────

@pytest.mark.parametrize("relacao", [
    RELATIONSHIP_CUSTOMER, RELATIONSHIP_DO_NOT_CONTACT, RELATIONSHIP_BLOCKED,
])
def test_quem_nao_e_lead_nunca_recebe(db, relacao):
    _, conversa = _conversa(db, relationship=relacao)
    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_NOT_A_LEAD


def test_cliente_atual_e_barrado_mesmo_com_conversa_ativa(db):
    """
    O caso que o sistema inteiro existe para impedir: a conversa está ativa, a
    janela aberta, o horário certo — e a empresa virou cliente no meio.
    """
    lead, conversa = _conversa(db)
    assert gate.can_send(db, conversa, agora=COMERCIAL).allowed is True

    states.mark_customer(db, lead)
    db.commit()

    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason in (gate.DENY_NOT_A_LEAD, gate.DENY_AI_NOT_ACTIVE)


def test_marcar_como_cliente_encerra_as_conversas_abertas(db):
    lead, conversa = _conversa(db)
    states.mark_customer(db, lead)
    db.commit()
    db.refresh(conversa)
    assert conversa.ai_status == STOPPED


# ── Controle humano: vale para a próxima mensagem, não para a seguinte ──────

def test_pausar_interrompe_antes_do_proximo_envio(db):
    lead, conversa = _conversa(db)
    assert gate.can_send(db, conversa, agora=COMERCIAL).allowed is True

    states.pause(db, conversa)
    db.commit()

    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_AI_NOT_ACTIVE


def test_assumir_interrompe_antes_do_proximo_envio(db):
    lead, conversa = _conversa(db)
    states.handoff(db, conversa, "O usuário assumiu.")
    db.commit()

    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_AI_NOT_ACTIVE


def test_pausa_gravada_por_outra_sessao_ja_vale(db):
    """
    O que garante o "imediatamente": o portão relê do banco. Se ele confiasse
    no objeto que já tinha em mãos, o clique em "Pausar" numa aba não seria
    visto pelo turno que está rodando na função serverless.
    """
    lead, conversa = _conversa(db)

    outra = _Session()
    try:
        copia = outra.query(Conversation).filter(Conversation.id == conversa.id).first()
        copia.ai_status = AI_PAUSED
        outra.commit()
    finally:
        outra.close()

    assert gate.can_send(db, conversa, agora=COMERCIAL).allowed is False


def test_retomar_devolve_a_conversa_para_a_automacao(db):
    lead, conversa = _conversa(db, ai_status=AI_PAUSED)
    states.resume(db, conversa)
    db.commit()
    assert gate.can_send(db, conversa, agora=COMERCIAL).allowed is True


def test_encerrada_nao_volta_sozinha(db):
    lead, conversa = _conversa(db, ai_status=STOPPED)
    assert gate.can_send(db, conversa, agora=COMERCIAL).allowed is False


# ── LGPD: pediu para parar, para ─────────────────────────────────────────────

def test_numero_com_opt_out_e_barrado(db):
    lead, conversa = _conversa(db)
    optout.register(db, "phone", TELEFONE, source="teste")
    db.commit()

    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_OPTED_OUT


def test_pedir_para_parar_bloqueia_o_numero_e_nao_so_o_lead(db):
    """
    A promessa é sobre o número, não sobre a linha da tabela. Se só o lead
    fosse marcado, o mesmo celular voltaria a receber pela próxima planilha
    importada — com outra empresa e outro id.
    """
    lead, conversa = _conversa(db)
    states.mark_do_not_contact(db, lead)
    db.commit()

    assert lead.relationship == RELATIONSHIP_DO_NOT_CONTACT
    assert optout.is_blocked(db, "phone", TELEFONE) is True


def test_voltar_a_marcar_como_lead_nao_desfaz_o_opt_out(db):
    lead, conversa = _conversa(db)
    states.mark_do_not_contact(db, lead)
    db.commit()

    states.mark_lead(db, lead)
    states.resume(db, conversa)
    db.commit()

    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_OPTED_OUT


# ── Janela de 24 h da Meta ───────────────────────────────────────────────────

def test_janela_vencida_bloqueia(db):
    lead, conversa = _conversa(db)
    conversa.window_expires_at = COMERCIAL - timedelta(minutes=1)
    db.commit()

    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_WINDOW_CLOSED


def test_conversa_sem_janela_nao_e_tratada_como_prazo_infinito(db):
    """Conversa que o lead nunca respondeu não tem janela livre nenhuma."""
    lead, conversa = _conversa(db, janela_horas=None)
    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_WINDOW_CLOSED


def test_mensagem_recebida_reabre_a_janela(db):
    lead, conversa = _conversa(db, janela_horas=None)
    states.register_inbound(db, conversa, "oi, quem fala?", quando=COMERCIAL)
    db.commit()

    assert gate.can_send(db, conversa, agora=COMERCIAL).allowed is True
    assert conversa.last_message_body == "oi, quem fala?"


def test_resposta_enviada_nao_estende_a_janela(db):
    lead, conversa = _conversa(db)
    prazo = conversa.window_expires_at
    states.register_outbound(db, conversa, "olá!")
    db.commit()
    assert conversa.window_expires_at == prazo


# ── Horário ──────────────────────────────────────────────────────────────────

def test_madrugada_nao_envia_nada(db):
    lead, conversa = _conversa(db)
    decisao = gate.can_send(db, conversa, agora=MADRUGADA)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_QUIET_HOURS


def test_fora_do_expediente_envia_em_modo_curto(db):
    """Sumir com quem acabou de escrever custa o lead; responder curto, não."""
    lead, conversa = _conversa(db)
    decisao = gate.can_send(db, conversa, agora=FORA_DO_HORARIO)
    assert decisao.allowed is True
    assert decisao.after_hours is True


def test_sabado_conta_como_fora_do_expediente(db):
    lead, conversa = _conversa(db, agora=SABADO)
    decisao = gate.can_send(db, conversa, agora=SABADO)
    assert decisao.allowed is True
    assert decisao.after_hours is True


def test_janela_de_servico_sem_fuso_disponivel_nega(monkeypatch):
    """Horário indeterminado é motivo para não enviar, não para enviar."""
    monkeypatch.setattr(gate, "_zona", lambda: None)
    assert gate.service_window(COMERCIAL) == (False, False)


# ── Casos degenerados ────────────────────────────────────────────────────────

def test_conversa_sem_telefone_e_recusada(db):
    lead, conversa = _conversa(db, phone="")
    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_NO_PHONE


def test_conversa_cujo_lead_sumiu_e_recusada(db):
    lead, conversa = _conversa(db)
    db.delete(lead)
    db.commit()

    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_LEAD_MISSING


def test_recusa_sempre_tem_explicacao_em_portugues(db):
    lead, conversa = _conversa(db, relationship=RELATIONSHIP_CUSTOMER)
    decisao = gate.can_send(db, conversa, agora=COMERCIAL)
    assert decisao.message and decisao.message != "Envio não autorizado."


# ── Abertura da conversa ─────────────────────────────────────────────────────

def test_abrir_conversa_duas_vezes_nao_duplica(db):
    lead, conversa = _conversa(db)
    de_novo = states.start(db, lead, TELEFONE, "test-user-123")
    db.commit()
    assert de_novo.id == conversa.id
    assert db.query(Conversation).filter(Conversation.lead_id == lead.id).count() == 1


def test_reabrir_conversa_de_cliente_nao_libera_envio(db):
    """Reabrir é permitido; furar a trava, não."""
    lead, conversa = _conversa(db, relationship=RELATIONSHIP_CUSTOMER)
    states.start(db, lead, TELEFONE, "test-user-123")
    db.commit()
    assert gate.can_send(db, conversa, agora=COMERCIAL).allowed is False


# ── Portão de abertura: o primeiro contato, que é pago ──────────────────────

def test_abertura_e_permitida_para_lead_sem_conversa(db):
    lead, _ = _conversa(db)
    db.query(Conversation).delete()
    db.commit()
    assert gate.can_start(db, lead, TELEFONE, None, agora=COMERCIAL).allowed is True


def test_abertura_nao_exige_janela_aberta(db):
    """
    O primeiro contato é, por definição, fora da janela de 24 h. Se ele
    passasse pelo `can_send`, toda abertura seria recusada — e a saída seria
    alguém desligar a checagem de janela para os dois casos.
    """
    lead, conversa = _conversa(db, janela_horas=None)
    assert gate.can_send(db, conversa, agora=COMERCIAL).reason == gate.DENY_WINDOW_CLOSED
    assert gate.can_start(db, lead, TELEFONE, conversa, agora=COMERCIAL).allowed is True


def test_abertura_barrada_para_quem_nao_e_lead(db):
    lead, _ = _conversa(db, relationship=RELATIONSHIP_CUSTOMER)
    decisao = gate.can_start(db, lead, TELEFONE, None, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_NOT_A_LEAD


def test_abertura_barrada_na_madrugada(db):
    lead, _ = _conversa(db)
    decisao = gate.can_start(db, lead, TELEFONE, None, agora=MADRUGADA)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_QUIET_HOURS


def test_abertura_barrada_quando_a_conversa_ja_esta_aberta(db):
    """Lead respondeu: a janela está aberta e a resposta é de graça."""
    lead, conversa = _conversa(db)
    decisao = gate.can_start(db, lead, TELEFONE, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_ALREADY_OPEN


def test_abertura_barrada_enquanto_o_convite_nao_foi_respondido(db):
    lead, conversa = _conversa(db, janela_horas=None)
    conversa.last_outbound_at = COMERCIAL - timedelta(hours=2)
    db.commit()

    decisao = gate.can_start(db, lead, TELEFONE, conversa, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_AWAITING_REPLY


def test_convite_pode_ser_repetido_depois_da_espera(db):
    """Insistir é diferente de nunca mais tentar; a diferença é o intervalo."""
    lead, conversa = _conversa(db, janela_horas=None)
    conversa.last_outbound_at = (
        COMERCIAL - timedelta(hours=gate.TEMPLATE_RETRY_HOURS + 1)
    )
    db.commit()

    assert gate.can_start(db, lead, TELEFONE, conversa, agora=COMERCIAL).allowed is True


def test_quem_respondeu_e_sumiu_pode_receber_novo_convite(db):
    """A janela fechou, mas houve resposta — não é insistência, é retomada."""
    lead, conversa = _conversa(db, janela_horas=None)
    conversa.last_outbound_at = COMERCIAL - timedelta(hours=5)
    conversa.last_inbound_at = COMERCIAL - timedelta(hours=4)
    db.commit()

    assert gate.can_start(db, lead, TELEFONE, conversa, agora=COMERCIAL).allowed is True


def test_abertura_sem_telefone_e_recusada(db):
    lead, _ = _conversa(db)
    decisao = gate.can_start(db, lead, "", None, agora=COMERCIAL)
    assert decisao.allowed is False
    assert decisao.reason == gate.DENY_NO_PHONE


# ── Escrita validada do relacionamento ───────────────────────────────────────

def test_relacionamento_invalido_e_recusado_na_escrita(db):
    """
    A coluna é um varchar: "CLIENTE" entraria sem erro e viraria um lead que o
    portão recusa para sempre, sem ninguém entender por quê.
    """
    lead, _ = _conversa(db)
    with pytest.raises(ValueError):
        states.set_relationship(db, lead, "CLIENTE")


@pytest.mark.parametrize("valor", [
    RELATIONSHIP_CUSTOMER, RELATIONSHIP_DO_NOT_CONTACT, RELATIONSHIP_BLOCKED,
])
def test_qualquer_relacionamento_que_nao_seja_lead_encerra_a_conversa(db, valor):
    lead, conversa = _conversa(db)
    states.set_relationship(db, lead, valor)
    db.commit()

    db.refresh(conversa)
    assert conversa.ai_status == STOPPED
    assert gate.can_send(db, conversa, agora=COMERCIAL).allowed is False


# ── Assinatura do webhook da Meta ────────────────────────────────────────────

CORPO = b'{"entry":[{"changes":[]}]}'
SEGREDO = "segredo-de-teste"


def test_assinatura_correta_e_aceita():
    assinatura = webhook.expected_signature(CORPO, SEGREDO)
    assert webhook.verify_signature(CORPO, assinatura, SEGREDO) is True


def test_assinatura_de_outro_corpo_e_recusada():
    assinatura = webhook.expected_signature(b'{"outro":true}', SEGREDO)
    assert webhook.verify_signature(CORPO, assinatura, SEGREDO) is False


def test_assinatura_com_outro_segredo_e_recusada():
    assinatura = webhook.expected_signature(CORPO, "segredo-errado")
    assert webhook.verify_signature(CORPO, assinatura, SEGREDO) is False


def test_requisicao_sem_assinatura_e_recusada():
    assert webhook.verify_signature(CORPO, None, SEGREDO) is False
    assert webhook.verify_signature(CORPO, "", SEGREDO) is False


def test_assinatura_sem_o_prefixo_sha256_e_recusada():
    digest = webhook.expected_signature(CORPO, SEGREDO).removeprefix("sha256=")
    assert webhook.verify_signature(CORPO, digest, SEGREDO) is False


def test_sem_segredo_configurado_o_webhook_recusa(monkeypatch):
    """Variável esquecida no deploy não pode virar endpoint aberto."""
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    assinatura = webhook.expected_signature(CORPO, SEGREDO)
    assert webhook.verify_signature(CORPO, assinatura) is False


def test_token_de_verificacao_confere(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "abre-te-sesamo")
    assert webhook.check_verify_token("abre-te-sesamo") is True
    assert webhook.check_verify_token("outro") is False
    assert webhook.check_verify_token(None) is False
