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
