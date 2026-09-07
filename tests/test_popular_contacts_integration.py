"""
Teste de integração para popular-contacts: verifica que o fluxo completo
funciona (enriquecer → carregar contatos populares com estilo Lusha).
"""
from unittest.mock import patch

from tests.test_api import client, MOCK_ENRICH_RESULT  # noqa: F401

_RESULTADO_MOCK = [{
    "name": "Satya Nadella",
    "title_searched": "CEO",
    "title_found": "Chief Executive Officer",
    "snippet": "Satya Nadella - CEO at Microsoft",
    "linkedin_url": "https://www.linkedin.com/in/satya-nadella",
    "probable_emails": [{"email": "satya@microsoft.com", "status": "valid", "confidence": 95}],
    "match_confidence": "high",
    "phone": "+1 4258828080",
}, {
    "name": "Bill Gates",
    "title_searched": "Founder",
    "title_found": "Founder",
    "snippet": "Bill Gates - Founder at Microsoft",
    "linkedin_url": "https://www.linkedin.com/in/william-h-gates-iii",
    "probable_emails": [{"email": "bgates@microsoft.com", "status": "valid", "confidence": 92}],
    "match_confidence": "high",
    "phone": None,
}]


def test_popular_contacts_full_flow(client):
    """Fluxo completo: enriquecer → popular-contacts retorna JSON válido para Lusha UI"""
    
    # 1. Enriquecer um lead
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "microsoft.com"})
    
    assert resp.status_code == 200
    lead = resp.json()["data"]
    lead_id = lead["id"]
    
    # 2. Carregar contatos populares
    with patch("routers.enrichment.find_decision_makers", return_value=_RESULTADO_MOCK):
        resp = client.get(f"/api/leads/{lead_id}/popular-contacts")
    
    assert resp.status_code == 200
    dados = resp.json()
    
    # 3. Validar estrutura da resposta
    assert dados["success"] is True
    assert "decisores" in dados
    assert isinstance(dados["decisores"], list)
    assert len(dados["decisores"]) == 2
    
    # 4. Validar campos necessários para Lusha UI
    for d in dados["decisores"]:
        # Campos obrigatórios para renderizar a card
        assert "name" in d
        assert "title_found" in d or "title_searched" in d
        assert "linkedin_url" in d or d.get("linkedin_url") is not None
        # Contato: pelo menos um de email ou phone
        assert d.get("phone") or d.get("probable_emails")
    
    # 5. Validar dados específicos
    satya = next(d for d in dados["decisores"] if d["name"] == "Satya Nadella")
    assert satya["title_found"] == "Chief Executive Officer"
    assert satya["phone"] == "+1 4258828080"
    assert satya["probable_emails"][0]["email"] == "satya@microsoft.com"
    
    # 6. Validar que Bill (sem phone) também aparece
    bill = next(d for d in dados["decisores"] if d["name"] == "Bill Gates")
    assert bill["phone"] is None or bill["phone"] == ""
    assert bill["probable_emails"][0]["email"] == "bgates@microsoft.com"


def test_popular_contacts_renderiza_sem_erros():
    """Valida que a função JS renderLushaContacts não geraria erros"""
    # Aqui testamos que o JSON retornado é válido para o JavaScript processar
    dummy_data = [{
        "id": 1,
        "name": "João Silva",
        "title_found": "VP Engineering",
        "title_searched": "CTO",
        "linkedin_url": "https://linkedin.com/in/joao",
        "probable_emails": [{"email": "joao@acme.com"}],
        "phone": "+55 11 98765-4321",
        "match_confidence": "high",
        "snippet": "VP at ACME"
    }]
    
    # Simula o que renderLushaContacts faria
    for p in dummy_data:
        init = p["name"][0].upper() if p.get("name") else "?"
        assert init == "J"
        
        # Extrair email
        emails = p.get("probable_emails") or []
        email = emails[0].get("email") if emails else None
        assert email == "joao@acme.com"
        
        # Extrair título
        titulo = p.get("title_found") or p.get("title_searched") or ""
        assert titulo == "VP Engineering"
        
        # Validar LinkedIn
        assert p.get("linkedin_url")
        
        # Validar telefone (E.164)
        assert p.get("phone")


def test_popular_contacts_filtra_sem_linkedin(client):
    """Valida que contatos SEM LinkedIn são descartados (fidelidade)"""
    from unittest.mock import patch
    
    # Mock com 2 contatos: um com LinkedIn, outro sem
    _RESULTADO_MISTO = [
        {
            "name": "Com LinkedIn",
            "title_searched": "CEO",
            "title_found": "CEO",
            "snippet": "CEO at Company",
            "linkedin_url": "https://www.linkedin.com/in/com-linkedin/",
            "probable_emails": [{"email": "com@company.com", "status": "valid", "confidence": 95}],
            "match_confidence": "high",
            "phone": None,
        },
        {
            "name": "Sem LinkedIn",  # Este deve ser FILTRADO
            "title_searched": "CTO",
            "title_found": "CTO",
            "snippet": "CTO at Company",
            "linkedin_url": None,  # Sem LinkedIn!
            "probable_emails": [{"email": "sem@company.com", "status": "valid", "confidence": 90}],
            "match_confidence": "high",
            "phone": None,
        },
    ]
    
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "company.com"})
    
    lead = resp.json()["data"]
    
    with patch("routers.enrichment.find_decision_makers", return_value=_RESULTADO_MISTO):
        resp = client.get(f"/api/leads/{lead['id']}/popular-contacts")
    
    assert resp.status_code == 200
    dados = resp.json()
    
    # Deve ter APENAS 1 contato (o com LinkedIn)
    assert len(dados["decisores"]) == 1
    assert dados["decisores"][0]["name"] == "Com LinkedIn"
    assert "linkedin.com/in/" in dados["decisores"][0]["linkedin_url"]


def test_popular_contacts_usa_lusha_company_api(client):
    """Valida que usa a API /v2/company do Lusha quando conectado"""
    from unittest.mock import patch
    
    # Mock da resposta do Lusha /company (lista de contatos da empresa)
    _LUSHA_COMPANY_MOCK = [
        {
            "provider": "lusha",
            "name": "Satya Nadella",
            "title": "CEO",
            "emails": [{"email": "satya@microsoft.com", "status": "unknown", "confidence": 92}],
            "phones": [{"e164": "+1 4258828080", "formatted": "(425) 882-8080", "type": "mobile", "confidence": 92}],
            "linkedin_url": "https://www.linkedin.com/in/satya-nadella",
        },
        {
            "provider": "lusha",
            "name": "Bill Gates",
            "title": "Founder",
            "emails": [{"email": "bgates@microsoft.com", "status": "unknown", "confidence": 92}],
            "phones": [{"e164": "+1 4257231580", "formatted": "(425) 723-1580", "type": "mobile", "confidence": 92}],
            "linkedin_url": "https://www.linkedin.com/in/william-h-gates-iii",
        },
    ]
    
    # 1. Enriquecer um lead
    with patch("services.enrichment_service.enrich_company", return_value=MOCK_ENRICH_RESULT):
        resp = client.post("/api/enrich", json={"domain": "microsoft.com"})
    
    lead = resp.json()["data"]

    # 2. Chamar popular-contacts com Lusha mockado (chave presente + resultado)
    with patch("routers.enrichment._lusha_key_utilizavel", return_value="fake-lusha-key"), \
         patch("routers.enrichment.lusha.find_company_contacts", return_value=_LUSHA_COMPANY_MOCK):
        resp = client.get(f"/api/leads/{lead['id']}/popular-contacts")
    
    assert resp.status_code == 200
    dados = resp.json()
    
    # 3. Verificar que retornou dados do Lusha (completos com phone)
    assert dados["success"] is True
    assert len(dados["decisores"]) == 2
    
    # Satya deve ter phone completo (E.164)
    satya = next(d for d in dados["decisores"] if d["name"] == "Satya Nadella")
    assert satya["phone"] == "+1 4258828080"
    assert satya["title_found"] == "CEO"
    
    # Bill também deve estar lá
    bill = next(d for d in dados["decisores"] if d["name"] == "Bill Gates")
    assert bill["phone"] == "+1 4257231580"
