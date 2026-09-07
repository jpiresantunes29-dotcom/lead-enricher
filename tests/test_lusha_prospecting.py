"""
Lusha Prospecting: parser, validação antes da rede e degradação.

O teste mais importante deste arquivo é o do parser contra fixture. A
implementação anterior quebrou por escrever o parser contra um formato suposto
— chamava `/v2/company`, que devolve dados firmográficos, esperando lista de
pessoas — e a extração tolerante devolvia `None` em silêncio em vez de erro.
Travar o formato numa fixture faz uma mudança da Lusha quebrar um teste, que é
o comportamento desejado, em vez de degradar calada.

⚠️ ATENÇÃO: as fixtures em tests/fixtures/ ainda foram montadas a partir da
documentação, não capturadas de uma resposta real. Ver tests/fixtures/LEIA-ME.md
para fechar isso (custa 1 crédito). Até lá, estes testes provam que o parser é
coerente com o formato documentado — não que o formato documentado é o real.
"""
import json
import unicodedata
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from services.providers import lusha_prospecting as lp

FIXTURES = Path(__file__).parent / "fixtures"


def _carregar(nome: str) -> dict:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


def _resposta(status=200, payload=None, headers=None):
    r = Mock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.json = Mock(return_value=payload if payload is not None else {})
    return r


# ── Parser contra fixture ───────────────────────────────────────────────────

def test_parser_contra_fixture_do_search():
    """
    Cada campo que a tela usa sai do lugar certo da resposta.

    Fixture capturada de uma chamada REAL em 2026-09-07 (domínio
    nubank.com.br, chave própria do usuário, 1 crédito) — não montada a
    partir de documentação. É a mesma empresa que o usuário mostrou numa
    screenshot da extensão da Lusha, e o primeiro nome que sai (David Vélez,
    fundador) bate com o primeiro contato da screenshot.
    """
    payload = _carregar("lusha_search.json")
    contatos = [lp.parse_contact(c) for c in payload["results"]]
    contatos = [c for c in contatos if c]

    assert len(contatos) == 10

    david = contatos[0]
    assert david["lusha_contact_id"] == "v1.zIfDcBPYH1LrjoDWCmfLU9EDUsnOar6TLQ"
    # NFC vs NFD: o "é" pode chegar como um único codepoint ou como "e" +
    # acento combinante — normaliza antes de comparar para não depender de
    # qual forma o editor deste arquivo escolheu.
    assert unicodedata.normalize("NFC", david["name"]) == "David Vélez"
    assert david["title"] == "Founder, Chief Executive Officer"
    assert david["linkedin_url"] == "https://www.linkedin.com/in/david-vélez-1004875"
    assert david["department"] == "General Management"
    # Senioridade no search já vem como rótulo em texto minúsculo, não ID.
    assert david["seniority"] == "founder"
    assert david["company_name"] == "Nubank"
    # `company.industry` não veio no search desta fixture real (só `id`,
    # `name`, `domain`) — confirma que industry é exclusivo do enrich.
    assert david["company_industries"] == []


def test_parser_monta_localizacao_da_api_e_nao_de_texto_fixo():
    """
    A tela escrevia "São Paulo, Brazil" fixo no JavaScript — certo para uma
    minoria e errado para todo o resto. A localização tem que vir do contato.
    """
    payload = _carregar("lusha_search.json")
    contatos = [lp.parse_contact(c) for c in payload["results"]]

    # City e state de David são o mesmo texto ("São Paulo") — o dedup do
    # parser junta os dois em vez de repetir.
    assert contatos[0]["location"] == "São Paulo, Brazil"
    # John Walton mora nos EUA — a fixture prova que o rótulo não é chumbado.
    assert contatos[1]["location"] == "Woodinville, Washington, United States"
    # Rob Livingston só tem country — sem vírgula solta nem campo vazio.
    assert contatos[2]["location"] == "United States"


def test_parser_le_can_reveal_com_o_custo():
    """
    `canReveal` é o que decide se o botão de revelar aparece e por quanto.
    Sem ele a tela ofereceria revelação que a Lusha recusaria com 400.
    """
    payload = _carregar("lusha_search.json")
    david, john = [lp.parse_contact(c) for c in payload["results"][:2]]

    # David já tinha sido revelado antes (por esta conta): credits 0 em
    # ambos os campos é o sinal real de "grátis para re-enriquecer".
    assert david["can_reveal"] == [
        {"field": "emails", "credits": 0},
        {"field": "phones", "credits": 0},
    ]
    # John nunca foi revelado: preço cheio.
    assert john["can_reveal"] == [
        {"field": "emails", "credits": 1},
        {"field": "phones", "credits": 5},
    ]


def test_parser_conta_pontos_de_dados_para_os_badges():
    """
    `has` no formato real (V3ContactPreview) é um conjunto de nomes de campo
    presentes, não uma contagem por quantidade — cada campo aparece no máximo
    uma vez. O badge "quantos celulares" não tem suporte confirmado; o que a
    API garante é "este contato tem telefone", não "tem dois".
    """
    payload = _carregar("lusha_search.json")
    david = lp.parse_contact(payload["results"][0])
    assert david["data_points"]["phones"] == 1
    assert david["data_points"]["emails"] == 1
    assert "mobile_phone" not in david["data_points"]


def test_search_nao_traz_email_nem_telefone():
    """
    Se o search trouxesse contato revelado, a tela mostraria dado pago sem
    ninguém ter clicado — e o crédito teria sido gasto sem pedido.
    """
    payload = _carregar("lusha_search.json")
    for bruto in payload["results"]:
        c = lp.parse_contact(bruto)
        assert c["emails"] == []
        assert c["phones"] == []


def test_parser_contra_fixture_do_enrich():
    """
    Fixture capturada de uma chamada REAL de enrich em 2026-09-07 (mesmo
    contato do search acima, David Vélez — +1 crédito, mas cobrou 0 porque já
    tinha sido revelado antes por esta conta, confirmando o canReveal.credits
    de 0 do search).
    """
    payload = _carregar("lusha_enrich.json")
    david = lp.parse_contact(payload["results"][0])

    assert [e["email"] for e in david["emails"]] == ["david.velez@nubank.com.br"]
    assert david["emails"][0]["confidence"] == lp.CONF_LUSHA
    # `dataSource` NÃO está no schema formal (V3EmailAddress/V3PhoneNumber),
    # mas a resposta real da conta trouxe `"dataSource": "lusha"` mesmo assim
    # — o schema publicado está incompleto. O parser aceita o campo quando
    # vem, então o valor certo aqui é "lusha", não None.
    assert david["emails"][0]["dataSource"] == "lusha"
    assert david["company_industries"] == ["Finance"]


def test_enrich_poe_celular_antes_do_fixo():
    """
    Celular é exatamente o que o caminho gratuito nunca entrega. Se o fixo
    vier primeiro, a tela mostra o número da central como se fosse o do
    decisor. A resposta real trouxe dois telefones — um "phone" (tipo não
    mapeado, cai em "unknown") e um "mobile" — na ordem phone-depois-mobile;
    o parser reordena.
    """
    payload = _carregar("lusha_enrich.json")
    david = lp.parse_contact(payload["results"][0])

    assert len(david["phones"]) == 2
    assert david["phones"][0]["type"] == "mobile"
    assert david["phones"][0]["e164"] == "+59899381621"
    assert david["phones"][1]["e164"] == "+16503872695"


def test_parser_descarta_contato_sem_id():
    """Sem ID não há como revelar depois — o card seria um beco sem saída."""
    assert lp.parse_contact({"firstName": "Sem", "lastName": "Id"}) is None


def test_parser_descarta_contato_sem_nome():
    assert lp.parse_contact({"id": "v1.abc"}) is None


def test_parser_nao_quebra_com_lixo():
    for entrada in (None, [], "texto", 42, {}):
        assert lp.parse_contact(entrada) is None


# ── Validação antes da rede ─────────────────────────────────────────────────
#
# Tudo aqui é recusado ANTES de sair da máquina. Um 400 da Lusha é uma ida que
# não devolve dado e ainda pode cobrar — checar na entrada é de graça.

@pytest.mark.parametrize("tamanho", [0, 1, 9, 101, 200, -5, None, "20"])
def test_search_recusa_page_size_invalido_sem_ir_a_rede(tamanho):
    with patch.object(lp.requests, "post") as post:
        assert lp.search_contacts("chave", ["acme.com"], page_size=tamanho) is None
    post.assert_not_called()


@pytest.mark.parametrize("tamanho", [10, 25, 100])
def test_search_aceita_page_size_no_intervalo(tamanho):
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"results": []})) as post:
        assert lp.search_contacts("chave", ["acme.com"], page_size=tamanho) is not None
    post.assert_called_once()


def test_search_recusa_pagina_negativa_sem_ir_a_rede():
    with patch.object(lp.requests, "post") as post:
        assert lp.search_contacts("chave", ["acme.com"], page=-1) is None
    post.assert_not_called()


def test_search_sem_dominio_nao_vai_a_rede():
    """A API exige ao menos um filtro; sem domínio a chamada seria 400."""
    with patch.object(lp.requests, "post") as post:
        assert lp.search_contacts("chave", []) is None
        assert lp.search_contacts("chave", ["", "   "]) is None
    post.assert_not_called()


def test_search_sem_chave_nao_vai_a_rede():
    """Sem chave conectada, zero requisições à Lusha."""
    with patch.object(lp.requests, "post") as post:
        assert lp.search_contacts("", ["acme.com"]) is None
        assert lp.search_contacts("   ", ["acme.com"]) is None
    post.assert_not_called()


def test_enrich_recusa_mais_de_100_ids_sem_ir_a_rede():
    with patch.object(lp.requests, "post") as post:
        assert lp.enrich_contacts("chave", ["v1.x"] * 101) is None
    post.assert_not_called()


def test_enrich_aceita_exatamente_100_ids():
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"results": []})) as post:
        assert lp.enrich_contacts("chave", ["v1.x"] * 100) is not None
    post.assert_called_once()


def test_enrich_sem_id_nao_vai_a_rede():
    with patch.object(lp.requests, "post") as post:
        assert lp.enrich_contacts("chave", []) is None
        assert lp.enrich_contacts("chave", ["", "  "]) is None
    post.assert_not_called()


def test_enrich_recusa_campo_de_reveal_desconhecido():
    """
    Pedir um campo fora de emails/phones é 400. Recusar antes evita a ida — e
    evita cobrar crédito por uma requisição que não devolve nada.
    """
    with patch.object(lp.requests, "post") as post:
        assert lp.enrich_contacts("chave", ["v1.x"], reveal=["endereco"]) is None
    post.assert_not_called()


def test_search_descarta_filtro_invalido_em_vez_de_repassar():
    """
    Um ID de senioridade ou departamento inválido faria a Lusha rejeitar a
    requisição inteira. Descartar o valor solto preserva o resto da busca.
    """
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"results": []})) as post:
        lp.search_contacts(
            "chave", ["acme.com"],
            seniority_ids=[9, 999],
            departments=["Sales", "Departamento Inventado"],
            existing_data_points=["mobile_phone", "telepatia"],
        )
    # Confirmado 2026-09-07: filtros de contato ficam em filters.contacts.include,
    # e o campo de senioridade é seniorityIds (não seniority).
    filtros = post.call_args.kwargs["json"]["filters"]["contacts"]["include"]
    assert filtros["seniorityIds"] == [9]
    assert filtros["departments"] == ["Sales"]
    assert filtros["existingDataPoints"] == ["mobile_phone"]


# ── Degradação: nada aqui pode levantar exceção ─────────────────────────────

@pytest.mark.parametrize("status", [400, 401, 402, 429, 451, 500, 503])
def test_search_devolve_none_em_qualquer_erro_http(status):
    with patch.object(lp.requests, "post", return_value=_resposta(status)):
        assert lp.search_contacts("chave", ["acme.com"]) is None


@pytest.mark.parametrize("status", [400, 401, 402, 429, 451, 500])
def test_enrich_devolve_none_em_qualquer_erro_http(status):
    with patch.object(lp.requests, "post", return_value=_resposta(status)):
        assert lp.enrich_contacts("chave", ["v1.x"]) is None


@pytest.mark.parametrize("excecao", [
    requests.Timeout("estourou"),
    requests.ConnectionError("sem rede"),
    requests.RequestException("qualquer coisa"),
])
def test_search_devolve_none_quando_a_rede_falha(excecao):
    with patch.object(lp.requests, "post", side_effect=excecao):
        assert lp.search_contacts("chave", ["acme.com"]) is None


def test_search_devolve_none_com_json_malformado():
    """200 com corpo que não é JSON não pode virar exceção na ficha."""
    resp = _resposta(200)
    resp.json = Mock(side_effect=ValueError("não é json"))
    with patch.object(lp.requests, "post", return_value=resp):
        assert lp.search_contacts("chave", ["acme.com"]) is None


def test_search_com_resposta_de_formato_inesperado_devolve_lista_vazia():
    """
    Foi exatamente isto que escondeu o erro anterior: resposta de outro
    endpoint produzindo um resultado plausível. Agora o resultado é vazio e
    explícito, não None disfarçado de "não achou".
    """
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"industry": "Banking"})):
        r = lp.search_contacts("chave", ["acme.com"])
    assert r is not None
    assert r["contacts"] == []
    assert r["total"] == 0


# ── Distinção 402 x 429 ─────────────────────────────────────────────────────

def test_erro_out_distingue_sem_credito_de_rate_limit():
    """
    Sem crédito o usuário precisa comprar; em rate limit precisa esperar.
    Colapsar os dois em "erro da Lusha" daria o conselho errado na metade dos
    casos.
    """
    erro = {}
    with patch.object(lp.requests, "post", return_value=_resposta(402)):
        lp.search_contacts("chave", ["acme.com"], erro_out=erro)
    assert erro["status"] == 402
    assert "crédito" in erro["mensagem"].lower()

    erro = {}
    with patch.object(lp.requests, "post", return_value=_resposta(429)):
        lp.search_contacts("chave", ["acme.com"], erro_out=erro)
    assert erro["status"] == 429
    assert "limite" in erro["mensagem"].lower()


def test_erro_legivel_cobre_os_status_que_pedem_acao_diferente():
    assert lp.erro_legivel(401) != lp.erro_legivel(402)
    assert lp.erro_legivel(402) != lp.erro_legivel(429)
    assert lp.erro_legivel(None)      # sempre há uma mensagem


# ── Paginação e requisição ──────────────────────────────────────────────────

def test_paginacao_chega_correta_ao_provedor():
    """`page` é 0-based na API — mandar 1-based pularia a primeira página."""
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"results": []})) as post:
        r = lp.search_contacts("chave", ["acme.com"], page=2, page_size=30)

    corpo = post.call_args.kwargs["json"]
    # Campo confirmado é `pagination`, não `pages` — a Lusha recusava com 400
    # "property pages should not exist".
    assert corpo["pagination"] == {"page": 2, "size": 30}
    assert r["page"] == 2
    assert r["page_size"] == 30


def test_dominio_vai_normalizado_e_no_lugar_certo():
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"results": []})) as post:
        lp.search_contacts("chave", ["  NuBank.COM.BR  "])
    corpo = post.call_args.kwargs["json"]
    # Confirmado 2026-09-07: domínio fica em companies.include.domains, não
    # companies.domains direto.
    assert corpo["filters"]["companies"]["include"]["domains"] == ["nubank.com.br"]


def test_chave_vai_no_header_api_key():
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"results": []})) as post:
        lp.search_contacts("  minha-chave  ", ["acme.com"])
    assert post.call_args.kwargs["headers"]["api_key"] == "minha-chave"


def test_total_vem_da_resposta_para_a_paginacao_funcionar():
    """
    Sem `total` a tela não sabe se existe próxima página e o rodapé mente.
    Quando a Lusha não manda, o total é o que veio — nunca um chute maior.
    """
    payload = _carregar("lusha_search.json")
    with patch.object(lp.requests, "post", return_value=_resposta(200, payload)):
        r = lp.search_contacts("chave", ["nubank.com.br"])
    # Total real da base da Lusha para nubank.com.br em 2026-09-07 (a página
    # devolveu só 10, mas a empresa tem 10699 contatos catalogados).
    assert r["total"] == 10699

    with patch.object(lp.requests, "post", return_value=_resposta(200, {"results": payload["results"]})):
        r = lp.search_contacts("chave", ["nubank.com.br"])
    # Sem `pagination.total` na resposta, cai para a contagem do que veio.
    assert r["total"] == 10


def test_enrich_manda_ids_e_reveal():
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"results": []})) as post:
        lp.enrich_contacts("chave", ["v1.a", "v1.b"], reveal=["emails"], waterfall_enabled=False)
    corpo = post.call_args.kwargs["json"]
    # Campo confirmado é `ids`, não `contactIds` (V3ContactsEnrichRequest).
    assert corpo["ids"] == ["v1.a", "v1.b"]
    assert corpo["reveal"] == ["emails"]
    assert corpo["waterfallEnabled"] is False


def test_enrich_sem_waterfall_nao_manda_o_campo():
    """Omitir deixa a conta decidir; mandar False forçaria só-Lusha."""
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"results": []})) as post:
        lp.enrich_contacts("chave", ["v1.a"])
    assert "waterfallEnabled" not in post.call_args.kwargs["json"]


# ── Limites de uso ──────────────────────────────────────────────────────────

def test_limites_saem_dos_headers_da_resposta():
    """
    O plano do usuário pode ser Free (40/min), não o premium (300/min) da
    conta em que os valores foram levantados. Ler dos headers é a única forma
    de saber o limite real dele.
    """
    headers = {
        "x-rate-limit-minute": "40",  "x-minute-requests-left": "38",
        "x-rate-limit-hourly": "100", "x-hourly-requests-left": "95",
        "x-rate-limit-daily": "100",  "x-daily-requests-left": "60",
    }
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"data": []}, headers)):
        r = lp.search_contacts("chave", ["acme.com"])

    assert r["limites"]["minuto"] == {"limite": 40, "restante": 38}
    assert r["limites"]["dia"] == {"limite": 100, "restante": 60}


def test_limites_ausentes_viram_none_e_nao_zero():
    """Zero significaria "acabou"; ausente significa "não sei"."""
    with patch.object(lp.requests, "post", return_value=_resposta(200, {"data": []})):
        r = lp.search_contacts("chave", ["acme.com"])
    assert r["limites"]["minuto"] == {"limite": None, "restante": None}


# ── Vocabulário verificado contra a API real ────────────────────────────────

def test_senioridades_na_ordem_da_extensao_e_com_os_ids_certos():
    """
    Os IDs não são sequenciais na ordem de exibição. Gerar essa lista por loop
    produziria filtros que apontam para a senioridade errada.
    """
    assert [s["id"] for s in lp.SENIORITY] == [10, 7, 9, 8, 6, 5, 4, 3, 2, 1]
    assert [s["label"] for s in lp.SENIORITY] == [
        "founder", "partner", "c-suite", "vice president", "director",
        "manager", "senior", "entry", "intern", "other",
    ]


def test_departamentos_sao_as_strings_exatas():
    """
    Uma variação ("Engineering" em vez de "Engineering & Technical") não dá
    erro — dá zero resultado, que parece "a empresa não tem ninguém".
    """
    assert "Engineering & Technical" in lp.DEPARTMENTS
    assert "Research & Analytics" in lp.DEPARTMENTS
    assert len(lp.DEPARTMENTS) == 16


def test_preco_do_telefone_e_cinco_vezes_o_do_email():
    """
    É o número que justifica a tela inteira: revelar 25 telefones custa 125
    créditos, listar os mesmos 25 custa 1.
    """
    assert lp.PRICING["contactSearch"] == {"credits": 1, "per": 25}
    assert lp.PRICING["revealEmail"]["credits"] == 1
    assert lp.PRICING["revealPhone"]["credits"] == 5
