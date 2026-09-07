"""
Verificação premium de e-mail (Hunter e afins): cache, teto por lote e teto
diário.

Até esta correção, `HUNTER_API_KEY` configurada não tinha efeito nenhum — nada
no fluxo real de decisores ou da extensão chamava o provedor (`verify_emails`,
a única função que chamava, nunca era usada). Aqui se testa o novo caminho
(`verify_emails_effective`) e os freios de custo que o acompanham: sem eles,
uma chave configurada por engano em um lote grande pagaria por cada e-mail
"unknown", sem limite.
"""
from unittest.mock import patch

import pytest

from tests.test_api import clean_db, _Session  # noqa: F401

from models.database import PremiumEmailCheck, ProviderCall
from services import email_verifier


@pytest.fixture(autouse=True)
def sem_smtp(monkeypatch):
    """
    Sondagem SMTP desligada — o cenário que mais importa (Vercel bloqueia a
    porta 25), e o único em que o resultado grátis é sempre "unknown" sem
    nenhuma chamada de rede real acontecer no teste.
    """
    monkeypatch.setattr(email_verifier, "smtp_probe_available", lambda: False)


@pytest.fixture(autouse=True)
def sem_hunter_configurado_por_padrao(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)


def _com_hunter(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "chave-de-teste")


def test_sem_provedor_configurado_nao_chama_nada(clean_db):
    """Sem HUNTER_API_KEY, o comportamento é idêntico ao de antes: tudo "unknown"."""
    db = _Session()
    try:
        resultado = email_verifier.verify_emails_effective(["a@acme.com"], db=db)
        assert resultado == [{"email": "a@acme.com", "status": "unknown"}]
        assert db.query(ProviderCall).count() == 0
    finally:
        db.close()


def test_provedor_configurado_upgrade_o_unknown(monkeypatch, clean_db):
    _com_hunter(monkeypatch)
    db = _Session()
    try:
        with patch("services.providers.premium_verify_email", return_value="valid") as mock:
            resultado = email_verifier.verify_emails_effective(["a@acme.com"], db=db)
        assert resultado == [{"email": "a@acme.com", "status": "valid"}]
        mock.assert_called_once_with("a@acme.com")
        assert db.query(ProviderCall).filter(ProviderCall.provider == "premium_email").count() == 1
    finally:
        db.close()


def test_sem_db_nao_tenta_premium(monkeypatch, clean_db):
    """Sem sessão, não há onde guardar cache nem contar o teto — não arrisca."""
    _com_hunter(monkeypatch)
    with patch("services.providers.premium_verify_email") as mock:
        resultado = email_verifier.verify_emails_effective(["a@acme.com"], db=None)
    assert resultado == [{"email": "a@acme.com", "status": "unknown"}]
    mock.assert_not_called()


def test_segunda_consulta_do_mesmo_email_usa_cache_sem_chamar_de_novo(monkeypatch, clean_db):
    _com_hunter(monkeypatch)
    db = _Session()
    try:
        with patch("services.providers.premium_verify_email", return_value="valid") as mock:
            email_verifier.verify_emails_effective(["a@acme.com"], db=db)
            email_verifier.verify_emails_effective(["a@acme.com"], db=db)
        assert mock.call_count == 1
        assert db.query(ProviderCall).count() == 1
        assert db.query(PremiumEmailCheck).filter(PremiumEmailCheck.email == "a@acme.com").one().status == "valid"
    finally:
        db.close()


def test_teto_diario_impede_chamada_nova_mas_mantem_o_que_ja_sabia(monkeypatch, clean_db):
    _com_hunter(monkeypatch)
    monkeypatch.setattr(email_verifier, "PREMIUM_DAILY_LIMIT", 1)
    db = _Session()
    try:
        with patch("services.providers.premium_verify_email", return_value="valid") as mock:
            email_verifier.verify_emails_effective(["a@acme.com"], db=db)
            # segundo e-mail, dentro do MESMO lote: o teto diário já foi
            # atingido pela primeira chamada, e este e-mail nunca foi visto.
            resultado = email_verifier.verify_emails_effective(["b@acme.com"], db=db)
        assert mock.call_count == 1
        assert resultado == [{"email": "b@acme.com", "status": "unknown"}]
    finally:
        db.close()


def test_teto_por_lote_limita_chamadas_dentro_de_uma_unica_busca(monkeypatch, clean_db):
    """
    Um lote de 10 candidatos "unknown" não pode virar 10 chamadas pagas de uma
    vez — é exatamente o cenário de um lote grande de domínios que a auditoria
    apontou como risco de custo sem controle.
    """
    _com_hunter(monkeypatch)
    monkeypatch.setattr(email_verifier, "PREMIUM_MAX_PER_BATCH", 3)
    db = _Session()
    try:
        emails = [f"pessoa{i}@acme.com" for i in range(10)]
        with patch("services.providers.premium_verify_email", return_value="valid") as mock:
            email_verifier.verify_emails_effective(emails, db=db)
        assert mock.call_count == 3
    finally:
        db.close()


def test_resposta_sem_status_premium_preserva_unknown_e_nao_grava_cache(monkeypatch, clean_db):
    """Provedor fora do ar (`None`): nem sobe o "unknown" para outra coisa, nem cria cache falso."""
    _com_hunter(monkeypatch)
    db = _Session()
    try:
        with patch("services.providers.premium_verify_email", return_value=None):
            resultado = email_verifier.verify_emails_effective(["a@acme.com"], db=db)
        assert resultado == [{"email": "a@acme.com", "status": "unknown"}]
        assert db.query(PremiumEmailCheck).count() == 0
        # A tentativa em si fica registrada (é o que "hit=False" documenta).
        chamada = db.query(ProviderCall).one()
        assert chamada.hit is False
    finally:
        db.close()


def test_status_ja_confiavel_do_smtp_nao_aciona_premium(monkeypatch, clean_db):
    """"valid"/"invalid" já são resposta confiável — gastar dinheiro aqui seria à toa."""
    _com_hunter(monkeypatch)
    monkeypatch.setattr(email_verifier, "smtp_probe_available", lambda: True)
    db = _Session()
    try:
        with patch("services.email_verifier.verify_batch",
                   return_value=[{"email": "a@acme.com", "status": "invalid"}]):
            with patch("services.providers.premium_verify_email") as mock:
                resultado = email_verifier.verify_emails_effective(["a@acme.com"], db=db)
        mock.assert_not_called()
        assert resultado == [{"email": "a@acme.com", "status": "invalid"}]
    finally:
        db.close()
