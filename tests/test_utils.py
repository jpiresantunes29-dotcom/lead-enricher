import pytest
from services._utils import normalize_domain, tld_to_region


@pytest.mark.parametrize("input_val,expected", [
    ("https://www.nubank.com.br/conta", "nubank.com.br"),
    ("http://nubank.com.br", "nubank.com.br"),
    ("www.nubank.com.br", "nubank.com.br"),
    ("nubank.com.br", "nubank.com.br"),
    ("NUBANK.COM.BR", "nubank.com.br"),
    ("nubank.com.br?ref=test", "nubank.com.br"),
    ("  nubank.com.br  ", "nubank.com.br"),
])
def test_normalize_domain(input_val, expected):
    assert normalize_domain(input_val) == expected


@pytest.mark.parametrize("domain,expected_region", [
    ("empresa.com.br", "BR"),
    ("empresa.pt", "PT"),
    ("empresa.com.mx", "MX"),
    ("empresa.com.us", "US"),
    ("empresa.com", "BR"),  # TLD desconhecido → default BR
])
def test_tld_to_region(domain, expected_region):
    assert tld_to_region(domain) == expected_region


# ── contagem exata de membros associados (página pública do LinkedIn) ──────────
from services.employee_count import _exact_from_company_page, normalize_employee_count


def test_exact_from_face_pile_pt():
    html = ('<div><p class="face-pile__text font-sans text-sm link-styled">'
            ' Visualizar todos os 12.701 funcionários </p></div>'
            '<dd>5.001-10.000 funcionários</dd>')
    result = _exact_from_company_page(html)
    assert result is not None
    assert result["exact"] == 12701          # exato, não a faixa
    assert result["source"] == "linkedin_people"
    assert result["band"] == "10000+"


def test_exact_from_text_pattern_en():
    html = "<a href='/company/acme/people/'>View all 1,524 employees</a>"
    result = _exact_from_company_page(html)
    assert result is not None
    assert result["exact"] == 1524


def test_exact_from_staffcount_json():
    html = '<script>{"staffCount":684,"followerCount":301928}</script>'
    result = _exact_from_company_page(html)
    assert result is not None
    assert result["exact"] == 684
    assert result["band"] == "501-1000"


def test_exact_none_when_only_band():
    html = "<dd>5.001-10.000 funcionários</dd>"
    assert _exact_from_company_page(html) is None
