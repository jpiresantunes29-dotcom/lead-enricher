"""
Testes do parser de planilhas (services/importer.py).

Cobrem os casos que quebram importação no mundo real: cabeçalho com acento,
CSV com ponto e vírgula, linhas de título antes da tabela, domínio sujo
(https://www.x.com/contato), duplicados e linhas sem domínio.
"""
import csv
import io

import pytest
from openpyxl import Workbook

from services.exporter import build_csv as build_export_csv
from services.importer import (
    MAX_ROWS, SpreadsheetError, _parse_employee_count,
    build_template_csv, build_template_xlsx, parse_spreadsheet,
)
from models.database import Lead


def _csv_bytes(rows, delimiter=",", encoding="utf-8-sig"):
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode(encoding)


def _xlsx_bytes(rows):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ok_rows(parsed):
    return [r for r in parsed["rows"] if r["status"] == "ok"]


# ── mapeamento de colunas ─────────────────────────────────────────────────────
def test_mapeia_cabecalho_em_portugues_com_acento():
    content = _csv_bytes([
        ["Domínio", "Razão Social", "Setor", "Telefone"],
        ["nubank.com.br", "Nu Pagamentos", "Fintech", "11 4000-0000"],
    ])
    parsed = parse_spreadsheet("empresas.csv", content)

    assert parsed["mapping"]["domain"] == "Domínio"
    assert parsed["mapping"]["company_name"] == "Razão Social"
    data = _ok_rows(parsed)[0]["data"]
    assert data["domain"] == "nubank.com.br"
    assert data["company_name"] == "Nu Pagamentos"
    assert data["phone"] == "11 4000-0000"


def test_mapeia_cabecalho_em_ingles():
    content = _csv_bytes([
        ["Website", "Company Name", "Industry"],
        ["https://stripe.com", "Stripe", "Payments"],
    ])
    parsed = parse_spreadsheet("companies.csv", content)

    assert set(parsed["mapping"]) >= {"domain", "company_name", "sector"}
    assert _ok_rows(parsed)[0]["data"]["domain"] == "stripe.com"


def test_colunas_desconhecidas_viram_unmapped():
    content = _csv_bytes([
        ["Site", "Empresa", "Vendedor responsável", "CNPJ"],
        ["totvs.com.br", "TOTVS", "Ana", "53.113.791/0001-22"],
    ])
    parsed = parse_spreadsheet("x.csv", content)

    assert parsed["unmapped"] == ["Vendedor responsável", "CNPJ"]
    assert "cnpj" not in _ok_rows(parsed)[0]["data"]


def test_planilha_sem_coluna_de_dominio_e_rejeitada():
    content = _csv_bytes([["Empresa", "Setor"], ["TOTVS", "Tecnologia"]])
    with pytest.raises(SpreadsheetError, match="domínio"):
        parse_spreadsheet("x.csv", content)


def test_ignora_linhas_de_titulo_antes_do_cabecalho():
    content = _csv_bytes([
        ["Lista de prospects — Q3"],
        [],
        ["Domínio", "Empresa"],
        ["gerdau.com.br", "Gerdau"],
    ])
    parsed = parse_spreadsheet("x.csv", content)

    assert parsed["total_rows"] == 1
    row = _ok_rows(parsed)[0]
    assert row["data"]["domain"] == "gerdau.com.br"
    assert row["row_number"] == 4  # linha real na planilha, para o usuário achar


# ── normalização de domínio ───────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("https://www.rdstation.com/contato", "rdstation.com"),
    ("WWW.Embraer.com.br", "embraer.com.br"),
    ("  totvs.com.br  ", "totvs.com.br"),
    ("http://gerdau.com.br?utm=x", "gerdau.com.br"),
])
def test_normaliza_dominio_sujo(raw, expected):
    content = _csv_bytes([["Site"], [raw]])
    parsed = parse_spreadsheet("x.csv", content)
    assert _ok_rows(parsed)[0]["data"]["domain"] == expected


def test_deriva_dominio_do_email_quando_nao_ha_site():
    content = _csv_bytes([
        ["Empresa", "E-mail"],
        ["Acme", "comercial@acme.com.br"],
    ])
    parsed = parse_spreadsheet("x.csv", content)
    assert _ok_rows(parsed)[0]["data"]["domain"] == "acme.com.br"


def test_linha_sem_dominio_valido_e_marcada_como_invalida():
    content = _csv_bytes([
        ["Domínio", "Empresa"],
        ["", "Sem site"],
        ["não é domínio", "Lixo"],
        ["ok.com.br", "Boa"],
    ])
    parsed = parse_spreadsheet("x.csv", content)

    status = [r["status"] for r in parsed["rows"]]
    assert status == ["invalid", "invalid", "ok"]
    assert parsed["rows"][0]["reason"]


def test_dominio_repetido_no_arquivo_e_marcado_como_duplicado():
    content = _csv_bytes([
        ["Domínio"],
        ["acme.com.br"],
        ["https://www.acme.com.br/"],
    ])
    parsed = parse_spreadsheet("x.csv", content)

    assert [r["status"] for r in parsed["rows"]] == ["ok", "duplicate_file"]
    assert "linha 2" in parsed["rows"][1]["reason"]


# ── formatos e limites ────────────────────────────────────────────────────────
def test_csv_com_ponto_e_virgula():
    # escrito como coluna única para o writer não escapar o ";" do separador
    content = _csv_bytes([["Domínio;Empresa"], ["totvs.com.br;TOTVS"]])
    parsed = parse_spreadsheet("x.csv", content)
    assert _ok_rows(parsed)[0]["data"]["company_name"] == "TOTVS"


def test_csv_latin1():
    content = "Domínio,Empresa\nacme.com.br,Construções Acme\n".encode("latin-1")
    parsed = parse_spreadsheet("x.csv", content)
    assert _ok_rows(parsed)[0]["data"]["company_name"] == "Construções Acme"


def test_xlsx_e_lido():
    content = _xlsx_bytes([
        ["Domínio", "Empresa", "Funcionários"],
        ["nubank.com.br", "Nubank", 5000],
    ])
    parsed = parse_spreadsheet("empresas.xlsx", content)
    data = _ok_rows(parsed)[0]["data"]
    assert data["company_name"] == "Nubank"
    assert data["employee_count"]["exact"] == 5000


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
        parse_spreadsheet("x.csv", b"a" * (5 * 1024 * 1024 + 1))


def test_corta_em_max_rows_e_sinaliza():
    rows = [["Domínio"]] + [[f"empresa{i}.com.br"] for i in range(MAX_ROWS + 20)]
    parsed = parse_spreadsheet("x.csv", _csv_bytes(rows))

    assert parsed["truncated"] is True
    assert parsed["total_rows"] == MAX_ROWS


def test_planilha_so_com_cabecalho():
    with pytest.raises(SpreadsheetError, match="nenhuma linha de dados"):
        parse_spreadsheet("x.csv", _csv_bytes([["Domínio", "Empresa"]]))


# ── employee_count ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("250", {"exact": 250}),
    ("51-200", {"min": 51, "max": 200}),
    ("1.001 a 5.000", {"min": 1001, "max": 5000}),
    ("10000+", {"min": 10000}),
])
def test_parse_employee_count(raw, expected):
    result = _parse_employee_count(raw)
    for key, value in expected.items():
        assert result[key] == value
    assert result["raw"] == raw
    assert result["source"] == "import"


def test_parse_employee_count_vazio():
    assert _parse_employee_count("") is None


# ── round-trip com o exportador ───────────────────────────────────────────────
def test_reimporta_o_proprio_csv_exportado():
    """Exportar → editar no Excel → reimportar tem que funcionar sem retrabalho."""
    lead = Lead(
        id=1,
        raw_input_domain="acme.com.br",
        domain="acme.com.br",
        company_name="Acme Tecnologia",
        website="https://acme.com.br",
        sector="Tecnologia",
        location="São Paulo, SP",
        corporate_email="contato@acme.com.br",
        phone="+55 11 3000-0000",
        status="enriched",
    )
    parsed = parse_spreadsheet("leads_enriquecidos.csv", build_export_csv([lead]))

    data = _ok_rows(parsed)[0]["data"]
    assert data["domain"] == "acme.com.br"
    assert data["company_name"] == "Acme Tecnologia"
    assert data["sector"] == "Tecnologia"
    assert data["corporate_email"] == "contato@acme.com.br"


# ── modelo para download ──────────────────────────────────────────────────────
@pytest.mark.parametrize("builder,filename", [
    (build_template_csv, "modelo.csv"),
    (build_template_xlsx, "modelo.xlsx"),
])
def test_modelo_gerado_e_importavel(builder, filename):
    parsed = parse_spreadsheet(filename, builder())
    assert parsed["total_rows"] == 2
    assert all(r["status"] == "ok" for r in parsed["rows"])
