"""
Fluxo comercial automatizado: tier (Captura e Priorização), agendamento em
lote, cadência de contato/qualificação, aquecimento e contatos manuais.

Cada ramo dos fluxogramas tem pelo menos um teste — é o contrato com o time
comercial, e regra de priorização que muda sem teste muda sem ninguém ver.
"""
from datetime import date, datetime, timedelta, UTC
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT, FAKE_USER  # noqa: F401

from models.database import Activity, Lead, Profile
from services import cadence as cad
from services.tier_classifier import classify_tier, email_service, team_of


def _lead(**kw):
    base = dict(mx_provider=None, mx_records=None, dns_report=None, employee_count=None,
                employee_count_linkedin=None, relationship="LEAD", domain="acme.com.br")
    base.update(kw)
    return SimpleNamespace(**base)


def _ec(lo, hi=None, exact=None, band=None, source="linkedin_direct"):
    return {"raw": band, "min": lo, "max": hi, "band": band, "exact": exact, "source": source}


# ── Tier: cada ramo do fluxograma ────────────────────────────────────────────

def test_cliente_da_base_e_tier_4():
    r = classify_tier(_lead(employee_count=_ec(201, 500)), is_customer=True,
                      customer_kind="revenda")
    assert r["tier"] == 4
    assert r["path"][0]["resposta"] == "sim"
    assert "revenda" in r["reason"]


def test_mx_skymail_e_cliente_mesmo_sem_rotulo():
    """Skymail só sai com confiança baixa — o rastro do MX é o que acusa."""
    lead = _lead(employee_count=_ec(201, 500),
                 mx_records=[{"host": "mx-ha.skymail.net.br", "ptr": None, "asn_org": None}])
    assert classify_tier(lead)["tier"] == 4


def test_outro_executivo_e_tier_4():
    r = classify_tier(_lead(employee_count=_ec(201, 500)), other_owner="ana@skymail.com.br")
    assert r["tier"] == 4
    assert "ana@skymail.com.br" in r["reason"]


def test_menos_de_50_e_tier_4():
    assert classify_tier(_lead(employee_count_linkedin=30))["tier"] == 4


def test_faixa_11_50_conta_como_pequena():
    assert classify_tier(_lead(employee_count=_ec(11, 50, band="11-50")))["tier"] == 4


def test_porte_da_receita_em_texto():
    ec = {"raw": "1-19 (estimado pelo porte)", "band": "1-19 (estimado pelo porte)",
          "min": None, "max": None, "exact": None, "source": "cnpj_porte"}
    assert classify_tier(_lead(employee_count=ec))["tier"] == 4


def test_mais_de_1000_e_tier_3():
    r = classify_tier(_lead(employee_count=_ec(1001, 5000), mx_provider="Microsoft 365"))
    assert r["tier"] == 3
    assert "Microsoft 365" in r["reason"]


def test_porte_medio_microsoft_e_tier_2():
    assert classify_tier(_lead(employee_count=_ec(51, 200),
                               mx_provider="Microsoft 365"))["tier"] == 2


def test_porte_medio_google_e_tier_2():
    assert classify_tier(_lead(employee_count_linkedin=300,
                               mx_provider="Google Workspace"))["tier"] == 2


def test_gateway_com_spf_microsoft_e_tier_2():
    """Proofpoint na frente não pode esconder um Microsoft 365 atrás."""
    lead = _lead(employee_count=_ec(201, 500), mx_provider="Proofpoint",
                 dns_report={"spf": "v=spf1 include:spf.protection.outlook.com -all"})
    r = classify_tier(lead)
    assert r["tier"] == 2
    assert r["email_service"]["evidencia"] == "spf"


def test_porte_medio_outro_provedor_e_tier_1():
    r = classify_tier(_lead(employee_count=_ec(51, 200), mx_provider="Locaweb"))
    assert r["tier"] == 1
    assert r["email_service"]["rotulo"] == "Locaweb"
    assert [p["resposta"] for p in r["path"]] == ["não", "não", "não", "não", "não"]


def test_limites_sao_estritos():
    assert classify_tier(_lead(employee_count_linkedin=50, mx_provider="Locaweb"))["tier"] == 1
    assert classify_tier(_lead(employee_count_linkedin=1000, mx_provider="Locaweb"))["tier"] == 1
    assert classify_tier(_lead(employee_count_linkedin=1001, mx_provider="Locaweb"))["tier"] == 3


def test_faixa_que_cruza_o_limite_vira_suposicao_pelo_mx():
    ec = {"raw": "10-99", "band": "10-99", "min": 10, "max": 99, "exact": None,
          "source": "cnpj_porte"}
    r = classify_tier(_lead(employee_count=ec, mx_provider="Locaweb"))
    assert (r["tier"], r["needs_review"]) == (1, True)


def test_porte_desconhecido_supoe_medio_pelo_mx():
    """Otimista: entra no lote já, mas marcado para revisão."""
    r = classify_tier(_lead(mx_provider="Locaweb"))
    assert (r["tier"], r["needs_review"]) == (1, True)
    r = classify_tier(_lead(mx_provider="Microsoft 365"))
    assert (r["tier"], r["needs_review"]) == (2, True)
    r = classify_tier(_lead())
    assert (r["tier"], r["needs_review"]) == (1, True)


def test_cliente_vence_porte_desconhecido():
    """A ordem do fluxograma importa: cliente é perguntado antes do porte."""
    assert classify_tier(_lead(), is_customer=True)["tier"] == 4


def test_email_service_sem_mx():
    assert email_service(_lead())["familia"] is None


def test_team_of():
    assert team_of("Ana@Skymail.com.br", "u1") == "skymail.com.br"
    assert team_of(None, "u1") == "user:u1"


# ── Tier na API ──────────────────────────────────────────────────────────────

def _enrich(client, domain="acme.com.br", **extra):
    data = {**MOCK_ENRICH_RESULT, "raw_input_domain": domain, "domain": domain,
            "company_name": domain.split(".")[0].title(), **extra}
    with patch("services.enrichment_service.enrich_company", return_value=data):
        resp = client.post("/api/enrich", json={"domain": domain})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_enriquecer_ja_classifica(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    assert lead["tier"] == 1
    assert lead["tier_details"]["path"]
    assert "alvo prioritário" in lead["tier_reason"]


def test_colega_de_time_tratando_vira_tier_4(client):
    db = _Session()
    db.add(Profile(id="colega", email="colega@example.com"))
    db.add(Lead(user_id="colega", raw_input_domain="acme.com.br", domain="acme.com.br",
                status="enriched", stage="contatado"))
    db.commit()
    db.close()
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    assert lead["tier"] == 4
    assert "colega@example.com" in lead["tier_reason"]


def test_usuario_de_outra_empresa_nao_conta(client):
    db = _Session()
    db.add(Profile(id="estranho", email="x@concorrente.com"))
    db.add(Lead(user_id="estranho", raw_input_domain="acme.com.br", domain="acme.com.br",
                status="enriched", stage="contatado"))
    db.commit()
    db.close()
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    assert lead["tier"] == 1


def test_base_de_clientes_reclassifica(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    resp = client.post("/api/clientes", json={"texto": "acme.com.br\noutra.com", "kind": "skymail"})
    assert resp.json()["adicionados"] == 2
    assert resp.json()["reclassificados"] == 1
    assert client.get(f"/api/leads/{lead['id']}").json()["tier"] == 4
    assert client.get("/api/clientes").json()["total"] == 2

    client.delete("/api/clientes/acme.com.br")
    assert client.get(f"/api/leads/{lead['id']}").json()["tier"] == 1


def test_tier_manual_e_porte_informado(client):
    lead = _enrich(client, mx_provider="Microsoft 365")
    assert lead["tier"] == 2                              # suposto pelo MX
    assert lead["tier_details"]["needs_review"] is True
    assert client.get("/api/leads").json()[0]["tier_needs_review"] is True
    assert client.get("/api/tiers/resumo").json()["revisar"] == 1

    r = client.patch(f"/api/leads/{lead['id']}", json={"employees": 30}).json()
    assert r["tier"] == 4                                 # porte real corrige
    assert r["tier_details"]["needs_review"] is False

    r = client.patch(f"/api/leads/{lead['id']}/tier", json={"tier": 3}).json()
    assert (r["tier"], r["tier_auto"], r["tier_manual"]) == (3, 4, 3)

    r = client.patch(f"/api/leads/{lead['id']}/tier", json={"tier": None}).json()
    assert r["tier"] == 4


def test_marcar_cliente_na_ficha(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    r = client.patch(f"/api/leads/{lead['id']}", json={"relationship": "CUSTOMER"}).json()
    assert r["tier"] == 4


def test_lista_filtra_e_ordena_por_tier(client):
    _enrich(client, "a.com.br", mx_provider="Microsoft 365", employee_count=_ec(51, 200))
    _enrich(client, "b.com.br", mx_provider="Locaweb", employee_count=_ec(51, 200))
    _enrich(client, "c.com.br", mx_provider="Locaweb", employee_count=_ec(1, 10))
    lista = client.get("/api/leads?sort=tier").json()
    assert [l["tier"] for l in lista] == [1, 2, 4]
    assert "tier_details" not in lista[0]
    assert [l["domain"] for l in client.get("/api/leads?tier=2").json()] == ["a.com.br"]
    resumo = client.get("/api/tiers/resumo").json()
    assert resumo["por_tier"]["1"] == 1 and resumo["nao_executados"]["2"] == 1


def test_reclassificar_base_antiga(client):
    db = _Session()
    db.add(Lead(user_id=FAKE_USER["sub"], raw_input_domain="velho.com.br",
                domain="velho.com.br", status="enriched", mx_provider="Locaweb",
                employee_count=_ec(51, 200)))
    db.commit()
    db.close()
    r = client.post("/api/tiers/reclassificar").json()
    assert r["reclassificados"] == 1
    assert client.get("/api/leads").json()[0]["tier"] == 1


# ── Agendamento em lote ──────────────────────────────────────────────────────

def test_montar_lote_respeita_mix_e_completa():
    c = {1: list(range(30)), 2: list(range(100, 110)), 3: list(range(200, 210))}
    lote = cad.montar_lote(c, 50, {1: 40, 2: 5, 3: 5})
    assert len(lote) == 50
    assert sum(1 for x in lote if x < 100) == 30          # todos os T1 que havia
    assert sum(1 for x in lote if 100 <= x < 200) == 10   # T2 cobre a falta primeiro
    assert sum(1 for x in lote if x >= 200) == 10


def _proximo_dia_util():
    d = datetime.now(UTC).astimezone(cad.TZ).date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def test_agendar_lote(client):
    for i in range(3):
        _enrich(client, f"t1-{i}.com.br", mx_provider="Locaweb", employee_count=_ec(51, 200))
    _enrich(client, "t2.com.br", mx_provider="Microsoft 365", employee_count=_ec(51, 200))
    _enrich(client, "t4.com.br", mx_provider="Locaweb", employee_count=_ec(1, 10))
    dia = _proximo_dia_util()

    prev = client.post("/api/agenda/lote", json={"dia": dia.isoformat(), "total": 3,
                                                 "mix": {"1": 2, "2": 1, "3": 0}}).json()
    assert prev["por_tier"] == {"1": 2, "2": 1, "3": 0}
    assert prev["criadas"] == 0

    feito = client.post("/api/agenda/lote", json={"dia": dia.isoformat(), "total": 50,
                                                  "preview": False}).json()
    assert feito["criadas"] == 4                       # Tier 4 nunca entra
    assert "só 4 de 50" in feito["message"]

    agenda = client.get(f"/api/agenda?dia={dia.isoformat()}").json()
    assert agenda["resumo"]["hoje"] == 4
    assert agenda["hoje"][0]["lead"]["tier"] == 1      # Tier 1 no topo
    assert agenda["hoje"][0]["acao"]["titulo"] == "1º contato"

    # Já agendados não voltam no próximo lote
    de_novo = client.post("/api/agenda/lote", json={"dia": dia.isoformat()}).json()
    assert de_novo["success"] is False


def test_lote_recusa_fim_de_semana_e_passado(client):
    ontem = datetime.now(UTC).date() - timedelta(days=2)
    assert client.post("/api/agenda/lote", json={"dia": ontem.isoformat()}).status_code == 422
    sabado = date(2030, 1, 5)
    assert client.post("/api/agenda/lote", json={"dia": sabado.isoformat()}).status_code == 422


# ── Cadência: Contatos e Qualificação ────────────────────────────────────────

def _cad(client, lead_id, resultado, **extra):
    resp = client.post(f"/api/leads/{lead_id}/cadencia", json={"resultado": resultado, **extra})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_tres_tentativas_sem_contato_vao_para_aquecimento(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    r = _cad(client, lead["id"], "iniciar")
    assert r["cadencia"]["cadence"] == "tentativas"

    r = _cad(client, lead["id"], "sem_contato")
    assert r["criadas"][0]["step"] == "tentativa_2"
    due = datetime.fromisoformat(r["criadas"][0]["due_at"])
    assert 6 <= (due.date() - datetime.now(UTC).date()).days <= 9   # D+7, dia útil

    r = _cad(client, lead["id"], "sem_contato")
    assert r["criadas"][0]["step"] == "tentativa_3"

    r = _cad(client, lead["id"], "sem_contato")
    assert r["lead"]["cadence"] == "aquecimento"
    assert [a["step"] for a in r["criadas"]] == [
        "aquec_email_1", "aquec_email_2", "aquec_email_3", "aquec_whatsapp"]
    # Só uma tarefa aberta de tentativa nunca sobra: as anteriores foram fechadas
    assert all(a["step"].startswith("aquec") for a in r["cadencia"]["proximas"])


def test_telefone_errado_nao_avanca_tentativa(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    _cad(client, lead["id"], "iniciar")
    r = _cad(client, lead["id"], "telefone_errado")
    assert {a["step"] for a in r["criadas"]} == {"contato_alternativo", "primeiro_contato"}
    assert r["lead"]["cadence_attempt"] == 1


def test_caminho_feliz_ate_proposta(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    _cad(client, lead["id"], "iniciar")
    quando = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    r = _cad(client, lead["id"], "reuniao_marcada", reuniao_em=quando)
    assert r["lead"]["stage"] == "reuniao_agendada"
    assert r["criadas"][0]["type"] == "meeting"
    assert {a["resultado"] for a in r["cadencia"]["acoes"]} >= {
        "qualificado_com_demanda", "qualificado_sem_demanda", "nao_qualificado"}

    r = _cad(client, lead["id"], "qualificado_com_demanda")
    assert r["lead"]["cadence"] == "proposta"
    assert r["lead"]["stage"] == "oportunidade"

    r = _cad(client, lead["id"], "ganho")
    assert r["lead"]["stage"] == "ganho"
    assert r["cadencia"]["proximas"] == []


def test_sem_demanda_vai_para_aquecimento(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    _cad(client, lead["id"], "iniciar")
    _cad(client, lead["id"], "reuniao_marcada")
    r = _cad(client, lead["id"], "qualificado_sem_demanda")
    assert r["lead"]["cadence"] == "aquecimento"


def test_acao_fora_da_etapa_e_recusada(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    resp = client.post(f"/api/leads/{lead['id']}/cadencia",
                       json={"resultado": "qualificado_com_demanda"})
    assert resp.status_code == 422


def test_aquecimento_recomeca_ao_fim_do_ciclo(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    r = _cad(client, lead["id"], "aquecimento")
    ids = {a["step"]: a["id"] for a in r["criadas"]}
    for step in ("aquec_email_1", "aquec_email_2", "aquec_email_3"):
        client.patch(f"/api/activities/{ids[step]}", json={"completed": True})
    client.patch(f"/api/activities/{ids['aquec_whatsapp']}", json={"completed": True})

    proximas = client.get(f"/api/leads/{lead['id']}/cadencia").json()["proximas"]
    assert [a["step"] for a in proximas] == [
        "aquec_email_1", "aquec_email_2", "aquec_email_3", "aquec_whatsapp"]


def test_documenta_na_timeline(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    _cad(client, lead["id"], "iniciar", notas="Falar com o TI")
    notas = [a for a in client.get(f"/api/leads/{lead['id']}/activities").json()
             if a["type"] == "note"]
    assert notas and "Falar com o TI" in notas[0]["notes"]


# ── Contatos cadastrados à mão ───────────────────────────────────────────────

def test_cadastro_manual_de_decisor(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    resp = client.post(f"/api/leads/{lead['id']}/decisores", json={
        "name": "Maria Souza", "title": "Gerente de TI", "phone": "(11) 98888-7777",
        "email": "Maria@Acme.com.br", "linkedin_url": "linkedin.com/in/maria"})
    assert resp.status_code == 201, resp.text
    dm = resp.json()
    assert dm["source"] == "manual"
    assert dm["is_primary"] is True                     # primeiro vira principal
    assert dm["email"] == "maria@acme.com.br"
    assert dm["linkedin_url"].startswith("https://")

    segundo = client.post(f"/api/leads/{lead['id']}/decisores",
                          json={"name": "João", "is_primary": True}).json()
    lista = client.get(f"/api/leads/{lead['id']}/decisores").json()
    assert lista[0]["id"] == segundo["id"]
    assert sum(1 for d in lista if d["is_primary"]) == 1

    r = client.patch(f"/api/decisores/{dm['id']}", json={"email": ""}).json()
    assert r["email"] is None

    assert client.delete(f"/api/decisores/{segundo['id']}").status_code == 204
    assert len(client.get(f"/api/leads/{lead['id']}/decisores").json()) == 1


def test_validacao_do_contato(client):
    lead = _enrich(client)
    url = f"/api/leads/{lead['id']}/decisores"
    assert client.post(url, json={"name": ""}).status_code == 422
    assert client.post(url, json={"name": "X", "email": "sem-arroba"}).status_code == 422
    assert client.post(url, json={"name": "X", "phone": "123"}).status_code == 422


def test_agenda_usa_contato_principal_nos_links(client):
    lead = _enrich(client, mx_provider="Locaweb", employee_count=_ec(51, 200))
    client.post(f"/api/leads/{lead['id']}/decisores", json={
        "name": "Maria Souza", "phone": "(11) 98888-7777", "email": "maria@acme.com.br"})
    _cad(client, lead["id"], "iniciar")
    item = client.get("/api/agenda").json()["hoje"][0]
    assert item["contato"]["name"] == "Maria Souza"
    assert item["acao"]["tel_url"] == "tel:+5511988887777"
    assert item["acao"]["whatsapp_url"].startswith("https://wa.me/5511988887777?text=")
    assert item["acao"]["mailto_url"].startswith("mailto:maria@acme.com.br")


def test_contato_de_outro_usuario_e_404(client):
    db = _Session()
    alheio = Lead(user_id="outro", raw_input_domain="x.com", domain="x.com", status="enriched")
    db.add(alheio)
    db.commit()
    lid = alheio.id
    db.close()
    assert client.post(f"/api/leads/{lid}/decisores", json={"name": "X"}).status_code == 404
    assert client.get(f"/api/leads/{lid}/cadencia").status_code == 404
