"""
GET /api/leads/{id}/contacts e POST /api/decision-makers/{id}/reveal.

Substituem o antigo `/popular-contacts`, que chamava `/v2/company` — endpoint
de dados firmográficos que nunca devolveu lista de pessoas.

O que estes testes protegem, em ordem de importância:

1. **Crédito do usuário.** Listar é barato (1 por 25), revelar telefone custa 5.
   Nada pode gastar crédito sem clique, e revelar duas vezes não pode cobrar
   duas vezes.
2. **A tela nunca cai.** Sem chave, sem crédito, em rate limit ou com a Lusha
   fora do ar, o caminho gratuito assume.
3. **Fidelidade do caminho gratuito.** Contato sem perfil pessoal no LinkedIn
   não entra: um contato errado custa mais que nenhum.

Atenção ao mockar: são DUAS coisas. `routers.enrichment._lusha_key_utilizavel`
(sem isso o perfil de teste não tem chave e o código nem chega na Lusha) e a
função do provedor.
"""
from unittest.mock import patch

import pytest

from tests.test_api import client, clean_db, _Session, MOCK_ENRICH_RESULT  # noqa: F401

from models.database import DecisionMaker


def _make_lead(client) -> dict:
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "nubank.com.br"})
    return resp.json()["data"]


_GRATUITO = [{
    "name": "Ana Souza",
    "title_searched": "CTO",
    "title_found": "Chief Technology Officer",
    "snippet": "Ana Souza - CTO na Nubank",
    "linkedin_url": "https://www.linkedin.com/in/ana-souza",
    "probable_emails": [{"email": "ana@nubank.com.br", "status": "valid", "confidence": 97}],
    "match_confidence": "high",
    "phone": None,
}]


def _lusha_search(qtd=2, total=None):
    contatos = []
    for i in range(qtd):
        contatos.append({
            "lusha_contact_id": f"v1.id{i}",
            "name": f"Contato {i}",
            "title": "Head of Engineering",
            "linkedin_url": f"https://www.linkedin.com/in/contato-{i}",
            "location": "Belo Horizonte, Brazil",
            "department": "Engineering & Technical",
            "seniority": "director",
            "company_name": "Nubank",
            "company_domain": "nubank.com.br",
            "company_industries": ["Financial Services", "Banking"],
            "can_reveal": [{"field": "emails", "credits": 1}, {"field": "phones", "credits": 5}],
            "data_points": {"work_email": 1, "mobile_phone": 1},
            "emails": [],
            "phones": [],
        })
    return {
        "contacts": contatos,
        "total": total if total is not None else qtd,
        "page": 0,
        "page_size": 20,
        "limites": {},
        "request_id": "req_1",
    }


# ── Caminho pago ────────────────────────────────────────────────────────────

def test_com_chave_usa_a_lusha_e_marca_a_fonte(client):
    lead = _make_lead(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts",
               return_value=_lusha_search(2, total=47)) as busca:
        resp = client.get(f"/api/leads/{lead['id']}/contacts")

    assert resp.status_code == 200
    d = resp.json()
    assert d["fonte"] == "lusha"
    assert d["total"] == 47
    assert len(d["contatos"]) == 2
    busca.assert_called_once()


def test_contatos_da_lusha_chegam_nao_revelados(client):
    """
    O search não traz e-mail nem telefone — se chegassem preenchidos, seria
    sinal de que crédito foi gasto sem ninguém clicar.
    """
    lead = _make_lead(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts", return_value=_lusha_search(2)):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")

    for c in resp.json()["contatos"]:
        assert c["revealed"] is False
        assert c["phone"] is None
        assert not c["probable_emails"]
        # Mas já sabe o que dá para revelar e por quanto.
        assert {i["field"] for i in c["can_reveal"]} == {"emails", "phones"}


def test_localizacao_vem_da_api_e_nao_de_texto_fixo(client):
    """A tela escrevia "São Paulo, Brazil" fixo no JS, errado fora de SP."""
    lead = _make_lead(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts", return_value=_lusha_search(1)):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")

    assert resp.json()["contatos"][0]["location"] == "Belo Horizonte, Brazil"


def test_filtros_e_paginacao_chegam_ao_provedor(client):
    lead = _make_lead(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts",
               return_value=_lusha_search(1)) as busca:
        client.get(
            f"/api/leads/{lead['id']}/contacts"
            "?page=2&page_size=30&seniority=9&seniority=6"
            "&departments=Sales&job_titles=CTO&data_points=mobile_phone"
        )

    kw = busca.call_args.kwargs
    assert kw["page"] == 2
    assert kw["page_size"] == 30
    assert kw["seniority_ids"] == [9, 6]
    assert kw["departments"] == ["Sales"]
    assert kw["job_titles"] == ["CTO"]
    assert kw["existing_data_points"] == ["mobile_phone"]


@pytest.mark.parametrize("pedido,esperado", [(1, 10), (5, 10), (999, 50), (50, 50)])
def test_page_size_e_limitado_ao_intervalo_da_api(client, pedido, esperado):
    """Fora de 10..50 a Lusha devolve 400 — e um 400 pode cobrar crédito."""
    lead = _make_lead(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts",
               return_value=_lusha_search(1)) as busca:
        client.get(f"/api/leads/{lead['id']}/contacts?page_size={pedido}")

    assert busca.call_args.kwargs["page_size"] == esperado


def test_folhear_paginas_nao_duplica_contato(client):
    """Sem limpar a página anterior, o contador da tela passaria a mentir."""
    lead = _make_lead(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts", return_value=_lusha_search(2)):
        client.get(f"/api/leads/{lead['id']}/contacts?page=0")
        resp = client.get(f"/api/leads/{lead['id']}/contacts?page=1")

    assert len(resp.json()["contatos"]) == 2
    db = _Session()
    try:
        gravados = db.query(DecisionMaker).filter(
            DecisionMaker.lead_id == lead["id"], DecisionMaker.source == "lusha"
        ).count()
        assert gravados == 2
    finally:
        db.close()


def test_nova_busca_preserva_contato_ja_revelado(client):
    """
    Regravar um contato revelado apagaria o e-mail e o telefone que o usuário
    já pagou — e ele teria que pagar de novo para ver o mesmo dado.
    """
    lead = _make_lead(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts", return_value=_lusha_search(1)):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")
    dm_id = resp.json()["contatos"][0]["id"]

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.enrich_contacts", return_value={
             "contacts": [{
                 "lusha_contact_id": "v1.id0", "name": "Contato 0", "title": None,
                 "linkedin_url": None, "location": None, "department": None,
                 "seniority": None, "company_name": None, "company_domain": None,
                 "company_industries": [], "can_reveal": [], "data_points": {},
                 "emails": [{"email": "c0@nubank.com.br", "status": "unknown", "confidence": 92}],
                 "phones": [{"e164": "+5511999998888", "formatted": "11999998888",
                             "type": "mobile", "confidence": 92}],
             }],
             "limites": {},
         }):
        client.post(f"/api/decision-makers/{dm_id}/reveal", json={})

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts", return_value=_lusha_search(1)):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")

    contato = resp.json()["contatos"][0]
    assert contato["revealed"] is True
    assert contato["phone"] == "+5511999998888"


# ── Queda para o caminho gratuito ───────────────────────────────────────────

def test_sem_chave_cai_no_gratuito_e_avisa(client):
    """Sem chave conectada, zero requisições à Lusha."""
    lead = _make_lead(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value=None), \
         patch("routers.enrichment.lusha_prospecting.search_contacts") as busca, \
         patch("routers.enrichment.find_decision_makers", return_value=_GRATUITO):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")

    busca.assert_not_called()
    d = resp.json()
    assert d["fonte"] == "free"
    assert len(d["contatos"]) == 1
    assert "Configurações" in d["erro"]


@pytest.mark.parametrize("status,pedaco", [
    (402, "crédito"),
    (429, "limite"),
    (401, "chave"),
])
def test_lusha_recusando_cai_no_gratuito_dizendo_o_motivo(client, status, pedaco):
    """
    Sem crédito o usuário compra, em rate limit ele espera. A tela precisa
    saber qual dos dois foi para não dar o conselho errado.
    """
    lead = _make_lead(client)

    def _falha(*a, **kw):
        kw["erro_out"]["status"] = status
        from services.providers import lusha_prospecting as lp
        kw["erro_out"]["mensagem"] = lp.erro_legivel(status)
        return None

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts", side_effect=_falha), \
         patch("routers.enrichment.find_decision_makers", return_value=_GRATUITO):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")

    d = resp.json()
    assert d["fonte"] == "free"
    assert len(d["contatos"]) == 1
    assert pedaco in d["erro"].lower()


def test_lusha_fora_do_ar_cai_no_gratuito(client):
    lead = _make_lead(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts", return_value=None), \
         patch("routers.enrichment.find_decision_makers", return_value=_GRATUITO):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")

    assert resp.json()["fonte"] == "free"
    assert len(resp.json()["contatos"]) == 1


def test_gratuito_exige_perfil_pessoal_do_linkedin(client):
    """
    Página de empresa não prova que a pessoa trabalha lá. Sem `linkedin.com/in/`
    o contato não entra — um contato errado custa mais que nenhum.
    """
    lead = _make_lead(client)
    mistura = [
        dict(_GRATUITO[0], name="Com perfil"),
        dict(_GRATUITO[0], name="Só empresa",
             linkedin_url="https://www.linkedin.com/company/nubank"),
        dict(_GRATUITO[0], name="Sem nada", linkedin_url=None),
    ]

    with patch("routers.enrichment._lusha_key_utilizavel", return_value=None), \
         patch("routers.enrichment.find_decision_makers", return_value=mistura):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")

    nomes = [c["name"] for c in resp.json()["contatos"]]
    assert nomes == ["Com perfil"]


def test_gratuito_e_paginado_no_backend(client):
    lead = _make_lead(client)
    muitos = [dict(_GRATUITO[0], name=f"Pessoa {i}") for i in range(25)]

    with patch("routers.enrichment._lusha_key_utilizavel", return_value=None), \
         patch("routers.enrichment.find_decision_makers", return_value=muitos):
        resp = client.get(f"/api/leads/{lead['id']}/contacts?page=1&page_size=10")

    d = resp.json()
    assert d["total"] == 25
    assert len(d["contatos"]) == 10
    assert d["contatos"][0]["name"] == "Pessoa 10"


def test_lead_de_outro_usuario_da_404(client):
    resp = client.get("/api/leads/999999/contacts")
    assert resp.status_code == 404


# ── Revelação ───────────────────────────────────────────────────────────────

_ENRICHED = {
    "contacts": [{
        "lusha_contact_id": "v1.id0", "name": "Contato 0", "title": "Head of Engineering",
        "linkedin_url": None, "location": "Belo Horizonte, Brazil", "department": None,
        "seniority": None, "company_name": None, "company_domain": None,
        "company_industries": [], "can_reveal": [], "data_points": {},
        "emails": [{"email": "c0@nubank.com.br", "status": "unknown", "confidence": 92}],
        "phones": [{"e164": "+5511999998888", "formatted": "11999998888",
                    "type": "mobile", "confidence": 92}],
    }],
    "limites": {},
}


def _um_contato_lusha(client):
    lead = _make_lead(client)
    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts", return_value=_lusha_search(1)):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")
    return resp.json()["contatos"][0]["id"]


def test_revelar_grava_email_e_telefone_e_cobra_o_esperado(client):
    dm_id = _um_contato_lusha(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.enrich_contacts", return_value=_ENRICHED):
        resp = client.post(f"/api/decision-makers/{dm_id}/reveal", json={})

    assert resp.status_code == 200
    d = resp.json()
    assert d["contato"]["phone"] == "+5511999998888"
    assert d["contato"]["probable_emails"][0]["email"] == "c0@nubank.com.br"
    assert d["contato"]["revealed"] is True
    assert d["ja_revelado"] is False
    # 1 crédito pelo e-mail + 5 pelo telefone.
    assert d["creditos_gastos"] == 6


def test_revelar_duas_vezes_nao_chama_a_lusha_de_novo(client):
    """Revelar duas vezes cobraria duas vezes pelo mesmo dado."""
    dm_id = _um_contato_lusha(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.enrich_contacts", return_value=_ENRICHED):
        client.post(f"/api/decision-makers/{dm_id}/reveal", json={})

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.enrich_contacts") as enrich:
        resp = client.post(f"/api/decision-makers/{dm_id}/reveal", json={})

    enrich.assert_not_called()
    d = resp.json()
    assert d["ja_revelado"] is True
    assert d["creditos_gastos"] == 0
    # E o dado pago continua lá.
    assert d["contato"]["phone"] == "+5511999998888"


def test_revelar_so_email_nao_pede_telefone(client):
    """Quem quer só o e-mail paga 1, não 6."""
    dm_id = _um_contato_lusha(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.enrich_contacts",
               return_value=_ENRICHED) as enrich:
        client.post(f"/api/decision-makers/{dm_id}/reveal", json={"reveal": ["emails"]})

    assert enrich.call_args.kwargs["reveal"] == ["emails"]


def test_revelar_campo_fora_do_can_reveal_e_recusado_antes_da_rede(client):
    """Pedir um campo que o contato não permite é 400 na Lusha — e pode cobrar."""
    lead = _make_lead(client)
    so_email = _lusha_search(1)
    so_email["contacts"][0]["can_reveal"] = [{"field": "emails", "credits": 1}]

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.search_contacts", return_value=so_email):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")
    dm_id = resp.json()["contatos"][0]["id"]

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.enrich_contacts") as enrich:
        resp = client.post(f"/api/decision-makers/{dm_id}/reveal", json={"reveal": ["phones"]})

    enrich.assert_not_called()
    assert resp.status_code == 422


def test_revelar_contato_gratuito_e_recusado(client):
    """Contato de fonte pública não tem ID na Lusha — não há o que revelar."""
    lead = _make_lead(client)
    with patch("routers.enrichment._lusha_key_utilizavel", return_value=None), \
         patch("routers.enrichment.find_decision_makers", return_value=_GRATUITO):
        resp = client.get(f"/api/leads/{lead['id']}/contacts")
    dm_id = resp.json()["contatos"][0]["id"]

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.enrich_contacts") as enrich:
        resp = client.post(f"/api/decision-makers/{dm_id}/reveal", json={})

    enrich.assert_not_called()
    assert resp.status_code == 422


def test_revelar_sem_chave_e_recusado(client):
    dm_id = _um_contato_lusha(client)

    with patch("routers.enrichment._lusha_key_utilizavel", return_value=None), \
         patch("routers.enrichment.lusha_prospecting.enrich_contacts") as enrich:
        resp = client.post(f"/api/decision-makers/{dm_id}/reveal", json={})

    enrich.assert_not_called()
    assert resp.status_code == 422


def test_revelar_sem_credito_diz_que_e_falta_de_credito(client):
    dm_id = _um_contato_lusha(client)

    def _sem_credito(*a, **kw):
        from services.providers import lusha_prospecting as lp
        kw["erro_out"]["status"] = 402
        kw["erro_out"]["mensagem"] = lp.erro_legivel(402)
        return None

    with patch("routers.enrichment._lusha_key_utilizavel", return_value="chave"), \
         patch("routers.enrichment.lusha_prospecting.enrich_contacts", side_effect=_sem_credito):
        resp = client.post(f"/api/decision-makers/{dm_id}/reveal", json={})

    assert resp.status_code == 502
    assert "crédito" in resp.json()["detail"].lower()


def test_revelar_contato_de_outro_usuario_da_404(client):
    resp = client.post("/api/decision-makers/999999/reveal", json={})
    assert resp.status_code == 404


# ── Filtros da barra lateral ────────────────────────────────────────────────

def test_filtros_nao_consomem_credito_e_vem_na_ordem_da_extensao(client):
    with patch("routers.enrichment.lusha_prospecting.search_contacts") as busca:
        resp = client.get("/api/lusha/filters")

    busca.assert_not_called()
    d = resp.json()
    assert [s["id"] for s in d["seniority"]] == [10, 7, 9, 8, 6, 5, 4, 3, 2, 1]
    assert "Engineering & Technical" in d["departments"]
    assert d["pricing"]["revealPhone"]["credits"] == 5
    assert d["page_size"] == {"min": 10, "max": 50, "default": 20}
