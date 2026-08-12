"""
O cliente da Meta e a chamada da IA, no nível do HTTP.

Todo o resto da suíte mocka estas duas funções — o que é certo, porque o
assunto lá é a decisão, não o transporte. O efeito colateral é que o código
que fala com a rede era justamente o que nunca rodava em teste: ele só
executaria pela primeira vez em produção, com dinheiro e com o número do
cliente em jogo.

Aqui o mock desce um nível: `requests` é substituído, e o que se verifica é o
que sai no corpo, o que se faz com o que volta, e o que acontece quando o
outro lado responde errado.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from services.wa import brain, client as wa_client


@pytest.fixture(autouse=True)
def credenciais(monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token-de-teste")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "primeiro_contato")


def _resposta(status=200, corpo=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = corpo if corpo is not None else {}
    return m


# ── Envio ────────────────────────────────────────────────────────────────────

def test_texto_sai_no_formato_que_a_meta_espera():
    envio = _resposta(200, {"messages": [{"id": "wamid.ABC"}]})
    with patch.object(wa_client.requests, "post", return_value=envio) as post:
        r = wa_client.send_text("+5511988887777", "Olá!")

    assert r.ok is True
    assert r.wa_message_id == "wamid.ABC"

    corpo = post.call_args.kwargs["json"]
    assert corpo["messaging_product"] == "whatsapp"
    assert corpo["to"] == "+5511988887777"
    assert corpo["text"]["body"] == "Olá!"
    # A prévia de link muda o tamanho da mensagem e o que o destinatário vê.
    assert corpo["text"]["preview_url"] is False


def test_texto_longo_e_cortado_no_limite_da_meta():
    with patch.object(wa_client.requests, "post",
                      return_value=_resposta(200, {"messages": [{"id": "x"}]})) as post:
        wa_client.send_text("+5511988887777", "a" * 5000)
    assert len(post.call_args.kwargs["json"]["text"]["body"]) == 4096


def test_template_sem_variaveis_nao_manda_componentes():
    """Parâmetro a mais num template fixo faz a Meta recusar a mensagem."""
    with patch.object(wa_client.requests, "post",
                      return_value=_resposta(200, {"messages": [{"id": "x"}]})) as post:
        wa_client.send_template("+5511988887777")

    template = post.call_args.kwargs["json"]["template"]
    assert template["name"] == "primeiro_contato"
    assert "components" not in template


def test_template_com_variavel_monta_o_componente_do_corpo():
    with patch.object(wa_client.requests, "post",
                      return_value=_resposta(200, {"messages": [{"id": "x"}]})) as post:
        wa_client.send_template("+5511988887777", variaveis=["Acme"])

    componentes = post.call_args.kwargs["json"]["template"]["components"]
    assert componentes[0]["parameters"][0]["text"] == "Acme"


def test_erro_da_meta_vira_recusa_com_o_codigo():
    erro = _resposta(400, {"error": {"code": 131047, "message": "fora da janela"}})
    with patch.object(wa_client.requests, "post", return_value=erro):
        r = wa_client.send_text("+5511988887777", "oi")

    assert r.ok is False
    assert "131047" in r.error


def test_erro_da_meta_sem_json_ainda_devolve_recusa_legivel():
    erro = _resposta(502)
    erro.json.side_effect = ValueError("não é json")
    with patch.object(wa_client.requests, "post", return_value=erro):
        r = wa_client.send_text("+5511988887777", "oi")

    assert r.ok is False
    assert "502" in r.error


def test_rede_fora_nao_levanta_excecao():
    """
    O envio é chamado de dentro do webhook. Exceção aqui viraria 500, e 500 no
    webhook faz a Meta reentregar — o que produziria uma segunda mensagem.
    """
    with patch.object(wa_client.requests, "post",
                      side_effect=requests.RequestException("timeout")):
        r = wa_client.send_text("+5511988887777", "oi")

    assert r.ok is False
    assert r.error


def test_resposta_sem_id_de_mensagem_ainda_conta_como_enviada():
    """A mensagem saiu; só a idempotência fica sem âncora."""
    with patch.object(wa_client.requests, "post", return_value=_resposta(200, {})):
        r = wa_client.send_text("+5511988887777", "oi")
    assert (r.ok, r.wa_message_id) == (True, None)


def test_mensagem_vazia_nem_chega_a_sair():
    with patch.object(wa_client.requests, "post") as post:
        r = wa_client.send_text("+5511988887777", "   ")
    assert r.ok is False
    assert post.call_count == 0


def test_sem_credencial_nada_sai(monkeypatch):
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    with patch.object(wa_client.requests, "post") as post:
        r = wa_client.send_text("+5511988887777", "oi")
    assert r.ok is False
    assert post.call_count == 0


def test_template_ausente_e_recusado_antes_da_rede(monkeypatch):
    monkeypatch.delenv("WHATSAPP_TEMPLATE_NAME", raising=False)
    with patch.object(wa_client.requests, "post") as post:
        r = wa_client.send_template("+5511988887777")
    assert r.ok is False
    assert post.call_count == 0


# ── Qualidade do número ──────────────────────────────────────────────────────

@pytest.mark.parametrize("rating,tom", [
    ("GREEN", "ok"), ("YELLOW", "atencao"), ("RED", "critico"),
])
def test_cada_rating_vira_um_tom_e_um_recado(rating, tom):
    with patch.object(wa_client.requests, "get",
                      return_value=_resposta(200, {"quality_rating": rating,
                                                   "messaging_limit_tier": "TIER_1K"})):
        q = wa_client.phone_quality()

    assert q["rating"] == rating
    assert q["tom"] == tom
    assert q["recado"]
    assert q["limite"] == "TIER_1K"


def test_rating_desconhecido_nao_quebra():
    """A Meta pode inventar um valor novo; isso não pode virar exceção."""
    with patch.object(wa_client.requests, "get",
                      return_value=_resposta(200, {"quality_rating": "ROXO"})):
        q = wa_client.phone_quality()
    assert q["tom"] == "desconhecido"


def test_meta_fora_do_ar_devolve_none():
    with patch.object(wa_client.requests, "get",
                      side_effect=requests.RequestException("timeout")):
        assert wa_client.phone_quality() is None


def test_sem_credencial_nao_consulta_qualidade(monkeypatch):
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    with patch.object(wa_client.requests, "get") as get:
        assert wa_client.phone_quality() is None
    assert get.call_count == 0


# ── A chamada da IA ──────────────────────────────────────────────────────────

def test_chamada_da_ia_monta_o_pedido_e_extrai_o_texto(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-teste")
    resposta = _resposta(200, {"content": [{"type": "text", "text": '{"ok":true}'}]})
    with patch.object(brain.requests, "post", return_value=resposta) as post:
        texto = brain._chamar("classifique isto")

    assert texto == '{"ok":true}'
    assert post.call_args.kwargs["headers"]["x-api-key"] == "sk-teste"
    assert post.call_args.kwargs["json"]["messages"][0]["content"] == "classifique isto"


def test_blocos_que_nao_sao_texto_sao_ignorados(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-teste")
    resposta = _resposta(200, {"content": [
        {"type": "thinking", "thinking": "hmm"},
        {"type": "text", "text": "resposta"},
    ]})
    with patch.object(brain.requests, "post", return_value=resposta):
        assert brain._chamar("x") == "resposta"


def test_erro_http_da_ia_vira_none_e_nao_excecao(monkeypatch):
    """
    O turno inteiro depende disto não levantar: `None` vira AMBIGUO, que o
    orquestrador trata como "chame o humano".
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-teste")
    erro = _resposta(500)
    erro.raise_for_status.side_effect = requests.HTTPError("500")
    with patch.object(brain.requests, "post", return_value=erro):
        assert brain._chamar("x") is None


def test_sem_chave_a_ia_nem_e_chamada(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch.object(brain.requests, "post") as post:
        assert brain._chamar("x") is None
    assert post.call_count == 0


def _capturar(logger):
    """
    Prende um handler direto no logger e garante que ele receba o registro.

    Não usa `caplog` porque o app reconfigura o logging no import (`dictConfig`
    em `main.py`) e a captura do pytest depende de handler na raiz. E força o
    nível do logger porque a suíte inteira compartilha o estado global do
    `logging`: sem isso o teste passa sozinho e falha quando roda depois de
    outro módulo que mexeu em níveis — que foi exatamente o que aconteceu.
    """
    import logging

    registros = []

    class Coletor(logging.Handler):
        def emit(self, record):
            registros.append(record.getMessage())

    handler = Coletor()
    nivel_antes = logger.level
    desabilitado_antes = logger.disabled
    disable_antes = logging.root.manager.disable

    # `logger.disabled` é o detalhe que custou caro: o `fileConfig` do Alembic
    # (que `test_migracoes.py` dispara ao rodar as migrações) marca como
    # desabilitados TODOS os loggers que já existiam, e isso vale para o resto
    # do processo. O logger continua com nível normal e handlers no lugar, mas
    # `logger.warning` não cria registro nenhum — o teste passava sozinho e
    # falhava na suíte inteira, conforme a ordem dos arquivos.
    logger.disabled = False
    logging.disable(logging.NOTSET)
    logger.setLevel(logging.DEBUG)
    getattr(logger, "_cache", {}).clear()
    logger.addHandler(handler)

    def soltar():
        logger.removeHandler(handler)
        logger.setLevel(nivel_antes)
        logger.disabled = desabilitado_antes
        logging.disable(disable_antes)

    return registros, soltar


def test_a_mensagem_do_lead_nao_vaza_no_log_de_erro(monkeypatch):
    """
    O corpo do erro pode conter o que o lead escreveu. Só o tipo da exceção
    vai para o log — o resto é dado pessoal em arquivo de texto.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-teste")
    registros, soltar = _capturar(brain.logger)
    try:
        with patch.object(brain.requests, "post",
                          side_effect=requests.RequestException("corpo: meu CPF é 123")):
            brain._chamar("classifique: meu CPF é 123")
    finally:
        soltar()

    texto = " ".join(registros)
    assert "123" not in texto
    assert "RequestException" in texto


def test_o_telefone_do_lead_nao_vaza_no_log_de_erro_da_meta():
    """Mesma regra do outro lado: o corpo do erro da Meta traz o destinatário."""
    registros, soltar = _capturar(wa_client.logger)
    try:
        erro = _resposta(400, {"error": {"code": 131047,
                                         "message": "recipient +5511988887777 inválido"}})
        with patch.object(wa_client.requests, "post", return_value=erro):
            wa_client.send_text("+5511988887777", "oi")
    finally:
        soltar()

    assert "5511988887777" not in " ".join(registros)
