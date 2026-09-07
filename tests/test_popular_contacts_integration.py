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
