"""Testes do motor de lead scoring (função pura, sem I/O)."""
from services.lead_scorer import score_lead, SCORING_VERSION


def _base_lead(**overrides):
    lead = {
        "mx_provider": None,
        "dns_report": None,
        "hosting_provider": None,
        "employee_count": None,
        "linkedin_confidence": None,
        "linkedin_url": None,
        "description": None,
        "sector": None,
    }
    lead.update(overrides)
    return lead


def test_empty_lead_scores_zero_low_priority():
    result = score_lead(_base_lead())
    assert result["score"] == 0
    assert result["priority"] == "baixa"
    assert result["breakdown"] == []
    assert result["version"] == SCORING_VERSION


def test_modern_mail_provider_scores_10():
    result = score_lead(_base_lead(mx_provider="Google Workspace"))
    assert result["score"] == 10
    assert result["breakdown"][0]["criterion"] == "Email corporativo moderno"

    result = score_lead(_base_lead(mx_provider="Microsoft 365"))
    assert result["score"] == 10


def test_security_gateway_scores_12():
    result = score_lead(_base_lead(mx_provider="Proofpoint"))
    assert result["score"] == 12


def test_spf_dmarc_from_dns_report():
    lead = _base_lead(dns_report={"spf": "v=spf1 -all", "dmarc": "v=DMARC1; p=reject"})
    result = score_lead(lead)
    assert result["score"] == 8  # 3 + 5


def test_employee_bands():
    assert score_lead(_base_lead(employee_count={"min": 11, "max": 50}))["score"] == 8
    assert score_lead(_base_lead(employee_count={"min": 51, "max": 200}))["score"] == 15
    assert score_lead(_base_lead(employee_count={"exact": 5000}))["score"] == 20
    # 10 ou menos: sem pontos
    assert score_lead(_base_lead(employee_count={"exact": 5}))["score"] == 0


def test_decision_maker_signals():
    dms = [{
        "title_found": "CTO at Acme",
        "title_searched": "CTO",
        "phone": "+55 11 99999-0000",
        "probable_emails": [{"email": "cto@acme.com", "status": "valid"}],
    }]
    result = score_lead(_base_lead(), dms)
    # decisor (10) + email válido (15) + telefone (20) + C-level (5)
    assert result["score"] == 50
    assert result["priority"] == "alta"


def test_priority_bands():
    # 0-20 baixa
    assert score_lead(_base_lead(mx_provider="Google Workspace"))["priority"] == "baixa"
    # 21-40 média (10 + 5 + 8 = 23)
    lead = _base_lead(
        mx_provider="Google Workspace",
        linkedin_confidence="verified",
        employee_count={"min": 11},
    )
    assert score_lead(lead)["priority"] == "media"


def test_full_stack_lead_is_high_priority():
    lead = _base_lead(
        mx_provider="Microsoft 365",
        dns_report={"spf": "v=spf1", "dmarc": "v=DMARC1"},
        hosting_provider="Amazon AWS",
        employee_count={"min": 51, "max": 200},
        linkedin_confidence="verified",
        description="Empresa de tecnologia",
        sector="SaaS",
    )
    result = score_lead(lead)
    # 10+3+5+5+15+5+3 = 46
    assert result["score"] == 46
    assert result["priority"] == "alta"
    assert len(result["breakdown"]) == 7
    # Todo item do breakdown tem evidência
    assert all(item["evidence"] for item in result["breakdown"])
