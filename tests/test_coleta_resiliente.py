"""
O que a ficha faz quando a coleta vai mal.

Três situações que produziam o mesmo resultado na tela — ficha vazia, sem
explicação — e que aqui passam a ser distinguidas:

  1. o DNS do sistema não responde (resolver de VPN morta, container sem
     /etc/resolv.conf): a consulta é repetida em um resolver público;
  2. uma coleta que voltou vazia sobre uma ficha que já tinha dados: os dados
     antigos ficam, e o status não é rebaixado;
  3. o site recusou o robô (403 / desafio anti-bot): a ficha registra o
     motivo, em vez de deixar os campos vazios sem explicação.

O caso 2 é o que estragou a ficha da Magazine Luiza em produção: coleta boa às
04:28:29, refresh com DNS mudo às 04:28:51, e a ficha terminou pior do que
começou.
"""
from types import SimpleNamespace
from unittest.mock import patch

import dns.resolver
import pytest

from services import dns_resolver
from services.enrichment_service import merge_collected


# ══════════════════════════════════════════════════════════════════
# 1. Resolver DNS com fallback
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _resolver_limpo():
    dns_resolver.reset_state()
    yield
    dns_resolver.reset_state()


class _ResolverFalso:
    def __init__(self, resposta=None, erro=None):
        self.resposta, self.erro = resposta, erro
        self.chamadas = []
        self.nameservers = ["fake"]

    def resolve(self, name, rtype, lifetime=None):
        self.chamadas.append((name, rtype))
        if self.erro is not None:
            raise self.erro
        return self.resposta


def _patch_resolvers(monkeypatch, primario, fallback):
    monkeypatch.setattr(dns_resolver, "_primary", lambda: primario)
    monkeypatch.setattr(dns_resolver, "_fallback", lambda: fallback)


def test_timeout_do_sistema_cai_no_resolver_publico(monkeypatch):
    primario = _ResolverFalso(erro=dns.resolver.LifetimeTimeout(timeout=5.0, errors=[]))
    fallback = _ResolverFalso(resposta=["MX!"])
    _patch_resolvers(monkeypatch, primario, fallback)

    assert dns_resolver.resolve("exemplo.com.br", "MX") == ["MX!"]
    assert fallback.chamadas == [("exemplo.com.br", "MX")]


def test_dominio_inexistente_nao_repete_no_fallback(monkeypatch):
    """NXDOMAIN é resposta, não silêncio: repetir só custaria tempo."""
    primario = _ResolverFalso(erro=dns.resolver.NXDOMAIN())
    fallback = _ResolverFalso(resposta=["nunca"])
    _patch_resolvers(monkeypatch, primario, fallback)

    with pytest.raises(dns.resolver.NXDOMAIN):
        dns_resolver.resolve("nao-existe.invalid", "MX")
    assert fallback.chamadas == []


def test_apos_uma_falha_o_sistema_deixa_de_ser_consultado(monkeypatch):
    """
    A ficha faz ~10 consultas. Sem esta memória, cada uma pagaria de novo os
    segundos de timeout do resolver quebrado.
    """
    primario = _ResolverFalso(erro=dns.resolver.LifetimeTimeout(timeout=5.0, errors=[]))
    fallback = _ResolverFalso(resposta=["ok"])
    _patch_resolvers(monkeypatch, primario, fallback)

    for rtype in ("MX", "A", "NS", "TXT"):
        dns_resolver.resolve("exemplo.com.br", rtype)

    assert len(primario.chamadas) == 1
    assert len(fallback.chamadas) == 4


def test_sem_fallback_configurado_o_erro_sobe(monkeypatch):
    primario = _ResolverFalso(erro=dns.resolver.LifetimeTimeout(timeout=5.0, errors=[]))
    _patch_resolvers(monkeypatch, primario, None)

    with pytest.raises(dns.resolver.LifetimeTimeout):
        dns_resolver.resolve("exemplo.com.br", "MX")


def test_resolve_do_dns_lookup_devolve_lista_vazia_quando_ninguem_responde(monkeypatch):
    """O contrato de `_resolve` não muda: quem chama continua vendo []."""
    from services import dns_lookup

    def _explode(*a, **kw):
        raise dns.resolver.LifetimeTimeout(timeout=5.0, errors=[])

    monkeypatch.setattr(dns_lookup.dns_resolver, "resolve", _explode)
    assert dns_lookup._resolve("exemplo.com.br", "MX") == []


# ══════════════════════════════════════════════════════════════════
# 2. Merge não-destrutivo
# ══════════════════════════════════════════════════════════════════

def _ficha(**kwargs):
    """Ficha já coletada uma vez, com dados bons."""
    base = dict(
        domain="magazineluiza.com.br", company_name="Magazine Luiza",
        website="https://magazineluiza.com.br", linkedin_url=None,
        linkedin_confidence=None, mx_provider="Google Workspace",
        mx_provider_confidence="high",
        mx_records=[{"host": "aspmx.l.google.com", "priority": 1}],
        dns_report={"mx": ["aspmx.l.google.com"]}, hosting_provider="AWS",
        employee_count={"band": "1000+"}, employee_count_linkedin=None,
        sector="Varejo", location="Franca, SP", description="Loja",
        corporate_email=None, phone=None, status="partial",
        site_block_reason=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _coleta_vazia(**kwargs):
    """O que o enricher devolve quando nada respondeu."""
    base = dict(
        company_name="Magazineluiza",  # fallback derivado do domínio
        website="https://magazineluiza.com.br", linkedin_url=None,
        linkedin_confidence=None, mx_provider=None, mx_provider_confidence=None,
        mx_records=[], dns_report={"mx": [], "a": []}, hosting_provider=None,
        employee_count=None, employee_count_linkedin=None, sector=None,
        location=None, description=None, corporate_email=None, phone=None,
        site_block_reason=None, status="failed",
    )
    base.update(kwargs)
    return base


def test_coleta_vazia_nao_apaga_dados_de_uma_coleta_anterior():
    lead = _ficha()
    merge_collected(lead, _coleta_vazia())

    assert lead.mx_provider == "Google Workspace"
    assert lead.mx_records == [{"host": "aspmx.l.google.com", "priority": 1}]
    assert lead.dns_report == {"mx": ["aspmx.l.google.com"]}
    assert lead.sector == "Varejo"
    assert lead.location == "Franca, SP"
    assert lead.hosting_provider == "AWS"


def test_coleta_vazia_nao_rebaixa_o_status_da_ficha():
    lead = _ficha(status="partial")
    merge_collected(lead, _coleta_vazia())
    assert lead.status == "partial"


def test_ficha_sem_nada_aceita_o_status_failed():
    """Sem dado nenhum para proteger, `failed` é a verdade e tem que colar."""
    lead = _ficha(mx_provider=None, sector=None, location=None,
                  employee_count=None, linkedin_url=None, status="pending")
    merge_collected(lead, _coleta_vazia())
    assert lead.status == "failed"


def test_coleta_boa_sobrescreve_o_que_estava_la():
    lead = _ficha(sector="Varejo", status="partial")
    merge_collected(lead, _coleta_vazia(
        sector="Comércio varejista", mx_provider="Microsoft 365",
        location="São Paulo, SP", status="enriched",
    ))
    assert lead.sector == "Comércio varejista"
    assert lead.mx_provider == "Microsoft 365"
    assert lead.status == "enriched"


def test_status_sobe_de_partial_para_enriched():
    lead = _ficha(status="partial")
    merge_collected(lead, _coleta_vazia(sector="Varejo", status="enriched"))
    assert lead.status == "enriched"


def test_bloqueio_e_sempre_o_da_coleta_atual():
    """Site que voltou a responder não pode continuar marcado como bloqueado."""
    lead = _ficha(site_block_reason="http_403")
    merge_collected(lead, _coleta_vazia(sector="Varejo", status="partial"))
    assert lead.site_block_reason is None


# ══════════════════════════════════════════════════════════════════
# 3. Site que recusa o robô
# ══════════════════════════════════════════════════════════════════

def _resposta(status_code=200, text="<html><title>Empresa</title></html>"):
    return SimpleNamespace(
        status_code=status_code, text=text, headers={"content-type": "text/html; charset=utf-8"},
        raise_for_status=lambda: None,
    )


@pytest.mark.parametrize("codigo,motivo", [
    (403, "http_403"),
    (429, "http_429"),
    (451, "http_451"),
])
def test_site_que_recusa_o_robo_vira_status_blocked(codigo, motivo):
    from services import scraper

    with patch.object(scraper, "safe_get", return_value=_resposta(status_code=codigo)):
        data = scraper.scrape_website("https://magazineluiza.com.br")

    assert data["status"] == "blocked"
    assert data["block_reason"] == motivo


def test_desafio_anti_bot_vira_blocked_e_nao_failed():
    from services import scraper

    with patch.object(scraper, "safe_get", return_value=_resposta()), \
         patch.object(scraper, "looks_like_bot_wall", return_value=True):
        data = scraper.scrape_website("https://magazineluiza.com.br")

    assert data["status"] == "blocked"
    assert data["block_reason"] == "bot_wall"


def test_site_fora_do_ar_continua_failed_sem_motivo():
    """Sem resposta não há bloqueio a reportar — e a tela não deve inventar um."""
    from services import scraper

    with patch.object(scraper, "safe_get", return_value=None):
        data = scraper.scrape_website("https://exemplo.com.br")

    assert data["status"] == "failed"
    assert data["block_reason"] is None
