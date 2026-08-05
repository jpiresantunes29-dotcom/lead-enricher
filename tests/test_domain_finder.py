"""
Testes da descoberta de domínio (services/domain_finder.py).

As buscas são mockadas — o que se testa aqui é o julgamento: descartar rede
social e agregador, casar o nome da empresa com o domínio e recusar quando
não dá para ter certeza. Aceitar um domínio errado é pior que não achar: o
enriquecimento gravaria a ficha de outra empresa.
"""
from unittest.mock import patch

import pytest

from services.domain_finder import _core_name, _name_similarity, find_domain


def _results(*urls):
    return [{"url": u, "title": ""} for u in urls]


def _find(company, urls, linkedin=None, public=True, site_confirm=0.0):
    with patch("services.domain_finder.search_multi", return_value=_results(*urls)), \
         patch("services.domain_finder.is_public_host", return_value=public), \
         patch("services.domain_finder._confirm_on_site", return_value=site_confirm):
        return find_domain(company, linkedin)


# ── casamento de nome ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("company,expected", [
    ("Abix Tecnologia Ltda", "abix"),
    ("Grupo Hobi | Hobimix", "hobi"),
    ("Irmãos Dalacorte Transportes S/A", "irmaosdalacorte"),
])
def test_core_name_tira_ruido_juridico(company, expected):
    assert _core_name(company).startswith(expected)


def test_similaridade_alta_quando_o_dominio_e_a_marca():
    assert _name_similarity("Abix Tecnologia Ltda", "abix.com.br") == 1.0
    assert _name_similarity("FGM Dental Group", "fgmdentalgroup.com") == 1.0


def test_similaridade_baixa_para_empresa_diferente():
    assert _name_similarity("Lola Soluções Têxteis", "nutritec.ind.br") < 0.5


# ── escolha do candidato ──────────────────────────────────────────────────────
def test_encontra_o_site_oficial():
    result = _find("Abix Tecnologia", ["https://abix.com.br/sobre"])
    assert result["domain"] == "abix.com.br"
    assert result["confidence"] == "high"


def test_descarta_redes_sociais_e_agregadores():
    result = _find("Abix Tecnologia", [
        "https://www.linkedin.com/company/abix",
        "https://www.facebook.com/abix",
        "https://econodata.com.br/consulta-empresa/abix",
        "https://abix.com.br",
    ])
    assert result["domain"] == "abix.com.br"


def test_sem_candidato_util_devolve_none():
    result = _find("Abix Tecnologia", [
        "https://www.linkedin.com/company/abix",
        "https://vagas.com.br/empresa/abix",
    ])
    assert result["domain"] is None
    assert result["confidence"] == "none"


def test_recusa_dominio_que_nao_confere_com_o_nome():
    """Sem parecença e sem confirmação no site, é melhor não chutar."""
    result = _find("Cervejaria OPA BIER", ["https://distribuidoraxyz.com.br"])
    assert result["domain"] is None


def test_site_confirmando_o_nome_salva_o_candidato():
    result = _find("Cervejaria OPA BIER", ["https://bebidasdosul.com.br"], site_confirm=1.0)
    assert result["domain"] == "bebidasdosul.com.br"
    assert result["confidence"] == "high"


def test_slug_do_linkedin_desempata():
    result = _find(
        "Indústria e Comércio Reunidas",
        ["https://irec.com.br"],
        linkedin="https://www.linkedin.com/company/irec/",
    )
    assert result["domain"] == "irec.com.br"


def test_ignora_host_que_nao_resolve_ou_e_interno():
    result = _find("Abix Tecnologia", ["https://abix.com.br"], public=False)
    assert result["domain"] is None


def test_sem_nome_nao_busca():
    with patch("services.domain_finder.search_multi") as search:
        result = find_domain("")
    assert result["domain"] is None
    assert not search.called


def test_busca_quebrada_nao_derruba():
    with patch("services.domain_finder.search_multi", side_effect=RuntimeError("engine fora")):
        result = find_domain("Abix Tecnologia")
    assert result["domain"] is None
    assert result["confidence"] == "none"
