"""
Testes da régua de priorização (services/lead_scorer.py).

Duas metades: a função pura, testada sem banco nem aplicação, e o caminho
HTTP — coleta pontua, busca de decisores repontua, recálculo sob demanda.

O que estes testes protegem, além dos números: a nota é gravada, não calculada
na leitura. Toda vez que alguém acrescentar um caminho que muda um campo
pontuável sem repontuar, é aqui que deveria quebrar.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401

from services.lead_scorer import (
    PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM, SCORING_VERSION,
    apply_score, score_lead,
)


def _lead(**kwargs):
    """Ficha mínima: tudo vazio, e só o que o teste liga é que pontua."""
    base = dict(
        phone=None, corporate_email=None, mx_provider=None, dns_report=None,
        hosting_provider=None, employee_count=None, employee_count_linkedin=None,
        sector=None, description=None, location=None, linkedin_url=None,
        linkedin_confidence=None, decision_makers=[],
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _dm(**kwargs):
    base = dict(probable_emails=None, phone=None)
    base.update(kwargs)
    return SimpleNamespace(**base)


# ── casos extremos ─────────────────────────────────────────────────────────


def test_ficha_vazia_pontua_zero_e_nao_estoura():
    """
    O caso que mais aparece em produção: domínio que não resolveu nada. Uma
    régua que dividisse por um total zerado ou lesse chave ausente derrubaria
    a coleta inteira num 500 — e a coleta é o caminho crítico do produto.
    """
    r = score_lead(_lead(), [])
    assert r["score"] == 0
    assert r["priority"] == PRIORITY_LOW
    assert r["score_version"] == SCORING_VERSION
    assert all(not s["hit"] for g in r["score_breakdown"]["groups"] for s in g["signals"])


def test_ficha_completa_nao_passa_de_cem():
    lead = _lead(
        phone="(11) 3333-4444",
        corporate_email="contato@empresa.com.br",
        mx_provider="Google Workspace",
        dns_report={"email": {
            "spf": {"raw": "v=spf1 -all"},
            "dmarc": {"policy": "reject", "enforced": True},
            "dkim": [{"selector": "google"}],
        }},
        hosting_provider="AWS",
        employee_count={"band": "51-200", "exact": 120},
        employee_count_linkedin=120,
        sector="Fintech",
        description="Banco digital brasileiro com operação em toda a América Latina.",
        location="São Paulo, Brasil",
        linkedin_url="https://linkedin.com/company/x",
        linkedin_confidence="verified",
    )
    dms = [
        _dm(probable_emails=[{"email": "a@x.com", "status": "valid"}], phone="+5511999998888"),
        _dm(probable_emails=[{"email": "b@x.com", "status": "unknown"}]),
        _dm(probable_emails=[{"email": "c@x.com", "status": "valid"}]),
    ]
    r = score_lead(lead, dms)
    assert r["score"] == 100
    assert r["priority"] == PRIORITY_HIGH


def test_dns_report_nulo_nao_quebra_os_sinais_de_email():
    """Coleta parcial deixa `dns_report` nulo — não pode virar AttributeError."""
    r = score_lead(_lead(dns_report=None), [])
    sinais = {s["key"]: s for g in r["score_breakdown"]["groups"] for s in g["signals"]}
    assert sinais["spf"]["points"] == 0
    assert sinais["dmarc"]["points"] == 0


def test_dns_report_com_formato_inesperado_nao_quebra():
    """Ficha antiga pode ter `dns_report` numa forma que a régua não conhece."""
    for lixo in ("string", [], {"email": None}, {"email": "nao-e-dict"}):
        r = score_lead(_lead(dns_report=lixo), [])
        assert 0 <= r["score"] <= 100


def test_decisor_sem_emails_nao_quebra():
    r = score_lead(_lead(), [_dm(probable_emails=None), _dm(probable_emails="lixo")])
    assert r["score"] > 0  # o próprio decisor pontua


# ── o peso segue o vendedor ────────────────────────────────────────────────


def test_alcance_pesa_mais_que_maturidade_tecnica():
    """
    A decisão de projeto central da régua: um decisor com telefone e e-mail
    verificado vale mais que um domínio tecnicamente impecável sem ninguém
    para ligar. Se esta inversão passar, a lista volta a ordenar por DNS
    bonito — que é exatamente o que a nota existe para não fazer.
    """
    so_tecnico = score_lead(_lead(
        mx_provider="Google", hosting_provider="AWS",
        dns_report={"email": {
            "spf": {"raw": "v=spf1"},
            "dmarc": {"policy": "reject", "enforced": True},
            "dkim": [{"selector": "g"}],
        }},
    ), [])
    so_alcance = score_lead(_lead(phone="(11) 3333-4444"), [
        _dm(probable_emails=[{"email": "a@x.com", "status": "valid"}], phone="+5511999998888"),
    ])
    assert so_alcance["score"] > so_tecnico["score"]


def test_email_verificado_vale_mais_que_catch_all_que_vale_mais_que_deduzido():
    def nota(status):
        return score_lead(_lead(), [
            _dm(probable_emails=[{"email": "a@x.com", "status": status}])
        ])["score"]

    assert nota("valid") > nota("catch_all") > nota("unknown") > nota("invalid")


def test_melhor_email_entre_decisores_e_o_que_conta():
    """Basta UM caminho que funcione — nove inválidos e um verificado vale o verificado."""
    misto = score_lead(_lead(), [
        _dm(probable_emails=[{"email": "a@x.com", "status": "invalid"}]),
        _dm(probable_emails=[{"email": "b@x.com", "status": "valid"}]),
    ])
    sinais = {s["key"]: s for g in misto["score_breakdown"]["groups"] for s in g["signals"]}
    assert sinais["email_decisor"]["points"] == 10


def test_primeiro_decisor_vale_mais_que_o_terceiro():
    def pontos(n):
        r = score_lead(_lead(), [_dm() for _ in range(n)])
        return {s["key"]: s for g in r["score_breakdown"]["groups"]
                for s in g["signals"]}["decisores"]["points"]

    assert pontos(1) - pontos(0) > pontos(3) - pontos(2)


def test_dmarc_ativo_vale_mais_que_dmarc_em_monitoramento():
    def nota(dmarc):
        return score_lead(_lead(dns_report={"email": {"dmarc": dmarc}}), [])["score"]

    assert nota({"policy": "reject", "enforced": True}) > nota({"policy": "none", "enforced": False})


def test_curva_de_porte_penaliza_extremos():
    """Nem microempresa nem multinacional é alvo típico de prospecção fria."""
    def nota(band):
        return score_lead(_lead(employee_count={"band": band}), [])["score"]

    assert nota("51-200") > nota("1-10")
    assert nota("51-200") > nota("10000+")


# ── faixas de prioridade ───────────────────────────────────────────────────


def test_faixas_de_prioridade_seguem_os_limiares():
    from services.lead_scorer import _priority_for

    assert _priority_for(100) == PRIORITY_HIGH
    assert _priority_for(70) == PRIORITY_HIGH
    assert _priority_for(69) == PRIORITY_MEDIUM
    assert _priority_for(40) == PRIORITY_MEDIUM
    assert _priority_for(39) == PRIORITY_LOW
    assert _priority_for(0) == PRIORITY_LOW


# ── detalhamento auditável ─────────────────────────────────────────────────


def test_breakdown_explica_a_nota_ponto_a_ponto():
    """
    O popover "por que 47?" só é honesto se a soma do detalhamento for a nota.
    Um detalhamento que não fecha é pior que nenhum: dá aparência de auditoria
    a um número inventado.
    """
    r = score_lead(_lead(sector="Fintech", phone="(11) 3333-4444"), [_dm()])
    b = r["score_breakdown"]

    soma = sum(s["points"] for g in b["groups"] for s in g["signals"])
    assert soma == b["points"]
    assert b["max_points"] == sum(s["max"] for g in b["groups"] for s in g["signals"])
    assert b["score"] == round(100 * b["points"] / b["max_points"])

    for grupo in b["groups"]:
        assert grupo["points"] == sum(s["points"] for s in grupo["signals"])


def test_todo_sinal_traz_rotulo_e_explicacao_mesmo_quando_nao_pontua():
    """A tela mostra o que faltou, não só o que somou — é isso que orienta a próxima ação."""
    r = score_lead(_lead(), [])
    for grupo in r["score_breakdown"]["groups"]:
        for sinal in grupo["signals"]:
            assert sinal["label"] and sinal["detail"]
            assert sinal["max"] > 0


def test_apply_score_grava_as_quatro_colunas():
    lead = _lead()
    apply_score(lead, [])
    assert lead.score == 0
    assert lead.priority == PRIORITY_LOW
    assert lead.score_version == SCORING_VERSION
    assert lead.score_breakdown["version"] == SCORING_VERSION


def test_score_lead_nao_muda_a_ficha():
    """A função pura não pode ter efeito colateral — `apply_score` é quem grava."""
    lead = _lead(sector="Fintech")
    score_lead(lead, [])
    assert not hasattr(lead, "score")


def test_decisores_lidos_da_relacao_quando_nao_passados():
    lead = _lead(decision_makers=[_dm(phone="+5511999998888")])
    com_relacao = score_lead(lead)["score"]
    assert com_relacao > score_lead(lead, [])["score"]


# ── caminho HTTP ───────────────────────────────────────────────────────────


def test_coleta_ja_nasce_pontuada(client):
    """
    Ficha sem nota fica invisível em qualquer lista ordenada por prioridade —
    por isso a coleta pontua mesmo antes de existir decisor.
    """
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})
    data = resp.json()["data"]
    assert data["score"] is not None
    assert data["priority"] in (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)
    assert data["score_version"] == SCORING_VERSION
    assert data["score_breakdown"]["groups"]


def test_busca_de_decisores_repontua_a_ficha(client):
    """
    O eixo de maior peso é alcance; achar decisores é o evento que mais muda a
    nota. Se este teste quebrar, a ficha continua com a nota da coleta e nunca
    sobe na lista, mesmo virando acionável.
    """
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        lead = client.post("/api/enrich", json={"domain": "nubank.com.br"}).json()["data"]
    antes = lead["score"]

    achados = [{
        "name": "Fulana de Tal", "title_searched": "CTO", "title_found": "CTO",
        "snippet": "", "linkedin_url": "https://linkedin.com/in/fulana",
        "probable_emails": [{"email": "fulana@nubank.com.br", "status": "valid"}],
        "match_confidence": "high", "phone": "+5511999998888",
    }]
    with patch("routers.enrichment.find_decision_makers", return_value=achados):
        resp = client.post("/api/decisores", json={"lead_id": lead["id"], "roles": ["CTO"]})
    assert resp.status_code == 200

    depois = client.get(f"/api/leads/{lead['id']}").json()
    assert depois["score"] > antes


def test_rescore_reflete_correcao_manual_de_telefone(client):
    """
    O caminho que nenhum ponto automático cobre: o usuário corrige o telefone à
    mão. Sem o endpoint, a nota ficaria travada no valor antigo para sempre.
    """
    sem_telefone = {**MOCK_ENRICH_RESULT, "phone": None}
    with patch("services.enrichment_service.enrich_company", return_value=sem_telefone):
        lead = client.post("/api/enrich", json={"domain": "nubank.com.br"}).json()["data"]
    antes = lead["score"]

    client.patch(f"/api/leads/{lead['id']}", json={"phone": "(11) 3333-4444"})
    depois = client.post(f"/api/leads/{lead['id']}/rescore").json()

    assert depois["score"] > antes
    assert depois["score_version"] == SCORING_VERSION


def test_rescore_e_idempotente(client):
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        lead = client.post("/api/enrich", json={"domain": "nubank.com.br"}).json()["data"]
    um = client.post(f"/api/leads/{lead['id']}/rescore").json()
    dois = client.post(f"/api/leads/{lead['id']}/rescore").json()
    assert um["score"] == dois["score"] == lead["score"]


def test_rescore_de_lead_de_outro_usuario_da_404(client):
    assert client.post("/api/leads/999999/rescore").status_code == 404


def test_listagem_ordena_por_score_e_filtra_por_prioridade(client):
    magro = {**MOCK_ENRICH_RESULT, "domain": "magro.com.br", "raw_input_domain": "magro.com.br",
             "sector": None, "description": None, "location": None, "linkedin_url": None,
             "linkedin_confidence": None, "mx_provider": None, "hosting_provider": None}
    with patch("services.enrichment_service.enrich_company", return_value=magro):
        client.post("/api/enrich", json={"domain": "magro.com.br"})
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        gordo = client.post("/api/enrich", json={"domain": "nubank.com.br"}).json()["data"]

    por_score = client.get("/api/leads?sort=score").json()
    assert por_score[0]["id"] == gordo["id"]
    assert [l["score"] for l in por_score] == sorted(
        [l["score"] for l in por_score], reverse=True
    )

    filtrada = client.get(f"/api/leads?priority={gordo['priority']}").json()
    assert all(l["priority"] == gordo["priority"] for l in filtrada)

    assert client.get("/api/leads?priority=urgentissimo").status_code == 422


def test_listagem_nao_carrega_o_detalhamento(client):
    """Cem detalhamentos numa tela seriam centenas de KB que nenhuma coluna usa."""
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        client.post("/api/enrich", json={"domain": "nubank.com.br"})
    item = client.get("/api/leads").json()[0]
    assert "score_breakdown" not in item
    assert item["score"] is not None  # a nota em si continua na lista


def test_dashboard_conta_leads_por_prioridade(client):
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        lead = client.post("/api/enrich", json={"domain": "nubank.com.br"}).json()["data"]

    m = client.get("/api/dashboard/metrics").json()
    assert set(m["leads_por_prioridade"]) == {PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW}
    assert m["leads_por_prioridade"][lead["priority"]] >= 1
    assert m["score_medio"] is not None
