"""
Testes do parser de planilhas (services/importer.py).

O contrato central é fidelidade: toda coluna vira coluna, toda célula
sobrevive com o tipo certo, e o que o sistema deduz nunca sobrescreve o que o
usuário digitou. Os casos vieram de uma base real de prospecção — cabeçalho
com emoji, célula multi-linha, número guardado como texto, e-mail colado na
linha errada, várias abas no mesmo arquivo.
"""
import csv
import io
from datetime import datetime

import pytest
from openpyxl import Workbook

from models.database import Lead
from services.exporter import build_csv as build_export_csv
from services.importer import (
    MAX_ROWS, SpreadsheetError, domain_matches_company, identity_key,
    build_template_csv, build_template_xlsx, list_sheets,
    parse_employee_count, parse_spreadsheet,
)


def _csv_bytes(rows, delimiter=",", encoding="utf-8-sig"):
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode(encoding)


def _xlsx_bytes(sheets):
    """sheets: {nome: [[linha], ...]}"""
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ok(parsed):
    return [r for r in parsed["rows"] if r["status"] == "ok"]


def _labels(parsed):
    return [c["label"] for c in parsed["columns"]]


def _field_of(parsed, label):
    return next(c["field"] for c in parsed["columns"] if c["label"] == label)


# ── fidelidade: nenhuma coluna, nenhuma célula perdida ────────────────────────
def test_preserva_todas_as_colunas_na_ordem_original():
    content = _csv_bytes([
        ["Empresa", "Vendedor", "CNPJ", "Observações", "LinkedIn"],
        ["Acme", "Ana", "53.113.791/0001-22", "ligar terça", "https://linkedin.com/company/acme"],
    ])
    parsed = parse_spreadsheet("base.csv", content)

    assert _labels(parsed) == ["Empresa", "Vendedor", "CNPJ", "Observações", "LinkedIn"]
    cells = _ok(parsed)[0]["cells"]
    assert cells["Vendedor"] == "Ana"
    assert cells["CNPJ"] == "53.113.791/0001-22"
    assert cells["Observações"] == "ligar terça"


def test_preserva_cabecalho_com_emoji_e_acento():
    content = _csv_bytes([
        ["🏢 Empresa", "📩 Email", "🏆 TIER"],
        ["Acme", "contato@acme.com.br", "TIER 1"],
    ])
    parsed = parse_spreadsheet("base.csv", content)

    assert _labels(parsed) == ["🏢 Empresa", "📩 Email", "🏆 TIER"]
    assert _field_of(parsed, "🏢 Empresa") == "company_name"
    assert _field_of(parsed, "🏆 TIER") is None      # coluna do usuário, sem palpite
    assert _ok(parsed)[0]["cells"]["🏆 TIER"] == "TIER 1"


def test_preserva_celula_multilinha_e_espacos():
    texto = "Empresa: Acme\nEmail Atual: Google\nQtd.Func.: 39"
    content = _csv_bytes([["Empresa", "Info"], ["Acme", texto]])
    parsed = parse_spreadsheet("base.csv", content)
    assert _ok(parsed)[0]["cells"]["Info"] == texto


def test_tipos_preservados_data_numero_texto():
    content = _xlsx_bytes({"Base": [
        ["Empresa", "Data", "Nº Funcionários", "Faixa"],
        ["Acme", datetime(2026, 1, 27), 39, "200 a 500"],
    ]})
    parsed = parse_spreadsheet("base.xlsx", content)

    cells = _ok(parsed)[0]["cells"]
    assert cells["Data"] == "2026-01-27"       # ISO, sem hora zerada
    assert cells["Nº Funcionários"] == 39      # número continua número
    assert cells["Faixa"] == "200 a 500"       # texto continua texto
    kinds = {c["label"]: c["kind"] for c in parsed["columns"]}
    assert kinds["Data"] == "date" and kinds["Nº Funcionários"] == "number"


def test_colunas_sem_titulo_e_repetidas_ganham_nome_unico():
    content = _csv_bytes([["Empresa", "", "Email", "Email"], ["Acme", "x", "a@a.com", "b@b.com"]])
    parsed = parse_spreadsheet("base.csv", content)
    assert _labels(parsed) == ["Empresa", "Coluna 2", "Email", "Email (2)"]
    assert _ok(parsed)[0]["cells"]["Email (2)"] == "b@b.com"


# ── mapeamento para os campos do sistema ─────────────────────────────────────
def test_nome_exato_ganha_de_parecido():
    """'Servidor de Email' é provedor de e-mail, não o e-mail de contato."""
    content = _csv_bytes([
        ["Empresa", "📧 Servidor de Email", "📩 Email"],
        ["Acme", "Google", "contato@acme.com.br"],
    ])
    parsed = parse_spreadsheet("base.csv", content)

    assert _field_of(parsed, "📧 Servidor de Email") == "mx_provider"
    assert _field_of(parsed, "📩 Email") == "corporate_email"
    assert _ok(parsed)[0]["lead"]["corporate_email"] == "contato@acme.com.br"


def test_linkedin_da_empresa_nao_e_confundido_com_o_do_decisor():
    content = _csv_bytes([
        ["Empresa", "🔗 LinkedIn", "🔍 LinkedIn Decisores"],
        ["Acme", "https://www.linkedin.com/company/acme/about/", "https://www.linkedin.com/in/fulano"],
    ])
    parsed = parse_spreadsheet("base.csv", content)

    lead = _ok(parsed)[0]["lead"]
    assert lead["linkedin_url"] == "https://www.linkedin.com/company/acme"
    assert _field_of(parsed, "🔍 LinkedIn Decisores") is None


def test_perfil_pessoal_nao_vira_linkedin_da_empresa():
    content = _csv_bytes([["Empresa", "LinkedIn"], ["Acme", "https://www.linkedin.com/in/fulano"]])
    parsed = parse_spreadsheet("base.csv", content)
    assert "linkedin_url" not in _ok(parsed)[0]["lead"]


def test_mapeia_cabecalho_em_ingles():
    content = _csv_bytes([["Website", "Company Name", "Industry"], ["https://stripe.com", "Stripe", "Payments"]])
    parsed = parse_spreadsheet("companies.csv", content)
    lead = _ok(parsed)[0]["lead"]
    assert lead["domain"] == "stripe.com"
    assert lead["company_name"] == "Stripe"
    assert lead["sector"] == "Payments"


# ── domínio: só quando dá para confiar ───────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("https://www.rdstation.com/contato", "rdstation.com"),
    ("WWW.Embraer.com.br", "embraer.com.br"),
    ("http://gerdau.com.br?utm=x", "gerdau.com.br"),
])
def test_normaliza_dominio_sujo(raw, expected):
    parsed = parse_spreadsheet("x.csv", _csv_bytes([["Site"], [raw]]))
    assert _ok(parsed)[0]["lead"]["domain"] == expected


def test_deriva_dominio_do_email_quando_combina_com_a_empresa():
    content = _csv_bytes([["Empresa", "Email"], ["Abix Tecnologia", "contato@abix.com.br"]])
    lead = _ok(parse_spreadsheet("x.csv", content))[0]["lead"]
    assert lead["domain"] == "abix.com.br"
    assert lead["domain_source"] == "email"


def test_nao_deriva_dominio_de_email_de_outra_empresa():
    """
    Caso real: a linha da "Lola Soluções Têxteis" tinha um e-mail @nutritec.
    Herdar esse domínio enriqueceria a empresa errada.
    """
    content = _csv_bytes([["Empresa", "Email"], ["Lola Soluções Têxteis", "ziguer@nutritec.ind.br"]])
    lead = _ok(parse_spreadsheet("x.csv", content))[0]["lead"]
    assert "domain" not in lead
    assert lead["corporate_email"] == "ziguer@nutritec.ind.br"   # a célula continua lá


@pytest.mark.parametrize("domain,company,expected", [
    ("abix.com.br", "Abix Tecnologia Ltda", True),
    ("fgmdentalgroup.com", "FGM Dental Group", True),
    ("nutritec.ind.br", "Lola Soluções Têxteis", False),
    ("bradesco.com.br", "Coopertruni Logística", False),
])
def test_dominio_combina_com_empresa(domain, company, expected):
    assert domain_matches_company(domain, company) is expected


def test_linha_sem_identificacao_e_marcada_mas_preservada():
    content = _csv_bytes([["Empresa", "Observação"], ["", "só uma nota"], ["Acme", "ok"]])
    parsed = parse_spreadsheet("x.csv", content)

    assert parsed["rows"][0]["status"] == "invalid"
    assert parsed["rows"][0]["cells"]["Observação"] == "só uma nota"
    assert parsed["rows"][1]["status"] == "ok"


def test_empresa_repetida_e_sinalizada_sem_ser_descartada():
    content = _csv_bytes([["Empresa"], ["Acme"], ["ACME"], ["Outra"]])
    parsed = parse_spreadsheet("x.csv", content)

    assert [r["status"] for r in parsed["rows"]] == ["ok", "duplicate_file", "ok"]
    assert "linha 2" in parsed["rows"][1]["reason"]
    assert len(parsed["rows"]) == 3   # nada some


@pytest.mark.parametrize("lead,prefixo", [
    ({"domain": "acme.com.br"}, "d:"),
    ({"linkedin_url": "https://linkedin.com/company/acme"}, "l:"),
    ({"company_name": "Acme Ltda"}, "n:"),
    ({}, ""),
])
def test_chave_de_identidade(lead, prefixo):
    assert identity_key(lead).startswith(prefixo)


# ── abas ──────────────────────────────────────────────────────────────────────
def test_escolhe_a_aba_de_leads_e_lista_as_demais():
    content = _xlsx_bytes({
        "Controle Diário": [["Data", "Leads analisados"], ["2026-01-27", 10]],
        "Base Leads": [["Empresa", "LinkedIn"], ["Acme", "https://linkedin.com/company/acme"]],
    })
    parsed = parse_spreadsheet("base.xlsx", content)

    assert parsed["sheet"] == "Base Leads"
    assert {s["name"] for s in parsed["sheets"]} == {"Controle Diário", "Base Leads"}


def test_aba_pode_ser_escolhida_na_mao():
    content = _xlsx_bytes({
        "Base Leads": [["Empresa"], ["Acme"]],
        "Outra": [["Empresa"], ["Beta"], ["Gama"]],
    })
    parsed = parse_spreadsheet("base.xlsx", content, sheet_name="Outra")
    assert parsed["sheet"] == "Outra"
    assert parsed["total_rows"] == 2


def test_aba_inexistente():
    content = _xlsx_bytes({"Base": [["Empresa"], ["Acme"]]})
    with pytest.raises(SpreadsheetError, match="não existe"):
        parse_spreadsheet("base.xlsx", content, sheet_name="Fantasma")


def test_list_sheets_resume_cada_aba():
    content = _xlsx_bytes({
        "Leads": [["Empresa", "Site"], ["Acme", "acme.com"]],
        "Metricas": [["Data", "Total"], ["2026-01-01", 5]],
    })
    resumo = {s["name"]: s for s in list_sheets("base.xlsx", content)}
    assert resumo["Leads"]["rows"] == 1
    assert resumo["Leads"]["score"] > resumo["Metricas"]["score"]


# ── cabeçalho fora da primeira linha ─────────────────────────────────────────
def test_ignora_linhas_de_titulo_antes_do_cabecalho():
    content = _csv_bytes([["Lista de prospects — Q3"], [], ["Empresa", "Site"], ["Gerdau", "gerdau.com.br"]])
    parsed = parse_spreadsheet("x.csv", content)

    assert parsed["total_rows"] == 1
    assert _ok(parsed)[0]["row_number"] == 4    # linha real do arquivo


# ── formatos, encoding e limites ─────────────────────────────────────────────
def test_csv_com_ponto_e_virgula():
    content = _csv_bytes([["Empresa;Site"], ["TOTVS;totvs.com.br"]])
    parsed = parse_spreadsheet("x.csv", content)
    assert _ok(parsed)[0]["lead"]["company_name"] == "TOTVS"


def test_csv_latin1():
    content = "Empresa,Site\nConstruções Acme,acme.com.br\n".encode("latin-1")
    parsed = parse_spreadsheet("x.csv", content)
    assert _ok(parsed)[0]["lead"]["company_name"] == "Construções Acme"


def test_extensao_nao_suportada():
    with pytest.raises(SpreadsheetError, match="Formato"):
        parse_spreadsheet("empresas.pdf", b"%PDF-1.4")


def test_xls_antigo_tem_mensagem_propria():
    with pytest.raises(SpreadsheetError, match="xlsx"):
        parse_spreadsheet("empresas.xls", b"\xd0\xcf\x11\xe0")


def test_arquivo_vazio():
    with pytest.raises(SpreadsheetError, match="vazio"):
        parse_spreadsheet("x.csv", b"")


def test_arquivo_grande_demais():
    with pytest.raises(SpreadsheetError, match="grande"):
        parse_spreadsheet("x.csv", b"a" * (15 * 1024 * 1024 + 1))


def test_corta_em_max_rows_e_sinaliza():
    rows = [["Empresa"]] + [[f"Empresa {i}"] for i in range(MAX_ROWS + 20)]
    parsed = parse_spreadsheet("x.csv", _csv_bytes(rows))
    assert parsed["truncated"] is True
    assert parsed["total_rows"] == MAX_ROWS


def test_planilha_so_com_cabecalho():
    with pytest.raises(SpreadsheetError, match="nenhuma linha de dados"):
        parse_spreadsheet("x.csv", _csv_bytes([["Empresa", "Site"]]))


# ── funcionários ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("250", {"exact": 250}),
    (39, {"exact": 39}),
    ("51-200", {"min": 51, "max": 200}),
    ("1.001 a 5.000", {"min": 1001, "max": 5000}),
    ("10000+", {"min": 10000}),
])
def test_parse_employee_count(raw, expected):
    result = parse_employee_count(raw)
    for key, value in expected.items():
        assert result[key] == value
    assert result["source"] == "import"


def test_parse_employee_count_vazio():
    assert parse_employee_count("") is None


# ── round-trip com o exportador ───────────────────────────────────────────────
def test_reimporta_o_proprio_csv_exportado():
    lead = Lead(
        id=1, raw_input_domain="acme.com.br", domain="acme.com.br",
        company_name="Acme Tecnologia", website="https://acme.com.br",
        sector="Tecnologia", location="São Paulo, SP",
        corporate_email="contato@acme.com.br", phone="+55 11 3000-0000",
        status="enriched",
    )
    parsed = parse_spreadsheet("leads.csv", build_export_csv([lead]))

    data = _ok(parsed)[0]["lead"]
    assert data["domain"] == "acme.com.br"
    assert data["company_name"] == "Acme Tecnologia"
    assert data["sector"] == "Tecnologia"


@pytest.mark.parametrize("builder,filename", [
    (build_template_csv, "modelo.csv"),
    (build_template_xlsx, "modelo.xlsx"),
])
def test_modelo_gerado_e_importavel(builder, filename):
    parsed = parse_spreadsheet(filename, builder())
    assert parsed["total_rows"] == 2
    assert all(r["status"] == "ok" for r in parsed["rows"])
