"""
A jornada inteira, de ponta a ponta.

Os outros arquivos de teste isolam peças: o portão, o turno, a tela, a
auditoria. Este percorre o caminho que uma conversa real percorre, do convite
ao opt-out, sem atalho nenhum — cada passo entra pela mesma porta que o
produto usa (a rota HTTP, o webhook assinado da Meta) e o estado de cada etapa
é o que a etapa anterior deixou.

O mock fica **só na fronteira de rede**: a Graph API da Meta e a API da
Anthropic. Tudo entre uma e outra — assinatura, portão, orquestrador, banco,
auditoria, métricas — roda de verdade.

É o teste que pega o que os testes de unidade não pegam: as peças passando no
próprio exame e mesmo assim não se encaixando.
"""
import hashlib
import hmac
import json
from datetime import timedelta
from itertools import count
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session  # noqa: F401

from models.database import (
    AI_ACTIVE, HUMAN_HANDOFF, STOPPED,
    AUDIT_ASSUMIDA, AUDIT_CONVERSA_ABERTA,
    AuditLog, Conversation, Lead, WaMessage,
    RELATIONSHIP_DO_NOT_CONTACT, utcnow,
)
from services.people import optout
from services.wa import brain, client as wa_client, gate

TELEFONE = "+5511988887777"
META_FROM = "5511988887777"
SEGREDO = "segredo-de-teste"

_wamid = count(1)


@pytest.fixture(autouse=True)
def servidor_completo(monkeypatch):
    """Um servidor com tudo ligado — e o horário fixo, para não depender do relógio."""
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SEGREDO)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "abre-te-sesamo")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token-de-teste")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "primeiro_contato")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-de-teste")
    monkeypatch.setattr(gate, "service_window", lambda agora=None: (True, False))
    # A consulta de qualidade do número é um GET à Graph API, fora do caminho
    # que esta jornada percorre. Sem este mock a suíte sai para a internet ao
    # pedir as métricas — lenta e capaz de falhar por motivo nenhum a ver com
    # o código. A qualidade tem teste próprio em `test_wa_auditoria.py`.
    monkeypatch.setattr(wa_client, "phone_quality", lambda: None)


class MetaFalsa:
    """
    A Graph API da Meta, no lugar onde ela realmente fica: o `requests.post`.

    Registra o que sairia pela rede, para o teste poder afirmar o que o lead
    receberia — sem nada entre o cliente HTTP e a asserção.
    """

    def __init__(self):
        self.enviadas = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.enviadas.append(json)

        class Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"messages": [{"id": f"wamid.SAIDA{next(_wamid)}"}]}

        return Resp()

    @property
    def textos(self):
        return [m["text"]["body"] for m in self.enviadas if m.get("type") == "text"]

    @property
    def templates(self):
        return [m["template"]["name"] for m in self.enviadas if m.get("type") == "template"]


def _ia_responde(intencao, rascunho="Claro! Posso te ligar amanhã às 10h?",
                 confianca=0.95):
    """A API da Anthropic devolvendo o JSON que o `brain` sabe ler."""
    corpo = json.dumps({"intencao": intencao, "confianca": confianca,
                        "rascunho": rascunho}, ensure_ascii=False)
    return patch.object(brain, "_chamar", return_value=corpo)


def _lead_com_telefone(client):
    db = _Session()
    try:
        lead = Lead(user_id="test-user-123", raw_input_domain="acme.com.br",
                    domain="acme.com.br", company_name="Acme", status="enriched",
                    phone=TELEFONE, relationship="LEAD")
        db.add(lead)
        db.commit()
        return lead.id
    finally:
        db.close()


def _lead_escreve(client, texto, wamid=None):
    """Uma mensagem entrando pelo webhook, assinada como a Meta assina."""
    evento = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "1234567890"},
            "messages": [{
                "from": META_FROM, "id": wamid or f"wamid.IN{next(_wamid)}",
                "timestamp": "1770000000", "type": "text",
                "text": {"body": texto},
            }],
        }}]}],
    }
    corpo = json.dumps(evento).encode("utf-8")
    assinatura = hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()
    return client.post("/api/wa/webhook", content=corpo,
                       headers={"x-hub-signature-256": f"sha256={assinatura}"})


def _conversa(client):
    return client.get("/api/wa/conversations").json()[0]


# ── A jornada boa: convite, conversa, ligação marcada ───────────────────────

def test_do_convite_ate_a_ligacao_marcada(client):
    """
    O caminho que o produto existe para percorrer. Cada passo entra pela porta
    real e vê o estado que o anterior deixou.
    """
    meta = MetaFalsa()
    lead_id = _lead_com_telefone(client)

    with patch("services.wa.client.requests.post", side_effect=meta.post):
        # 1. O humano dispara o convite (a ação paga).
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})
        assert resp.status_code == 200
        assert meta.templates == ["primeiro_contato"]

        # 2. O lead responde. O webhook grava e o turno roda em seguida.
        with _ia_responde(brain.CONFIRMOU_PESSOA, "Sou eu sim! Pode falar."):
            assert _lead_escreve(client, "sou eu mesmo, pode falar").status_code == 200

    # A automação respondeu, dentro da janela e sem custo.
    assert meta.textos == ["Sou eu sim! Pode falar."]

    conversa = _conversa(client)
    assert conversa["ai_status"] == AI_ACTIVE
    assert conversa["selo"]["tom"] == "ativa"
    assert conversa["janela_aberta"] is True

    # A conversa conta a história inteira, na ordem.
    det = client.get(f"/api/wa/conversations/{conversa['id']}").json()
    assert [(m["direction"], m["sent_by"]) for m in det["messages"]] == [
        ("out", "human"), ("in", None), ("out", "ai"),
    ]

    # E as métricas enxergam o que aconteceu.
    m = client.get("/api/wa/metrics").json()
    assert (m["convites_enviados"], m["leads_que_responderam"],
            m["respostas_da_ia"], m["taxa_de_resposta"]) == (1, 1, 1, 1.0)


# ── A jornada em que o humano assume ────────────────────────────────────────

def test_lead_que_negocia_para_na_mao_do_humano(client):
    """
    O gatilho mais importante: dinheiro na mesa sai do automático na hora, por
    mais confiante que a IA esteja.
    """
    meta = MetaFalsa()
    lead_id = _lead_com_telefone(client)

    with patch("services.wa.client.requests.post", side_effect=meta.post):
        client.post("/api/wa/start", json={"lead_id": lead_id})
        with _ia_responde(brain.NEGOCIANDO, rascunho="Nosso plano custa R$ 500."):
            _lead_escreve(client, "gostei. quanto custa pra 50 licenças?")

    # Nada foi respondido — nem o rascunho que a IA escreveu sobre preço.
    assert meta.textos == []

    conversa = _conversa(client)
    assert conversa["ai_status"] == HUMAN_HANDOFF
    assert conversa["aguardando_voce"] is True
    assert conversa["selo"]["tom"] == "aguardando"
    assert "interesse" in conversa["handoff_reason"].lower()

    # Aparece como pendência na barra lateral.
    assert client.get("/api/wa/status").json()["aguardando"] == 1

    # O humano responde pela tela e a pendência some.
    with patch("services.wa.client.requests.post", side_effect=meta.post):
        resp = client.post(f"/api/wa/conversations/{conversa['id']}/reply",
                           json={"texto": "Oi! Te ligo em 10 minutos para fechar isso."})
    assert resp.status_code == 200
    assert meta.textos == ["Oi! Te ligo em 10 minutos para fechar isso."]
    assert client.get("/api/wa/status").json()["aguardando"] == 0

    # A trilha distingue quem fez o quê.
    trilha = client.get(f"/api/wa/conversations/{conversa['id']}/audit").json()
    por_acao = {r["acao"]: r["ator"] for r in trilha}
    assert por_acao[AUDIT_CONVERSA_ABERTA] == "humano"
    assert por_acao[AUDIT_ASSUMIDA] == "ia"


# ── A jornada em que o lead pede para sair ──────────────────────────────────

def test_pedido_para_parar_encerra_e_bloqueia_o_numero(client):
    """
    O fim de linha que precisa funcionar sem falha: nenhuma mensagem depois,
    nem de confirmação, e o número bloqueado por hash — não só o lead.
    """
    meta = MetaFalsa()
    lead_id = _lead_com_telefone(client)

    with patch("services.wa.client.requests.post", side_effect=meta.post):
        client.post("/api/wa/start", json={"lead_id": lead_id})
        with _ia_responde(brain.PEDIU_PARAR):
            _lead_escreve(client, "não quero mais receber mensagens de vocês")

    assert meta.textos == []          # nem "ok, não mando mais"

    db = _Session()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).one()
        assert lead.relationship == RELATIONSHIP_DO_NOT_CONTACT
        assert optout.is_blocked(db, "phone", TELEFONE) is True
    finally:
        db.close()

    assert _conversa(client)["ai_status"] == STOPPED

    # E não dá para reabrir, nem pela porta da frente.
    outro = _lead_com_telefone(client)   # outro lead, mesmo número
    with patch("services.wa.client.requests.post", side_effect=meta.post):
        resp = client.post("/api/wa/start", json={"lead_id": outro})
    assert resp.status_code == 409
    assert meta.templates == ["primeiro_contato"]   # nenhum convite novo


# ── A jornada em que a automação falha ──────────────────────────────────────

def test_ia_fora_do_ar_vira_pendencia_e_o_lead_nao_se_perde(client):
    """
    O pior resultado possível seria o lead escrever e ninguém ficar sabendo.
    Com a IA fora do ar, a conversa precisa aparecer com badge na tela.
    """
    meta = MetaFalsa()
    lead_id = _lead_com_telefone(client)

    with patch("services.wa.client.requests.post", side_effect=meta.post):
        client.post("/api/wa/start", json={"lead_id": lead_id})
        with patch.object(brain, "_chamar", return_value=None):   # rede fora
            _lead_escreve(client, "oi, tudo bem?")

    assert meta.textos == []
    conversa = _conversa(client)
    assert conversa["ai_status"] == HUMAN_HANDOFF
    assert conversa["aguardando_voce"] is True
    assert client.get("/api/wa/status").json()["aguardando"] == 1

    # A mensagem do lead está guardada — nada se perdeu.
    det = client.get(f"/api/wa/conversations/{conversa['id']}").json()
    assert det["messages"][-1]["body"] == "oi, tudo bem?"


def test_reentrega_da_meta_nao_gera_segunda_resposta(client):
    """
    A Meta reentrega o que não recebeu 200 a tempo. Sem idempotência, uma
    lentidão nossa vira uma segunda mensagem no celular do lead.
    """
    meta = MetaFalsa()
    lead_id = _lead_com_telefone(client)

    with patch("services.wa.client.requests.post", side_effect=meta.post):
        client.post("/api/wa/start", json={"lead_id": lead_id})
        with _ia_responde(brain.CONVERSANDO, "Posso te ligar amanhã?"):
            _lead_escreve(client, "pode falar", wamid="wamid.REPETIDA")
            _lead_escreve(client, "pode falar", wamid="wamid.REPETIDA")

    assert meta.textos == ["Posso te ligar amanhã?"]

    db = _Session()
    try:
        assert db.query(WaMessage).filter(WaMessage.direction == "in").count() == 1
    finally:
        db.close()


# ── A jornada travada pelo relógio, retomada pelo cron ──────────────────────

def test_mensagem_da_madrugada_e_respondida_pelo_cron(client, monkeypatch):
    """
    O lead escreve às 3h. O portão recusa na hora — mandar mensagem comercial
    de madrugada é como se perde o número. O cron retoma quando amanhece, e é
    isso que impede o silêncio de virar lead perdido.
    """
    monkeypatch.setenv("CRON_SECRET", "segredo-do-cron")
    meta = MetaFalsa()
    lead_id = _lead_com_telefone(client)

    with patch("services.wa.client.requests.post", side_effect=meta.post):
        client.post("/api/wa/start", json={"lead_id": lead_id})

        # 3h da manhã: silêncio noturno.
        monkeypatch.setattr(gate, "service_window", lambda agora=None: (False, False))
        with _ia_responde(brain.CONVERSANDO, "Bom dia! Posso te ligar hoje?"):
            _lead_escreve(client, "oi, vi sua mensagem")
    assert meta.textos == []

    conversa = _conversa(client)
    assert conversa["ai_status"] == AI_ACTIVE      # continua no automático
    assert conversa["aguardando_voce"] is False    # não é pendência humana

    # Amanheceu. O cron passa e responde o que ficou para trás.
    monkeypatch.setattr(gate, "service_window", lambda agora=None: (True, False))
    with patch("services.wa.client.requests.post", side_effect=meta.post), \
         _ia_responde(brain.CONVERSANDO, "Bom dia! Posso te ligar hoje?"):
        resp = client.post("/api/internal/wa/pending",
                           headers={"Authorization": "Bearer segredo-do-cron"})

    assert resp.status_code == 200
    assert resp.json()["enviou"] == 1
    assert meta.textos == ["Bom dia! Posso te ligar hoje?"]


# ── A corrida entre a IA e o clique ─────────────────────────────────────────

def test_assumir_durante_a_chamada_da_ia_cancela_o_envio(client):
    """
    A corrida real: o lead escreve, a IA leva segundos para responder, e nesse
    intervalo o usuário clica em "Assumir agora". Sem o segundo portão, a
    automação responderia por cima dele.
    """
    meta = MetaFalsa()
    lead_id = _lead_com_telefone(client)

    with patch("services.wa.client.requests.post", side_effect=meta.post):
        client.post("/api/wa/start", json={"lead_id": lead_id})
        conversa_id = _conversa(client)["id"]

        def responder_e_assumir(prompt):
            # Acontece enquanto a chamada da IA está em curso.
            client.patch(f"/api/wa/conversations/{conversa_id}",
                         json={"acao": "assumir"})
            return json.dumps({"intencao": brain.CONVERSANDO, "confianca": 0.95,
                               "rascunho": "Posso te ligar amanhã?"})

        with patch.object(brain, "_chamar", side_effect=responder_e_assumir):
            _lead_escreve(client, "pode falar")

    assert meta.textos == []
    assert _conversa(client)["ai_status"] == HUMAN_HANDOFF


# ── Um convite que a Meta recusa ────────────────────────────────────────────

def test_convite_recusado_pela_meta_nao_deixa_rastro_falso(client):
    """O estado no banco tem que corresponder ao que de fato saiu."""
    lead_id = _lead_com_telefone(client)

    class MetaRecusando:
        status_code = 400

        @staticmethod
        def json():
            return {"error": {"code": 132000, "message": "template não aprovado"}}

    with patch("services.wa.client.requests.post", return_value=MetaRecusando()):
        resp = client.post("/api/wa/start", json={"lead_id": lead_id})

    assert resp.status_code == 502
    assert client.get("/api/wa/conversations").json() == []

    db = _Session()
    try:
        assert db.query(Conversation).count() == 0
        assert db.query(WaMessage).count() == 0
        assert db.query(AuditLog).count() == 0
    finally:
        db.close()
