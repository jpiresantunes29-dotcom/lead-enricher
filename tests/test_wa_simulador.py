"""
O simulador da IA.

O que estes testes protegem é uma promessa, não um comportamento: **o
simulador não fala com a Meta e não escreve no banco**. É a única razão para
ele poder ser usado à vontade, e é o tipo de garantia que se perde numa
refatoração distraída — alguém reaproveita o `orchestrator` inteiro "para não
duplicar código" e o teste de quinta à noite vira mensagem no celular de um
lead de verdade.

O resto verifica que a decisão exibida é a mesma da produção: a tabela de
intenções é importada de lá, e um simulador que aprova o que a produção recusa
é pior do que não ter simulador.
"""
import pytest

from services.wa import brain, orchestrator, sandbox


@pytest.fixture(autouse=True)
def _sessoes_limpas():
    """Cada teste começa sem sessão de outro."""
    sandbox._sessoes.clear()
    yield
    sandbox._sessoes.clear()


def _leitura(intencao, confianca=0.95, rascunho="Claro, posso explicar."):
    return brain.Leitura(intencao, confianca, rascunho=rascunho)


# ── A promessa: nada sai, nada é gravado ─────────────────────────────────────

def test_nao_chama_a_meta_em_nenhum_caminho(monkeypatch):
    """
    Nem no caminho feliz. `client.send_text` levanta se for tocado — se algum
    dia o simulador passar a usar o orquestrador de verdade, este teste cai
    antes de a primeira mensagem sair.
    """
    def _estourar(*a, **k):
        raise AssertionError("O simulador tentou enviar pela Meta.")

    monkeypatch.setattr("services.wa.client.send_text", _estourar)
    monkeypatch.setattr("services.wa.client.send_template", _estourar)
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(brain.CONVERSANDO))

    turno = sandbox.enviar_do_lead("u1", "O que vocês fazem?")
    assert turno.acao == sandbox.RESPONDEU


def test_o_modulo_nao_importa_sessao_de_banco():
    """A sessão vive em memória: nada aqui deve depender de SQLAlchemy."""
    import inspect
    fonte = inspect.getsource(sandbox)
    assert "Session" not in fonte
    assert "db." not in fonte


# ── A decisão é a mesma da produção ──────────────────────────────────────────

@pytest.mark.parametrize("intencao,acao_esperada", [
    (brain.CONFIRMOU_PESSOA, sandbox.RESPONDEU),
    (brain.CONVERSANDO, sandbox.RESPONDEU),
    (brain.QUER_HUMANO, sandbox.CHAMOU_HUMANO),
    (brain.NEGOCIANDO, sandbox.CHAMOU_HUMANO),
    (brain.PESSOA_ERRADA, sandbox.CHAMOU_HUMANO),
    (brain.FORA_DA_BASE, sandbox.CHAMOU_HUMANO),
    (brain.AMBIGUO, sandbox.CHAMOU_HUMANO),
    (brain.JA_E_CLIENTE, sandbox.ENCERROU),
    (brain.PEDIU_PARAR, sandbox.ENCERROU),
])
def test_cada_intencao_leva_a_acao_da_producao(monkeypatch, intencao, acao_esperada):
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(intencao))

    turno = sandbox.enviar_do_lead("u1", "mensagem qualquer")
    assert turno.acao == acao_esperada
    assert turno.intencao == intencao


def test_todas_as_intencoes_do_brain_tem_plano():
    """
    Uma intenção nova em `brain` sem entrada na tabela do orquestrador faria o
    simulador dizer "sem ação definida" — e a produção, chamar o humano para
    sempre. Melhor descobrir aqui.
    """
    faltando = [i for i in brain.INTENCOES if orchestrator.plano_para(i) is None]
    assert faltando == [brain.AMBIGUO] or not faltando


# ── Na dúvida, não responde ──────────────────────────────────────────────────

def test_confianca_abaixo_do_corte_chama_o_humano(monkeypatch):
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(
        brain.CONVERSANDO, confianca=brain.CONFIANCA_MINIMA - 0.01))

    turno = sandbox.enviar_do_lead("u1", "ok")
    assert turno.acao == sandbox.CHAMOU_HUMANO
    assert turno.confiavel is False
    # A tela precisa poder explicar; motivo vazio viraria um card mudo.
    assert turno.motivo


def test_rascunho_vazio_chama_o_humano(monkeypatch):
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(
        brain.CONVERSANDO, rascunho=None))

    turno = sandbox.enviar_do_lead("u1", "e aí")
    assert turno.acao == sandbox.CHAMOU_HUMANO
    assert turno.texto is None


def test_ia_desligada_nao_finge_que_respondeu(monkeypatch):
    monkeypatch.setattr(brain, "is_configured", lambda: False)
    turno = sandbox.enviar_do_lead("u1", "oi")
    assert turno.acao == sandbox.CHAMOU_HUMANO
    assert "ANTHROPIC_API_KEY" in (turno.erro or "")


# ── A conversa de teste ──────────────────────────────────────────────────────

def test_a_resposta_da_ia_entra_no_historico(monkeypatch):
    """O próximo turno precisa enxergar o que a IA acabou de dizer."""
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(
        brain.CONVERSANDO, rascunho="Posso te explicar em 5 minutos."))

    sandbox.enviar_do_lead("u1", "o que é isso?")
    s = sandbox.sessao("u1")
    assert [m.direction for m in s.mensagens] == ["in", "out"]
    assert s.mensagens[1].sent_by == "ai"
    assert s.mensagens[1].intent_detected == brain.CONVERSANDO


def test_quando_nao_responde_so_a_mensagem_do_lead_fica(monkeypatch):
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(brain.NEGOCIANDO))

    sandbox.enviar_do_lead("u1", "quanto custa?")
    s = sandbox.sessao("u1")
    assert [m.direction for m in s.mensagens] == ["in"]


def test_sessoes_de_usuarios_diferentes_nao_se_misturam(monkeypatch):
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(brain.NEGOCIANDO))

    sandbox.enviar_do_lead("u1", "mensagem do primeiro")
    sandbox.enviar_do_lead("u2", "mensagem do segundo")
    assert sandbox.sessao("u1").mensagens[0].body == "mensagem do primeiro"
    assert sandbox.sessao("u2").mensagens[0].body == "mensagem do segundo"


def test_reiniciar_apaga_a_conversa_e_troca_a_empresa(monkeypatch):
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(brain.NEGOCIANDO))

    sandbox.enviar_do_lead("u1", "oi")
    s = sandbox.reiniciar("u1", "Padaria do João")
    assert s.mensagens == []
    assert s.turnos == []
    assert s.empresa == "Padaria do João"


def test_empresa_em_branco_cai_no_padrao():
    assert sandbox.reiniciar("u1", "   ").empresa == sandbox.EMPRESA_PADRAO


def test_mensagem_vazia_nao_gasta_turno():
    turno = sandbox.enviar_do_lead("u1", "   ")
    assert turno.acao == sandbox.NAO_ENVIOU
    assert sandbox.sessao("u1").mensagens == []


def test_historico_nao_cresce_sem_fim(monkeypatch):
    """
    Sessão de vida longa não pode virar vazamento de memória num processo que
    atende muita gente.
    """
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(brain.NEGOCIANDO))

    for i in range(sandbox.MAX_MENSAGENS + 20):
        sandbox.enviar_do_lead("u1", f"mensagem {i}")
    assert len(sandbox.sessao("u1").mensagens) <= sandbox.MAX_MENSAGENS


def test_teto_de_sessoes_descarta_a_mais_antiga():
    for i in range(sandbox.MAX_SESSOES + 5):
        sandbox.sessao(f"user-{i}")
    assert len(sandbox._sessoes) <= sandbox.MAX_SESSOES


# ── A trava de horário ───────────────────────────────────────────────────────

def test_respeitar_o_horario_impede_o_turno(monkeypatch):
    """Com a trava ligada, madrugada devolve 'não enviou' sem chamar a IA."""
    monkeypatch.setattr("services.wa.gate.service_window", lambda *a, **k: (False, False))

    def _nao_deveria(*a, **k):
        raise AssertionError("A IA foi chamada com a trava de horário ligada.")

    monkeypatch.setattr(brain, "ler", _nao_deveria)
    turno = sandbox.enviar_do_lead("u1", "oi", ignorar_horario=False)
    assert turno.acao == sandbox.NAO_ENVIOU
    assert turno.fora_do_horario is True


def test_ignorar_horario_responde_mas_avisa(monkeypatch):
    """
    O padrão da tela: testar às 23h funciona, e o card diz que em produção
    aquela resposta teria esperado o horário de envio.
    """
    monkeypatch.setattr("services.wa.gate.service_window", lambda *a, **k: (False, False))
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(brain.CONVERSANDO))

    turno = sandbox.enviar_do_lead("u1", "oi", ignorar_horario=True)
    assert turno.acao == sandbox.RESPONDEU
    assert "horário de envio" in (turno.motivo or "")


# ── As rotas ─────────────────────────────────────────────────────────────────
# Da requisição até o raio-x que a tela desenha. O que se guarda aqui é o
# formato: a tela lê `acao`, `intencao` e `confianca` para montar o card, e um
# campo que muda de nome sem aviso deixa o painel mudo em produção.

from tests.test_api import client, clean_db  # noqa: E402,F401


def test_status_diz_o_que_falta_quando_a_ia_esta_desligada(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dados = client.get("/api/wa/sandbox/status").json()
    assert dados["ia_configurada"] is False
    assert dados["modelo"] is None
    assert dados["confianca_minima"] == brain.CONFIANCA_MINIMA
    # A tela usa a lista para explicar o que a IA sabe classificar.
    assert brain.NEGOCIANDO in dados["intencoes"]


def test_turno_completo_pela_rota(client, monkeypatch):
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(
        brain.CONVERSANDO, rascunho="Posso te explicar em 5 minutos."))

    turno = client.post("/api/wa/sandbox/message",
                        json={"texto": "o que vocês fazem?"}).json()
    assert turno["acao"] == "respondeu"
    assert turno["intencao"] == brain.CONVERSANDO
    assert turno["confiavel"] is True

    sessao = client.get("/api/wa/sandbox").json()
    assert [m["direction"] for m in sessao["mensagens"]] == ["in", "out"]
    assert sessao["mensagens"][1]["body"] == "Posso te explicar em 5 minutos."
    assert len(sessao["turnos"]) == 1


def test_mensagem_vazia_e_recusada_pela_rota(client):
    assert client.post("/api/wa/sandbox/message", json={"texto": "  "}).status_code == 422


def test_reset_pela_rota_limpa_e_nomeia(client, monkeypatch):
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(brain.NEGOCIANDO))

    client.post("/api/wa/sandbox/message", json={"texto": "quanto custa?"})
    sessao = client.post("/api/wa/sandbox/reset",
                         json={"empresa": "Padaria do João"}).json()
    assert sessao["mensagens"] == []
    assert sessao["empresa"] == "Padaria do João"


def test_o_simulador_nao_cria_conversa_no_banco(client, monkeypatch):
    """
    A garantia que permite testar à vontade: a métrica de WhatsApp continua
    contando só conversas que aconteceram de verdade.
    """
    monkeypatch.setattr(brain, "is_configured", lambda: True)
    monkeypatch.setattr(brain, "ler", lambda *a, **k: _leitura(brain.CONVERSANDO))

    client.post("/api/wa/sandbox/message", json={"texto": "oi"})
    assert client.get("/api/wa/conversations").json() == []
    assert client.get("/api/wa/metrics").json()["conversas_iniciadas"] == 0
