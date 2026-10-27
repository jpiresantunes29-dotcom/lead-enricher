"""
Precisão da coleta de MX/provedor de e-mail e dos links oficiais da empresa.

Nada aqui vai à rede: o resolver DNS, as páginas do site e as do LinkedIn são
substituídos por respostas montadas no teste. O que se testa é o julgamento —
qual MX é o primário, quando dá para afirmar o provedor, quando o domínio não
recebe e-mail, qual link do rodapé é o da empresa — porque é ele que decide se
a ficha mostra um dado certo, um palpite ou um dado errado.
"""
from types import SimpleNamespace

import dns.exception
import dns.resolver
import pytest
from bs4 import BeautifulSoup

from services import dns_lookup, scraper
from services._utils import (
    canonical_site_url, host_matches, is_credit_anchor, normalize_domain,
    registrable_domain, strip_tracking,
)
from services.dns_lookup import (
    identify_provider, lookup_mx, mailbox_from_spf, parse_mx_answers,
    spf_includes, summarize_mail_provider,
)
from services.linkedin_search import (
    _confidence, _extract_candidates_from_html, _pick_best_candidate,
)


# ── dublês de DNS ────────────────────────────────────────────────────────────

def _mx(pref, host):
    return SimpleNamespace(preference=pref, exchange=host)


class _Answer(list):
    """Imita `dns.resolver.Answer`: iterável e com `canonical_name`."""

    def __init__(self, items, canonical_name=None):
        super().__init__(items)
        self.canonical_name = canonical_name


def _dns_falso(monkeypatch, tabela):
    """
    `tabela[(nome, tipo)]` é a resposta (lista/_Answer) ou a exceção a levantar.
    Consulta fora da tabela é NoAnswer — o domínio existe, mas não publica.
    """
    consultas = []

    def resolve(name, rtype, lifetime=5.0):
        consultas.append((name, rtype))
        valor = tabela.get((name, rtype), dns.resolver.NoAnswer())
        if isinstance(valor, BaseException):
            raise valor
        return valor

    monkeypatch.setattr(dns_lookup.dns_resolver, "resolve", resolve)
    return consultas


# ── MX: registros publicados ─────────────────────────────────────────────────

def test_mx_sai_em_ordem_de_preferencia_e_deduplicado():
    """
    O DNS devolve os registros em qualquer ordem, e há zona que repete o mesmo
    host com duas prioridades. O primário tem de ser o de MENOR prioridade, e
    o mesmo a cada coleta (empate decidido pelo nome).
    """
    parsed = parse_mx_answers([
        _mx(20, "alt1.aspmx.l.google.com."),
        _mx(10, "ASPMX.L.GOOGLE.COM."),
        _mx(30, "alt2.aspmx.l.google.com."),
        _mx(10, "alt3.aspmx.l.google.com."),
        _mx(50, "aspmx.l.google.com."),
    ])
    assert parsed["null_mx"] is False
    assert parsed["records"] == [
        {"priority": 10, "host": "alt3.aspmx.l.google.com"},
        {"priority": 10, "host": "aspmx.l.google.com"},
        {"priority": 20, "host": "alt1.aspmx.l.google.com"},
        {"priority": 30, "host": "alt2.aspmx.l.google.com"},
    ]


def test_mx_nulo_declara_que_o_dominio_nao_recebe_email():
    """RFC 7505: `0 .` é o único registro → o domínio não recebe e-mail."""
    parsed = parse_mx_answers([_mx(0, ".")])
    assert parsed == {"records": [], "null_mx": True}


def test_mx_nulo_misturado_com_mx_real_e_ignorado():
    """Configuração errada: os MX reais continuam recebendo, o nulo não vale."""
    parsed = parse_mx_answers([_mx(0, "."), _mx(10, "mx.empresa.com.br.")])
    assert parsed["null_mx"] is False
    assert parsed["records"] == [{"priority": 10, "host": "mx.empresa.com.br"}]


def test_lookup_mx_com_registros_publicados(monkeypatch):
    _dns_falso(monkeypatch, {
        ("empresa.com.br", "MX"): _Answer([_mx(0, "empresa-com-br.mail.protection.outlook.com.")]),
    })
    res = lookup_mx("https://www.Empresa.com.br/contato")
    assert res["status"] == "ok"
    assert res["source_domain"] == "empresa.com.br"
    assert res["records"][0]["host"] == "empresa-com-br.mail.protection.outlook.com"


def test_lookup_mx_nulo(monkeypatch):
    _dns_falso(monkeypatch, {("parked.com.br", "MX"): _Answer([_mx(0, ".")])})
    res = lookup_mx("parked.com.br")
    assert res["status"] == "null_mx"
    assert res["records"] == []


def test_dominio_sem_mx_mas_com_a_e_marcado_como_mx_implicito(monkeypatch):
    """
    RFC 5321 §5.1: sem MX, entrega-se no A do domínio. Isso é registrado como
    "implicit" — e nunca vira linha de MX: o A é do servidor do site.
    """
    _dns_falso(monkeypatch, {("acme.com.br", "A"): ["200.1.2.3"]})
    res = lookup_mx("acme.com.br")
    assert res["status"] == "implicit"
    assert res["implicit_host"] == "acme.com.br"
    assert res["records"] == []


def test_dominio_sem_mx_e_sem_a_fica_sem_mx(monkeypatch):
    _dns_falso(monkeypatch, {})
    assert lookup_mx("acme.com.br")["status"] == "none"


def test_dominio_inexistente_nao_procura_mx_implicito(monkeypatch):
    consultas = _dns_falso(monkeypatch, {
        ("naoexiste.com.br", "MX"): dns.resolver.NXDOMAIN(),
    })
    assert lookup_mx("naoexiste.com.br")["status"] == "nxdomain"
    assert ("naoexiste.com.br", "A") not in consultas


def test_dns_sem_resposta_nao_vira_sem_mx(monkeypatch):
    """
    Timeout não prova ausência. Cair para o domínio raiz ou para o MX
    implícito aqui faria a ficha afirmar algo que ninguém respondeu.
    """
    consultas = _dns_falso(monkeypatch, {
        ("acme.com.br", "MX"): dns.exception.Timeout(),
    })
    res = lookup_mx("acme.com.br")
    assert res["status"] == "unreachable"
    assert consultas == [("acme.com.br", "MX")]


def test_resolvedores_sem_servidor_tambem_e_sem_resposta(monkeypatch):
    _dns_falso(monkeypatch, {
        ("acme.com.br", "MX"): dns.resolver.NoNameservers(),
    })
    assert lookup_mx("acme.com.br")["status"] == "unreachable"


def test_subdominio_sem_mx_usa_o_mx_do_dominio_raiz(monkeypatch):
    """
    "vendas.empresa.com.br" não publica MX; o e-mail da empresa mora em
    "empresa.com.br". Dizer "sem MX" seria falso para quem vai escrever.
    """
    _dns_falso(monkeypatch, {
        ("vendas.empresa.com.br", "MX"): dns.resolver.NXDOMAIN(),
        ("empresa.com.br", "MX"): _Answer([_mx(1, "aspmx.l.google.com.")]),
    })
    res = lookup_mx("vendas.empresa.com.br")
    assert res["status"] == "ok"
    assert res["source_domain"] == "empresa.com.br"
    assert res["records"] == [{"priority": 1, "host": "aspmx.l.google.com"}]


def test_subdominio_com_mx_proprio_nao_herda_o_da_raiz(monkeypatch):
    consultas = _dns_falso(monkeypatch, {
        ("mail.empresa.com.br", "MX"): _Answer([_mx(10, "mx.kinghost.net.")]),
        ("empresa.com.br", "MX"): _Answer([_mx(1, "aspmx.l.google.com.")]),
    })
    res = lookup_mx("mail.empresa.com.br")
    assert res["source_domain"] == "mail.empresa.com.br"
    assert res["records"][0]["host"] == "mx.kinghost.net"
    assert ("empresa.com.br", "MX") not in consultas


def test_dominio_cname_registra_de_onde_veio_o_mx(monkeypatch):
    _dns_falso(monkeypatch, {
        ("marca.com.br", "MX"): _Answer([_mx(10, "mx.zoho.com.")],
                                        canonical_name="grupo.com.br."),
    })
    res = lookup_mx("marca.com.br")
    assert res["status"] == "ok"
    assert res["alias_of"] == "grupo.com.br"


# ── provedor de e-mail ───────────────────────────────────────────────────────

@pytest.mark.parametrize("host,esperado", [
    ("aspmx.l.google.com", "Google Workspace"),
    ("smtp.google.com", "Google Workspace"),
    ("empresa-com-br.mail.protection.outlook.com", "Microsoft 365"),
    ("empresa-com-br.l-v1.mx.microsoft", "Microsoft 365"),
    ("mx.zoho.com", "Zoho Mail"),
    ("mx.zoho.eu", "Zoho Mail"),
    ("mx.a.locaweb.com.br", "Locaweb"),
    ("mx.kinghost.net", "KingHost"),
    ("mx.uhserver.com", "UOL Host"),
    ("mx1.hostinger.com", "Hostinger"),
    ("mx1.titan.email", "Titan Mail"),
    ("mx-ha.skymail.net.br", "Skymail"),
    ("mx0a-001.pphosted.com", "Proofpoint"),
    ("us-smtp-inbound-1.mimecast.com", "Mimecast"),
    ("d123.ess.barracudanetworks.com", "Barracuda"),
    ("mx1.hc123-45.iphmx.com", "Cisco IronPort"),
    ("mx10.mailspamprotection.com", "SiteGround"),
])
def test_provedores_conhecidos_pelo_hostname(host, esperado):
    assert identify_provider(host, None) == (esperado, "high")


def test_padrao_de_provedor_casa_por_sufixo_e_nao_por_pedaco():
    """
    `"uol.com.br" in host` casava com "bauol.com.br" — uma empresa qualquer
    virava cliente do UOL Host. O padrão tem de ser o domínio inteiro.
    """
    assert identify_provider("mx.bauol.com.br", None)[1] != "high"
    assert identify_provider("mail.google.com.empresa.net", None)[1] != "high"
    assert host_matches("mx.uol.com.br", "uol.com.br")
    assert not host_matches("mx.bauol.com.br", "uol.com.br")


def test_provedor_sai_do_mx_primario_e_nao_do_backup():
    """
    Servidor próprio na prioridade 10 com backup no Google na 50: quem recebe
    o e-mail é o servidor próprio. Antes, bastava QUALQUER MX bater para o
    Google Workspace sair com confiança alta.
    """
    resumo = summarize_mail_provider([
        {"priority": 10, "host": "mail.acme.com.br"},
        {"priority": 50, "host": "aspmx.l.google.com"},
    ])
    assert resumo["mx_primary"] == "mail.acme.com.br"
    assert resumo["mx_provider"] == "Google Workspace"
    assert resumo["mx_provider_confidence"] == "medium"
    assert resumo["mx_provider_evidence"] == "mx_backup"
    assert resumo["mailbox_provider"] is None


def test_mx_com_nome_proprio_e_reconhecido_pelo_cname():
    resumo = summarize_mail_provider(
        [{"priority": 0, "host": "mx.acme.com.br"}],
        resolve_cname=lambda host: "acme-com-br.mail.protection.outlook.com",
    )
    assert resumo["mx_provider"] == "Microsoft 365"
    assert resumo["mx_provider_confidence"] == "high"
    assert resumo["mx_provider_evidence"] == "mx_cname"


def test_mx_com_nome_proprio_e_reconhecido_pelo_ptr():
    resumo = summarize_mail_provider([
        {"priority": 10, "host": "mx.acme.com.br", "ptr": "mx-ha.skymail.net.br"},
    ])
    assert resumo["mx_provider"] == "Skymail"
    assert resumo["mx_provider_evidence"] == "mx_ptr"


def test_ptr_de_nuvem_generica_nao_afirma_provedor_de_email():
    """MX próprio numa EC2 não faz da empresa cliente de e-mail da Amazon."""
    resumo = summarize_mail_provider([
        {"priority": 10, "host": "mail.acme.com.br",
         "ptr": "ec2-3-4-5-6.compute-1.amazonaws.com"},
    ])
    assert resumo["mx_provider_confidence"] != "high"


def test_gateway_com_spf_de_uma_caixa_postal_mostra_os_dois():
    """
    Proofpoint na frente, Microsoft 365 atrás: o SPF entrega a caixa postal.
    O rótulo é o mesmo que o classificador de tier já monta.
    """
    resumo = summarize_mail_provider(
        [{"priority": 10, "host": "mx0a-0012.pphosted.com"},
         {"priority": 10, "host": "mx0b-0012.pphosted.com"}],
        spf="v=spf1 include:spf.protection.outlook.com include:sendgrid.net -all",
    )
    assert resumo["mx_gateway"] == "Proofpoint"
    assert resumo["mailbox_provider"] == "Microsoft 365"
    assert resumo["mailbox_evidence"] == "spf"
    assert resumo["mx_provider"] == "Microsoft 365 (via Proofpoint)"
    assert resumo["mx_provider_confidence"] == "high"


def test_gateway_sem_spf_conclusivo_fica_so_com_o_gateway():
    """Google e Microsoft no SPF (migração): ambíguo, a ficha não escolhe."""
    resumo = summarize_mail_provider(
        [{"priority": 10, "host": "eu-smtp-inbound-1.mimecast.com"}],
        spf="v=spf1 include:_spf.google.com include:spf.protection.outlook.com ~all",
    )
    assert resumo["mx_provider"] == "Mimecast"
    assert resumo["mx_gateway"] == "Mimecast"
    assert resumo["mailbox_provider"] is None
    assert resumo["mailbox_candidates"] == ["Google Workspace", "Microsoft 365"]


def test_spf_de_disparo_de_marketing_nao_vira_caixa_postal():
    assert mailbox_from_spf(
        "v=spf1 include:sendgrid.net include:servers.mcsv.net include:amazonses.com ~all"
    ) == (None, [])


def test_spf_le_include_e_redirect_com_qualificador():
    assert spf_includes("v=spf1 +include:_spf.google.com ?include:x.com redirect=_spf.acme.com") == [
        "_spf.google.com", "x.com", "_spf.acme.com",
    ]


def test_provedor_comum_vira_caixa_postal_sem_gateway():
    resumo = summarize_mail_provider([{"priority": 1, "host": "aspmx.l.google.com"}])
    assert resumo["mailbox_provider"] == "Google Workspace"
    assert resumo["mx_gateway"] is None


def test_sem_mx_nao_ha_provedor():
    resumo = summarize_mail_provider([])
    assert resumo["mx_provider"] is None
    assert resumo["mx_provider_confidence"] == "none"


# ── relatório DNS de ponta a ponta (sem rede) ────────────────────────────────

@pytest.fixture
def sem_whois(monkeypatch):
    """WHOIS/PTR/A dos hosts fora da rede: só o MX e o TXT importam aqui."""
    monkeypatch.setattr(dns_lookup, "_ip_info", lambda ip: dict(dns_lookup._EMPTY_IP_INFO))
    monkeypatch.setattr(dns_lookup, "_ptr", lambda ip: None)
    monkeypatch.setattr(dns_lookup, "_resolve_a", lambda host: None)


def _txt(valor):
    return SimpleNamespace(strings=[valor.encode()])


def test_relatorio_com_gateway_e_spf(monkeypatch, sem_whois):
    _dns_falso(monkeypatch, {
        ("acme.com.br", "MX"): _Answer([_mx(10, "mx0b-1.pphosted.com."),
                                        _mx(10, "mx0a-1.pphosted.com.")]),
        ("acme.com.br", "TXT"): [_txt("v=spf1 include:spf.protection.outlook.com -all")],
    })
    report = dns_lookup.get_dns_report("acme.com.br")
    assert report["mx_status"] == "ok"
    assert [m["host"] for m in report["mx"]] == ["mx0a-1.pphosted.com", "mx0b-1.pphosted.com"]
    assert report["mx_primary"] == "mx0a-1.pphosted.com"
    assert report["mx_provider"] == "Microsoft 365 (via Proofpoint)"
    assert report["mx_gateway"] == "Proofpoint"
    assert report["mailbox_provider"] == "Microsoft 365"


def test_relatorio_com_mx_nulo_nao_tem_linha_nem_provedor(monkeypatch, sem_whois):
    _dns_falso(monkeypatch, {("acme.com.br", "MX"): _Answer([_mx(0, ".")])})
    report = dns_lookup.get_dns_report("acme.com.br")
    assert report["mx_status"] == "null_mx"
    assert report["mx"] == []
    assert report["mx_provider"] is None


def test_relatorio_com_mx_implicito_nao_inventa_provedor(monkeypatch, sem_whois):
    _dns_falso(monkeypatch, {("acme.com.br", "A"): ["200.1.2.3"]})
    report = dns_lookup.get_dns_report("acme.com.br")
    assert report["mx_status"] == "implicit"
    assert report["mx_implicit_host"] == "acme.com.br"
    assert report["mx"] == []
    assert report["mx_provider"] is None


def test_relatorio_com_dns_fora_do_ar_diz_que_nao_sabe(monkeypatch, sem_whois):
    def _explode(*a, **k):
        raise dns.exception.Timeout()

    monkeypatch.setattr(dns_lookup.dns_resolver, "resolve", _explode)
    report = dns_lookup.get_dns_report("acme.com.br")
    assert report["mx_status"] == "unreachable"
    assert report["mx_provider"] is None


def test_enricher_aceita_gateway_com_caixa_postal_e_recusa_backup(monkeypatch):
    """Só "high" entra na ficha: backup conhecido fica no relatório."""
    from services import enricher

    def _coleta(dns_data):
        monkeypatch.setattr(enricher, "scrape_website", lambda url: None)
        monkeypatch.setattr(enricher, "get_dns_report", lambda d: dns_data)
        monkeypatch.setattr(enricher, "fetch_rdap", lambda d: None)
        monkeypatch.setattr(enricher, "fetch_employee_count", lambda *a, **k: None)
        monkeypatch.setattr(enricher, "find_company_linkedin",
                            lambda *a, **k: {"url": None, "page": None, "source": None})
        return enricher.enrich_company("acme.com.br")

    via = _coleta({"mx": [{"priority": 10, "host": "mx0a-1.pphosted.com"}],
                   "mx_provider": "Microsoft 365 (via Proofpoint)",
                   "mx_provider_confidence": "high"})
    assert via["mx_provider"] == "Microsoft 365 (via Proofpoint)"

    backup = _coleta({"mx": [{"priority": 10, "host": "mail.acme.com.br"}],
                      "mx_provider": "Google Workspace",
                      "mx_provider_confidence": "medium"})
    assert backup["mx_provider"] is None
    assert backup["mx_records"] == [{"priority": 10, "host": "mail.acme.com.br"}]


# ── domínio de entrada ───────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    ("https://www.Empresa.com.br/contato?utm_source=x", "empresa.com.br"),
    ("empresa.com.br.", "empresa.com.br"),
    ("empresa.com.br:443", "empresa.com.br"),
    ("contato@empresa.com.br", "empresa.com.br"),
    ("www.empresa.com.br#topo", "empresa.com.br"),
])
def test_normaliza_dominio_com_porta_ponto_final_e_email(entrada, esperado):
    assert normalize_domain(entrada) == esperado


@pytest.mark.parametrize("host,esperado", [
    ("vendas.empresa.com.br", "empresa.com.br"),
    ("empresa.com.br", "empresa.com.br"),
    ("mx.a.locaweb.com.br", "locaweb.com.br"),
    ("www.receita.fazenda.gov.br", "fazenda.gov.br"),
    ("blog.acme.com", "acme.com"),
    ("acme.co.uk", "acme.co.uk"),
])
def test_dominio_registravel(host, esperado):
    assert registrable_domain(host) == esperado


# ── site oficial ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("final,esperado", [
    ("https://www.acme.com.br/pt-br/?utm_source=google&gclid=1", "https://www.acme.com.br"),
    ("https://acme.com.br/", "https://acme.com.br"),
    # Site que só existe em http continua em http: forçar https daria link quebrado.
    ("http://acme.com.br/home", "http://acme.com.br"),
    # A empresa redireciona para a marca nova: é a declaração dela.
    ("https://www.grupoacme.com/br", "https://www.grupoacme.com"),
])
def test_site_oficial_e_a_origem_onde_a_home_terminou(final, esperado):
    assert canonical_site_url(final, "acme.com.br") == esperado


@pytest.mark.parametrize("final", [
    "https://www.instagram.com/acme/",
    "https://www.hugedomains.com/domain_profile.cfm?d=acme.com.br",
    "https://linktr.ee/acme",
])
def test_redirecionamento_para_rede_social_ou_estacionamento_nao_e_site(final):
    assert canonical_site_url(final, "acme.com.br") is None


def test_parametros_de_rastreio_saem_da_url():
    assert strip_tracking(
        "https://acme.com.br/produto?id=7&utm_campaign=x&fbclid=y#topo"
    ) == "https://acme.com.br/produto?id=7"


def _resposta(url, html):
    return SimpleNamespace(status_code=200, text=html, url=url,
                           headers={"content-type": "text/html; charset=utf-8"},
                           apparent_encoding="utf-8", encoding="utf-8",
                           raise_for_status=lambda: None)


def test_scraper_grava_o_site_onde_a_home_terminou(monkeypatch):
    html = ("<html><head><title>Acme</title></head><body>"
            "<footer><a href='https://br.linkedin.com/company/Acme/?trk=foot'>in</a>"
            "</footer>CNPJ 11.222.333/0001-81</body></html>")
    monkeypatch.setattr(scraper, "safe_get", lambda url, **k: _resposta(
        "https://www.acme.com.br/pt/?utm_source=ads", html))
    monkeypatch.setattr(scraper, "_cnpj_seguindo_links_do_site", lambda *a, **k: None)
    data = scraper.scrape_website("https://acme.com.br")
    assert data["website"] == "https://www.acme.com.br"
    # Link do rodapé na forma canônica, sem subdomínio de país nem rastreio.
    assert data["linkedin_url"] == "https://www.linkedin.com/company/acme"
    assert data["linkedin_ambiguous"] is False


def test_enricher_usa_o_site_canonico_e_valida_linkedin_pelo_destino(monkeypatch):
    """
    acme.com.br redireciona para acme.com, e o LinkedIn declara acme.com:
    é o mesmo dono — antes isso perdia o "verified".
    """
    from services import enricher

    visto = {}

    def _inspect(url, domain, **kwargs):
        visto.update(kwargs)
        return {"confidence": "verified", "sector": None, "location": None,
                "size": None, "name": "Acme", "html": None}

    monkeypatch.setattr(enricher, "scrape_website", lambda url: {
        "status": "enriched", "company_name": "Acme", "website": "https://www.acme.com",
        "linkedin_url": "https://www.linkedin.com/company/acme",
        "linkedin_ambiguous": False, "emails": [], "phones": [],
    })
    monkeypatch.setattr(enricher, "get_dns_report", lambda d: None)
    monkeypatch.setattr(enricher, "fetch_rdap", lambda d: None)
    monkeypatch.setattr(enricher, "fetch_employee_count", lambda *a, **k: None)
    monkeypatch.setattr(enricher, "inspect_company_page", _inspect)
    result = enricher.enrich_company("acme.com.br")
    assert result["website"] == "https://www.acme.com"
    assert visto["outros_dominios"] == ("acme.com",)
    assert visto["declarado_pelo_site"] is True
    assert result["linkedin_url"] == "https://www.linkedin.com/company/acme"


def test_dominio_do_redirecionamento_confirma_o_linkedin():
    html = "<html>" + "x" * 600 + "</html>"
    assert _confidence(html, "acme.com.br", "https://www.acme.com",
                       outros_dominios=("acme.com",)) == "verified"
    assert _confidence(html, "acme.com.br", "https://www.acme.com") == "unverified"


# ── LinkedIn oficial ─────────────────────────────────────────────────────────

_RODAPE_COM_AGENCIA = """
<footer>
  <div class="creditos">Desenvolvido por <a href="https://www.linkedin.com/company/agencia-pixel">Agência Pixel</a></div>
  <ul class="social">
    <li><a href="https://www.linkedin.com/company/acme-industria">LinkedIn</a></li>
  </ul>
</footer>
"""


def test_credito_da_agencia_no_rodape_nao_vira_linkedin_da_empresa():
    soup = BeautifulSoup(_RODAPE_COM_AGENCIA, "html.parser")
    url, ambiguo = scraper._escolher_linkedin(soup, _RODAPE_COM_AGENCIA, "acme.com.br", "Acme")
    assert url == "https://www.linkedin.com/company/acme-industria"
    assert ambiguo is False


def test_credito_da_agencia_e_reconhecido_pelo_texto_em_volta():
    soup = BeautifulSoup(_RODAPE_COM_AGENCIA, "html.parser")
    anchors = soup.find_all("a")
    assert is_credit_anchor(anchors[0])
    assert not is_credit_anchor(anchors[1])


def test_busca_no_site_tambem_ignora_o_credito_da_agencia():
    soup = BeautifulSoup(_RODAPE_COM_AGENCIA, "html.parser")
    candidatos = _extract_candidates_from_html(_RODAPE_COM_AGENCIA, soup)
    assert [c["slug"] for c in candidatos] == ["acme-industria"]


def test_so_link_da_agencia_no_site_nao_vira_linkedin():
    html = ('<footer><p>Site por <a href="https://linkedin.com/company/agencia-pixel">'
            'Pixel</a></p></footer>')
    url, _ = scraper._escolher_linkedin(BeautifulSoup(html, "html.parser"), html,
                                        "acme.com.br", "Acme")
    assert url is None


def test_link_parecido_com_a_empresa_ganha_do_parceiro_no_rodape():
    html = ('<footer><a href="https://www.linkedin.com/company/parceiro-logistico">a</a></footer>'
            '<header><a href="https://www.linkedin.com/company/acme">b</a></header>')
    url, ambiguo = scraper._escolher_linkedin(BeautifulSoup(html, "html.parser"), html,
                                              "acme.com.br", "Acme")
    assert url == "https://www.linkedin.com/company/acme"
    assert ambiguo is False


def test_varias_organizacoes_sem_nenhuma_parecida_e_ambiguo():
    """Sem sinal de qual é a empresa, o link não pode valer como declarado."""
    html = ('<footer><a href="https://www.linkedin.com/company/parceiro-um">a</a>'
            '<a href="https://www.linkedin.com/company/parceiro-dois">b</a></footer>')
    url, ambiguo = scraper._escolher_linkedin(BeautifulSoup(html, "html.parser"), html,
                                              "acme.com.br", "Acme")
    assert url is not None
    assert ambiguo is True


def test_enricher_exige_confirmacao_quando_o_link_do_site_e_ambiguo(monkeypatch):
    """
    Link ambíguo + página do LinkedIn que não respondeu = nada na ficha.
    Antes, todo link do site valia "probable" mesmo sem página.
    """
    from services import enricher, linkedin_search

    monkeypatch.setattr(linkedin_search, "fetch_company_page", lambda url: None)
    monkeypatch.setattr(enricher, "scrape_website", lambda url: {
        "status": "enriched", "company_name": "Acme", "website": "https://acme.com.br",
        "linkedin_url": "https://www.linkedin.com/company/parceiro-um",
        "linkedin_ambiguous": True, "emails": [], "phones": [],
    })
    monkeypatch.setattr(enricher, "get_dns_report", lambda d: None)
    monkeypatch.setattr(enricher, "fetch_rdap", lambda d: None)
    monkeypatch.setattr(enricher, "fetch_employee_count", lambda *a, **k: None)
    result = enricher.enrich_company("acme.com.br")
    assert result["linkedin_url"] is None
    assert result["linkedin_confidence"] is None


@pytest.mark.parametrize("href", [
    "https://www.linkedin.com/in/joao-silva-123",           # perfil pessoal
    "https://www.linkedin.com/showcase/acme-cloud",          # showcase
    "https://www.linkedin.com/jobs/view/3901234567",         # vaga
    "https://www.linkedin.com/company/74031250/admin",       # painel (login)
    "https://www.linkedin.com/feed/update/urn:li:activity:1",  # post
])
def test_links_que_nao_sao_pagina_da_empresa_sao_descartados(href):
    html = f'<footer><a href="{href}">LinkedIn</a></footer>'
    soup = BeautifulSoup(html, "html.parser")
    assert scraper._extract_linkedin(soup, html, "acme.com.br", "Acme") is None
    assert _extract_candidates_from_html(html, soup) == []


@pytest.mark.parametrize("href", [
    "http://br.linkedin.com/company/Acme",
    "https://m.linkedin.com/company/acme/",
    "https://www.linkedin.com/company/acme/jobs/",
    "https://www.linkedin.com/company/acme?trk=public_profile",
])
def test_variantes_do_mesmo_perfil_viram_a_url_canonica(href):
    html = f'<footer><a href="{href}">LinkedIn</a></footer>'
    soup = BeautifulSoup(html, "html.parser")
    assert scraper._extract_linkedin(soup, html) == "https://www.linkedin.com/company/acme"


def test_escolha_no_site_considera_o_nome_da_empresa():
    """
    Domínio curto ("gb.com.br") não se parece com slug nenhum; o nome que o
    site declara ("Grupo Boticário") é o que separa a empresa do fundo de
    investimento que ela também lista.
    """
    candidatos = [
        {"slug": "gb-ventures", "kind": "company",
         "url": "https://www.linkedin.com/company/gb-ventures", "source": "anchor", "weight": 0.5},
        {"slug": "grupo-boticario", "kind": "company",
         "url": "https://www.linkedin.com/company/grupo-boticario", "source": "anchor", "weight": 0.5},
    ]
    assert _pick_best_candidate(candidatos, "gb.com.br", "Grupo Boticário") == (
        "https://www.linkedin.com/company/grupo-boticario"
    )


def test_homonimo_do_buscador_nao_vira_linkedin_e_o_segundo_confirmado_ganha(monkeypatch):
    from services import linkedin_search

    monkeypatch.setattr(linkedin_search, "_extract_from_site", lambda *a, **k: None)
    monkeypatch.setattr(linkedin_search, "_guess_from_slugs", lambda *a, **k: None)
    monkeypatch.setattr(linkedin_search, "search_multi", lambda q, **k: [
        {"url": "https://www.linkedin.com/company/acme-ltda", "title": "Acme Ltda"},
        {"url": "https://www.linkedin.com/company/acme-industria", "title": "Acme Indústria"},
    ])
    paginas = {
        # Homônimo: outra marca, outro site — vem em primeiro no buscador.
        "https://www.linkedin.com/company/acme-ltda":
            '<meta property="og:title" content="Acme Consultoria Contábil | LinkedIn">'
            "<dl><dt>Site</dt><dd>https://acmeltda.com</dd></dl>" + "x" * 600,
        "https://www.linkedin.com/company/acme-industria":
            '<meta property="og:title" content="Acme Indústria | LinkedIn">'
            "<dl><dt>Site</dt><dd>https://www.acme.com.br</dd></dl>" + "x" * 600,
    }
    monkeypatch.setattr(linkedin_search, "fetch_company_page", lambda url: paginas.get(url))
    found = linkedin_search.find_company_linkedin("acme.com.br", "Acme")
    assert found["url"] == "https://www.linkedin.com/company/acme-industria"
    assert found["confidence"] == "verified"
    assert found["source"] == "search_engine"


def test_buscador_so_com_homonimos_nao_confirma_nada(monkeypatch):
    from services import linkedin_search

    monkeypatch.setattr(linkedin_search, "_extract_from_site", lambda *a, **k: None)
    monkeypatch.setattr(linkedin_search, "_guess_from_slugs", lambda *a, **k: None)
    monkeypatch.setattr(linkedin_search, "search_multi", lambda q, **k: [
        {"url": "https://www.linkedin.com/company/outra-acme", "title": "Outra Acme"},
    ])
    monkeypatch.setattr(linkedin_search, "fetch_company_page", lambda url: (
        '<meta property="og:title" content="Outra Acme Consultoria | LinkedIn">'
        "<dl><dt>Site</dt><dd>https://outraacme.com</dd></dl>" + "x" * 600))
    found = linkedin_search.find_company_linkedin("acme.com.br", "Acme")
    assert found["confidence"] == "unverified"


# ── descoberta de domínio ────────────────────────────────────────────────────

def test_descoberta_de_dominio_reduz_ao_dominio_registravel(monkeypatch):
    from services import domain_finder

    monkeypatch.setattr(domain_finder, "search_multi", lambda *a, **k: [
        {"url": "https://loja.abix.com.br/produto?utm_source=x", "title": ""},
    ])
    monkeypatch.setattr(domain_finder, "is_public_host", lambda h: True)
    monkeypatch.setattr(domain_finder, "_confirm_on_site", lambda *a: 0.0)
    assert domain_finder.find_domain("Abix Tecnologia")["domain"] == "abix.com.br"


def test_descoberta_de_dominio_descarta_construtor_de_pagina(monkeypatch):
    from services import domain_finder

    monkeypatch.setattr(domain_finder, "search_multi", lambda *a, **k: [
        {"url": "https://abix.wixsite.com/site", "title": ""},
        {"url": "https://abix.lojaintegrada.com.br", "title": ""},
    ])
    monkeypatch.setattr(domain_finder, "is_public_host", lambda h: True)
    monkeypatch.setattr(domain_finder, "_confirm_on_site", lambda *a: 0.0)
    assert domain_finder.find_domain("Abix Tecnologia")["domain"] is None
