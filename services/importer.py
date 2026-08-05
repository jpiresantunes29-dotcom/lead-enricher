"""
Importação de planilhas de empresas (XLSX/CSV).

Recebe o arquivo cru, detecta o cabeçalho, mapeia as colunas para os campos do
Lead por aliases (PT/EN) e devolve linhas normalizadas + diagnóstico por linha.
Nada aqui toca no banco nem faz chamada externa — o router decide o que salvar.

Formato aceito: o mesmo que o exportador gera (services/exporter.py), para que
o usuário possa exportar, editar e reimportar sem retrabalho.
"""
import csv
import io
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook

from ._utils import normalize_domain

# Limites — protegem a função serverless (60 s na Vercel) e a memória.
MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_ROWS = 500                     # linhas de dados consideradas
HEADER_SCAN_ROWS = 10              # até onde procuramos a linha de cabeçalho

XLSX_EXTENSIONS = (".xlsx", ".xlsm")
CSV_EXTENSIONS = (".csv", ".tsv", ".txt")

# Campos que aceitamos da planilha → sempre colunas reais do model Lead.
# A ordem define a ordem das colunas no modelo baixável.
FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "domain": ("dominio", "domain", "site", "website", "url", "pagina", "web"),
    "company_name": (
        "empresa", "nome", "nome da empresa", "razao social", "razaosocial",
        "company", "company name", "cliente", "conta",
    ),
    "sector": ("setor", "segmento", "industria", "ramo", "sector", "industry"),
    "location": ("localizacao", "local", "cidade", "endereco", "location", "city"),
    "description": ("descricao", "sobre", "description", "about", "observacao", "obs"),
    "corporate_email": ("email", "e mail", "email corporativo", "contato", "mail"),
    "phone": ("telefone", "fone", "celular", "whatsapp", "phone", "tel"),
    "linkedin_url": ("linkedin", "linkedin url", "linkedin da empresa", "perfil linkedin"),
    "employee_count": ("funcionarios", "colaboradores", "porte", "employees", "headcount"),
}

# Campos livres copiados como texto (domain/employee_count têm tratamento próprio)
TEXT_FIELDS = (
    "company_name", "sector", "location", "description",
    "corporate_email", "phone", "linkedin_url",
)

# Limites de tamanho das colunas VARCHAR correspondentes em models.database.Lead
_MAX_LEN = {
    "company_name": 255,
    "sector": 255,
    "location": 255,
    "corporate_email": 255,
    "phone": 100,
    "linkedin_url": 500,
    "description": 5000,
}

_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)+$")
_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")

TEMPLATE_HEADERS = [
    ("Domínio", "domain"),
    ("Empresa", "company_name"),
    ("Setor", "sector"),
    ("Localização", "location"),
    ("Email Corporativo", "corporate_email"),
    ("Telefone", "phone"),
    ("LinkedIn", "linkedin_url"),
    ("Funcionários", "employee_count"),
    ("Descrição", "description"),
]

TEMPLATE_SAMPLE = [
    ["nubank.com.br", "Nubank", "Serviços financeiros", "São Paulo, SP",
     "contato@nubank.com.br", "+55 11 4000-0000",
     "https://linkedin.com/company/nubank", "5001-10000", "Banco digital"],
    ["totvs.com.br", "TOTVS", "Tecnologia", "São Paulo, SP", "", "", "", "1001-5000", ""],
]


class SpreadsheetError(Exception):
    """Erro de parsing tratável — o router converte em HTTP 400/422."""


def _strip_accents(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(c)
    )


def _normalize_header(value: Any) -> str:
    """'Razão Social ' → 'razao social' (para casar com os aliases)."""
    text = _strip_accents(str(value or "")).lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


# ── leitura bruta ─────────────────────────────────────────────────────────────

def _read_xlsx(content: bytes) -> List[List[Any]]:
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        raise SpreadsheetError(f"Não consegui ler o arquivo Excel: {e}")
    try:
        ws = wb.active
        rows = []
        # +1 pela linha de cabeçalho; a fatia final acontece depois da detecção
        for row in ws.iter_rows(values_only=True, max_row=MAX_ROWS + HEADER_SCAN_ROWS + 1):
            rows.append(list(row))
        return rows
    finally:
        wb.close()


def _read_csv(content: bytes) -> List[List[Any]]:
    text = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SpreadsheetError("Não consegui identificar a codificação do arquivo.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        # Heurística simples quando o Sniffer desiste (1 coluna só, por exemplo)
        delimiter = max(",;\t|", key=sample.count) if sample else ","
        if sample.count(delimiter) == 0:
            delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [row for _, row in zip(range(MAX_ROWS + HEADER_SCAN_ROWS + 1), reader)]


def _read_raw(filename: str, content: bytes) -> List[List[Any]]:
    if len(content) > MAX_FILE_BYTES:
        raise SpreadsheetError(
            f"Arquivo muito grande (máx. {MAX_FILE_BYTES // (1024 * 1024)} MB)."
        )
    if not content:
        raise SpreadsheetError("Arquivo vazio.")
    name = (filename or "").lower()
    if name.endswith(XLSX_EXTENSIONS):
        return _read_xlsx(content)
    if name.endswith(CSV_EXTENSIONS):
        return _read_csv(content)
    if name.endswith(".xls"):
        raise SpreadsheetError(
            "Formato .xls (Excel 97-2003) não suportado. Salve como .xlsx ou .csv."
        )
    raise SpreadsheetError("Formato não suportado. Envie um arquivo .xlsx ou .csv.")


# ── cabeçalho e mapeamento ────────────────────────────────────────────────────

def _score_header_row(row: List[Any]) -> int:
    """Quantos campos conhecidos essa linha reconheceria como cabeçalho."""
    mapping, _ = _map_columns(row)
    return len(mapping)


def _find_header_row(rows: List[List[Any]]) -> int:
    """
    Escolhe a linha de cabeçalho: a que mapeia mais campos conhecidos nas
    primeiras HEADER_SCAN_ROWS linhas (planilhas reais costumam ter título,
    logo e linhas em branco antes da tabela).
    """
    best_idx, best_score = -1, 0
    for idx, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if not any(_cell_text(c) for c in row):
            continue
        score = _score_header_row(row)
        if score > best_score:
            best_idx, best_score = idx, score
    if best_idx >= 0:
        return best_idx
    # Nenhum cabeçalho reconhecido: assume a primeira linha não vazia
    for idx, row in enumerate(rows):
        if any(_cell_text(c) for c in row):
            return idx
    raise SpreadsheetError("A planilha não tem nenhuma linha preenchida.")


def _map_columns(header_row: List[Any]) -> Tuple[Dict[str, int], List[str]]:
    """
    Casa cada coluna do cabeçalho com um campo do Lead.
    Retorna ({campo: índice da coluna}, [rótulos originais do cabeçalho]).
    """
    labels = [_cell_text(c) for c in header_row]
    mapping: Dict[str, int] = {}
    for idx, label in enumerate(labels):
        norm = _normalize_header(label)
        if not norm:
            continue
        for field, aliases in FIELD_ALIASES.items():
            if field in mapping:
                continue
            if norm in aliases or any(
                norm.startswith(a + " ") or norm.endswith(" " + a) for a in aliases
            ):
                mapping[field] = idx
                break
    return mapping, labels


def _extract_domain(raw_values: Dict[str, str]) -> str:
    """
    Descobre o domínio a partir da coluna de domínio/site ou, em último caso,
    do e-mail corporativo (empresa@dominio.com.br).
    """
    candidate = raw_values.get("domain", "")
    if candidate:
        domain = normalize_domain(candidate)
        if _DOMAIN_RE.match(domain):
            return domain
    email = raw_values.get("corporate_email", "")
    match = _EMAIL_RE.search(email)
    if match:
        domain = normalize_domain(match.group(1))
        if _DOMAIN_RE.match(domain):
            return domain
    return ""


def _parse_employee_count(value: str) -> Optional[dict]:
    """
    Aceita '250', '51-200', '1.001+', '5001 a 10000' e devolve o mesmo shape
    consolidado que o enricher grava ({raw, min, max, band, exact, source}).
    """
    raw = (value or "").strip()
    if not raw:
        return None
    digits = [int(n.replace(".", "").replace(",", "")) for n in re.findall(r"\d[\d.,]*", raw)]
    result: Dict[str, Any] = {
        "raw": raw, "min": None, "max": None, "band": None,
        "exact": None, "source": "import",
    }
    if len(digits) >= 2:
        result["min"], result["max"] = digits[0], digits[1]
        result["band"] = f"{digits[0]}-{digits[1]}"
    elif len(digits) == 1:
        if "+" in raw or "mais" in raw.lower():
            result["min"] = digits[0]
            result["band"] = f"{digits[0]}+"
        else:
            result["exact"] = digits[0]
    else:
        result["band"] = raw
    return result


def parse_spreadsheet(filename: str, content: bytes) -> Dict[str, Any]:
    """
    Lê a planilha e devolve:
      {columns, mapping, unmapped, total_rows, truncated, rows: [...]}

    Cada item de `rows` traz:
      row_number  — linha real na planilha (1-based), para o usuário achar o erro
      status      — 'ok' | 'invalid' | 'duplicate_file'
      reason      — motivo quando status != 'ok'
      data        — {domain, company_name, ...} pronto para virar Lead
    """
    raw_rows = _read_raw(filename, content)
    header_idx = _find_header_row(raw_rows)
    mapping, labels = _map_columns(raw_rows[header_idx])

    if "domain" not in mapping and "corporate_email" not in mapping:
        raise SpreadsheetError(
            "Não encontrei uma coluna de domínio/site. Renomeie a coluna para "
            "\"Domínio\" (ou \"Site\") — ou baixe o modelo de planilha."
        )

    data_rows = raw_rows[header_idx + 1:]
    truncated = len(data_rows) > MAX_ROWS
    data_rows = data_rows[:MAX_ROWS]

    seen: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []

    for offset, raw in enumerate(data_rows):
        row_number = header_idx + 2 + offset  # 1-based, pulando o cabeçalho
        values = {
            field: _cell_text(raw[idx]) if idx < len(raw) else ""
            for field, idx in mapping.items()
        }
        if not any(values.values()):
            continue  # linha totalmente vazia — ignora em silêncio

        domain = _extract_domain(values)
        if not domain:
            rows.append({
                "row_number": row_number,
                "status": "invalid",
                "reason": "Sem domínio válido nesta linha.",
                "data": {"domain": "", "company_name": values.get("company_name", "")},
            })
            continue

        data: Dict[str, Any] = {"domain": domain, "raw_input_domain": domain}
        for field in TEXT_FIELDS:
            text = values.get(field, "")
            if text:
                data[field] = text[:_MAX_LEN.get(field, 255)]
        employees = _parse_employee_count(values.get("employee_count", ""))
        if employees:
            data["employee_count"] = employees
        data["website"] = f"https://{domain}"

        if domain in seen:
            rows.append({
                "row_number": row_number,
                "status": "duplicate_file",
                "reason": f"Domínio repetido (já aparece na linha {seen[domain]}).",
                "data": data,
            })
            continue

        seen[domain] = row_number
        rows.append({
            "row_number": row_number,
            "status": "ok",
            "reason": None,
            "data": data,
        })

    if not rows:
        raise SpreadsheetError("A planilha não tem nenhuma linha de dados abaixo do cabeçalho.")

    mapped_indexes = set(mapping.values())
    return {
        "columns": labels,
        "mapping": {field: labels[idx] for field, idx in mapping.items()},
        "unmapped": [
            label for idx, label in enumerate(labels)
            if label and idx not in mapped_indexes
        ],
        "total_rows": len(rows),
        "truncated": truncated,
        "rows": rows,
    }


# ── modelo de planilha para download ─────────────────────────────────────────

def build_template_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Empresas"
    from openpyxl.styles import Alignment, Font, PatternFill

    for col, (label, _) in enumerate(TEMPLATE_HEADERS, start=1):
        cell = ws.cell(row=1, column=col, value=label)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1D4ED8")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row_idx, sample in enumerate(TEMPLATE_SAMPLE, start=2):
        for col, value in enumerate(sample, start=1):
            ws.cell(row=row_idx, column=col, value=value)
    for col, width in enumerate([24, 26, 22, 22, 28, 20, 38, 16, 40], start=1):
        ws.column_dimensions[chr(64 + col)].width = width
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def build_template_csv() -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel")
    writer.writerow([label for label, _ in TEMPLATE_HEADERS])
    for sample in TEMPLATE_SAMPLE:
        writer.writerow(sample)
    return buf.getvalue().encode("utf-8-sig")
