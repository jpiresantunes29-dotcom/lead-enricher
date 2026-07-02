import pytest
from services.phone_normalizer import extract_and_normalize_phones, pick_best_phone


def test_extract_mobile_br():
    phones = extract_and_normalize_phones("Ligue: (11) 98888-7777", "BR")
    assert len(phones) == 1
    assert phones[0]["type"] == "mobile"
    assert phones[0]["country"] == "BR"
    assert phones[0]["formatted"].startswith("+55")


def test_extract_fixed_br():
    phones = extract_and_normalize_phones("Tel: (11) 4002-8922", "BR")
    assert len(phones) == 1
    assert phones[0]["type"] in ("fixed_line", "fixed_or_mobile")


def test_extract_empty_text():
    assert extract_and_normalize_phones("") == []
    assert extract_and_normalize_phones(None) == []


def test_no_duplicates():
    text = "(11) 98888-7777 e também 11 98888-7777"
    phones = extract_and_normalize_phones(text, "BR")
    assert len(phones) == 1


def test_pick_best_prefers_tel_link():
    html = '<a href="tel:+5511988887777">Fale conosco</a>'
    result = pick_best_phone("algum texto sem telefone", html=html, default_region="BR")
    assert result is not None
    assert "5511" in result.replace(" ", "")


def test_pick_best_prefers_mobile():
    text = "Fixo: (11) 3333-4444. Celular: (11) 99999-8888"
    result = pick_best_phone(text, default_region="BR")
    assert result is not None
    # número móvel tem 9 dígitos após DDD
    assert "9 9999" in result or "99999" in result.replace(" ", "")


def test_pick_best_no_phone_returns_none():
    assert pick_best_phone("texto sem número nenhum") is None
