"""
Regressões de PRECISÃO do enriquecimento.

Cada teste aqui existe porque o dado errado apareceu de verdade na ficha do
lead. Dado absurdo é pior do que dado ausente: destrói a confiança do vendedor
na ferramenta inteira.
"""
from bs4 import BeautifulSoup

from services.employee_count import (
    MAX_PLAUSIBLE_EMPLOYEES, _find_in_text, fetch_employee_count,
    normalize_employee_count,
)
from services.scraper import (
    _looks_like_slogan, _pick_company_name, _pick_corporate_email, _extract_linkedin,
)
from services._utils import (
    LINKEDIN_COMPANY_RE, is_public_linkedin_slug, looks_like_search_block,
    fix_response_encoding,
)
from services.linkedin_search import (
    _confidence, _normalize, _slug_from_url, parse_company_page,
    _guess_slug_candidates, _names_match,
)
from services.providers.cnpj_receita import (
    location_from_cnpj, sector_from_cnpj, employee_band_from_cnpj,
)
from services.dns_lookup import (
    clean_asn_org, country_label, identify_provider, registrable_name,
)


# Recorte real do bloco "Informações" da página pública de uma empresa.
LINKEDIN_INFO_HTML = """
<dl>
  <dt class="font-sans">Site</dt>
  <dd class="font-sans">http://www.skynova.com.br Link externo para Skynova</dd>
  <dt class="font-sans">Setor</dt>
  <dd class="font-sans">Atividades dos serviços de tecnologia da informação</dd>
  <dt class="font-sans">Tamanho da empresa</dt>
  <dd class="font-sans">51-200 funcionários</dd>
  <dt class="font-sans">Sede</dt>
  <dd class="font-sans">São Paulo, São Paulo</dd>
  <dt class="font-sans">Fundada em</dt>
  <dd class="font-sans">2013</dd>
</dl>
"""


# ── contagem de funcionários ─────────────────────────────────────────────────

def test_id_do_linkedin_nao_vira_numero_de_funcionarios():
    """
    Bug real: a busca por "linkedin.com/company/2629565 employees" fazia o
    Bing ecoar o termo pesquisado, e o regex lia o ID da página como
    "2.629.565 funcionários" para uma empresa de ~300 pessoas.
    """
    assert normalize_employee_count("2629565+ employees") is None
    assert _find_in_text("linkedin.com/company/2629565 employees") is None


def test_numeros_plausiveis_continuam_passando():
    assert normalize_employee_count("250 funcionários")["exact"] == 250
    assert normalize_employee_count("1.001-5.000 employees")["min"] == 1001
    assert normalize_employee_count("10.000+ funcionários")["min"] == 10000
    assert _find_in_text("Visualizar todos os 12.979 funcionários")["exact"] == 12979


def test_teto_de_sanidade_perto_do_limite():
    logo_abaixo = MAX_PLAUSIBLE_EMPLOYEES - 1
    logo_acima = MAX_PLAUSIBLE_EMPLOYEES + 1
    assert normalize_employee_count(f"{logo_abaixo} employees")["exact"] == logo_abaixo
    assert normalize_employee_count(f"{logo_acima} employees") is None


def test_nome_composto_com_hifen_nao_e_cortado():
    soup = _soup("<html><head><title>Acme-Tech Sistemas</title></head></html>")
    assert _pick_company_name(soup, {}, "acmetech.com.br") == "Acme-Tech Sistemas"


# ── nome da empresa ──────────────────────────────────────────────────────────

def test_slogan_nao_vira_razao_social():
    """Bug real: o Nubank virava 'Somos incansáveis pra você não precisar ser'."""
    assert _looks_like_slogan("Somos incansáveis pra você não precisar ser")
    assert _looks_like_slogan("A melhor plataforma de gestão para sua empresa")
    assert not _looks_like_slogan("Nubank")
    assert not _looks_like_slogan("RD Station")
    assert not _looks_like_slogan("Grupo Marista")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_nome_prefere_dados_estruturados_ao_title():
    soup = _soup(
        '<html><head><meta property="og:site_name" content="Nubank"/>'
        "<title>Somos incansáveis pra você não precisar ser</title></head></html>"
    )
    assert _pick_company_name(soup, {}, "nubank.com.br") == "Nubank"


def test_nome_usa_jsonld_quando_existe():
    soup = _soup("<html><head><title>Home</title></head></html>")
    assert _pick_company_name(soup, {"name": "TOTVS S.A."}, "totvs.com") == "TOTVS S.A."


def test_nome_cai_no_dominio_quando_so_ha_slogan():
    soup = _soup(
        "<html><head><title>A melhor plataforma para a sua empresa crescer</title></head></html>"
    )
    assert _pick_company_name(soup, {}, "acme-tech.com.br") == "Acme Tech"


def test_nome_corta_sufixo_de_marketing():
    soup = _soup("<html><head><title>Acme | Gestão inteligente de frotas</title></head></html>")
    assert _pick_company_name(soup, {}, "acme.com.br") == "Acme"


# ── e-mail institucional ─────────────────────────────────────────────────────

def test_email_corporativo_prefere_caixa_de_contato_do_dominio():
    emails = {"joao@acme.com", "contato@acme.com", "sac@outrodominio.com"}
    assert _pick_corporate_email(emails, "acme.com") == "contato@acme.com"


def test_email_corporativo_ignora_noreply():
    emails = {"noreply@acme.com", "comercial@acme.com"}
    assert _pick_corporate_email(emails, "acme.com") == "comercial@acme.com"


def test_email_corporativo_sem_dominio_proprio():
    assert _pick_corporate_email({"contato@gmail.com"}, "acme.com") == "contato@gmail.com"
    assert _pick_corporate_email(set(), "acme.com") is None


# ── slug do LinkedIn da empresa ──────────────────────────────────────────────

def test_slug_do_linkedin_nao_trunca_no_e_comercial():
    """
    Bug real: a ficha da C&A (cea.com.br) mostrava "linkedin.com/company/c" —
    o regex do slug usava uma allowlist de caracteres que não incluía "&", e
    cortava a URL ali mesmo. Razão social com "&" é comum no varejo BR
    (C&A, e outras). O regex agora é uma blocklist (para nos delimitadores
    reais de URL/HTML), não uma allowlist de caracteres "esperados".
    """
    url = "https://www.linkedin.com/company/c&a_brasil/"
    assert _slug_from_url(url) == "c&a_brasil"
    assert _normalize("c&a_brasil") == "https://www.linkedin.com/company/c&a_brasil"


def test_slug_percent_encoded_e_decodificado_para_exibicao():
    """Sites que codificam o href ("%26" em vez de "&") não podem gerar um slug ilegível."""
    assert _normalize("c%26a_brasil") == "https://www.linkedin.com/company/c&a_brasil"


def test_regex_do_slug_ainda_para_no_delimitador_certo():
    """Alargar a allowlist não pode voltar a engolir path ou query string seguintes."""
    m = LINKEDIN_COMPANY_RE.search("https://www.linkedin.com/company/acme/people/?trk=x")
    assert m.group(1) == "acme"


# ── localização e setor via CNPJ (fallback quando o site não tem dados estruturados) ──

def test_localizacao_via_cnpj_junta_municipio_e_uf():
    """
    Bug real: a ficha da C&A vinha com Localização e Setor vazios. O site não
    tem JSON-LD/meta geo.* na home (comum em varejo), e esses dois campos só
    eram calculados para o registro global de Company — nunca voltavam para
    o Lead que a tela exibe.
    """
    assert location_from_cnpj({"municipio": "Barueri", "uf": "SP"}) == "Barueri, SP"
    assert location_from_cnpj({"municipio": None, "uf": "SP"}) == "SP"
    assert location_from_cnpj({}) is None


def test_localizacao_via_cnpj_corrige_caixa_alta_da_receita():
    """A Receita devolve 'BARUERI'; gritar com o vendedor na ficha lê como erro."""
    assert location_from_cnpj({"municipio": "BARUERI", "uf": "sp"}) == "Barueri, SP"


def test_setor_via_cnpj_usa_cnae():
    assert sector_from_cnpj({"cnae": "Comércio varejista de artigos do vestuário"}) == (
        "Comércio varejista de artigos do vestuário"
    )
    assert sector_from_cnpj({}) is None


# ── bloco "Informações" da página do LinkedIn ────────────────────────────────

def test_pagina_do_linkedin_entrega_setor_sede_e_porte():
    """
    Bug real: Setor e Localização vinham vazios mesmo com o LinkedIn certo na
    ficha. A página da empresa — já baixada para validar o vínculo — declara
    os dois, e nós simplesmente não líamos.
    """
    info = parse_company_page(LINKEDIN_INFO_HTML)
    assert info["sector"] == "Atividades dos serviços de tecnologia da informação"
    assert info["location"] == "São Paulo, São Paulo"
    assert info["size"] == "51-200 funcionários"


def test_site_declarado_sai_sem_o_texto_do_link():
    """O <dd> mistura a URL com o rótulo acessível do link."""
    assert parse_company_page(LINKEDIN_INFO_HTML)["website"] == "http://www.skynova.com.br"


def test_parse_de_pagina_vazia_nao_explode():
    assert parse_company_page(None) == {
        "website": None, "sector": None, "size": None, "location": None,
        "name": None,
    }


def test_site_declarado_no_linkedin_confirma_o_vinculo():
    """Se a empresa declara o próprio site no LinkedIn, não há o que duvidar."""
    assert _confidence(LINKEDIN_INFO_HTML, "skynova.com.br", "http://www.skynova.com.br") == "verified"


def test_dominio_so_citado_na_pagina_nao_e_confirmacao():
    html = "<p>parceria com acme.com.br</p>"
    assert _confidence(html, "acme.com.br", None) == "probable"
    assert _confidence(html, "outra.com.br", None) == "unverified"


def test_pagina_que_nao_respondeu_nao_conta_contra_a_empresa():
    assert _confidence(None, "acme.com.br", None) == "probable"


def test_sem_orcamento_de_tempo_ainda_le_a_pagina_em_maos():
    """
    Sem tempo para rede, o que já está baixado continua valendo — o que não
    pode é entrar em rede e estourar o limite da função serverless.
    """
    html = "<dd>Visualizar todos os 12.851 funcionários</dd>"
    exato = fetch_employee_count("https://linkedin.com/company/x", page_html=html,
                                 allow_network=False)
    assert exato["exact"] == 12851

    faixa = fetch_employee_count("https://linkedin.com/company/x",
                                 page_html="<dd>51-200 funcionários</dd>",
                                 allow_network=False)
    assert (faixa["min"], faixa["max"]) == (51, 200)
    assert faixa["source"] == "linkedin_direct"


def test_sem_orcamento_e_sem_pagina_nao_vai_para_rede():
    assert fetch_employee_count("https://linkedin.com/company/x", "https://acme.com",
                                allow_network=False) is None


# ── LinkedIn: URL que exige login vs. página pública ─────────────────────────

def test_slug_numerico_e_de_painel_nao_sao_pagina_publica():
    """
    Bug real (farmatex.com.br): a empresa colou no rodapé o link do PRÓPRIO
    PAINEL — /company/74031250/admin. O LinkedIn manda essa URL para a tela
    de login, então setor, sede e funcionários vinham vazios. O ID numérico
    sozinho falha igual: só o slug textual abre a página pública.
    """
    assert is_public_linkedin_slug("farmatex-do-brasil") is True
    assert is_public_linkedin_slug("74031250") is False
    assert is_public_linkedin_slug("admin") is False
    assert is_public_linkedin_slug("") is False


def test_scraper_ignora_link_de_painel_no_rodape():
    """
    Gravar a URL ruim era pior que não achar nada: o enricher via o campo
    preenchido e nem tentava procurar a página pública.
    """
    html = '<footer><a href="https://www.linkedin.com/company/74031250/admin">LinkedIn</a></footer>'
    assert _extract_linkedin(BeautifulSoup(html, "html.parser"), html) is None


def test_scraper_prefere_slug_publico_quando_ha_os_dois():
    html = ('<footer><a href="https://www.linkedin.com/company/74031250/admin">a</a>'
            '<a href="https://www.linkedin.com/company/farmatex-do-brasil">b</a></footer>')
    achado = _extract_linkedin(BeautifulSoup(html, "html.parser"), html)
    assert achado == "https://www.linkedin.com/company/farmatex-do-brasil"


# ── LinkedIn: adivinhação do slug (substitui o buscador bloqueado) ───────────

def test_palpites_de_slug_cobrem_os_casos_reais_medidos():
    """
    Os buscadores estão todos barrando robô, então o slug é adivinhado pelo
    nome. Estes são os acertos medidos ao vivo em empresas reais.
    """
    assert "farmatex-do-brasil" in _guess_slug_candidates("Farmatex Do Brasil", "farmatex.com.br")
    assert "magazine-luiza" in _guess_slug_candidates("Magazine Luiza", "magazineluiza.com.br")
    assert "cia-hering" in _guess_slug_candidates("Hering", "hering.com.br")
    assert "casasbahia" in _guess_slug_candidates("Casas Bahia", "casasbahia.com.br")
    # Sem nome da empresa, a raiz do domínio ainda dá um palpite utilizável
    assert "copel" in _guess_slug_candidates(None, "copel.com")


def test_palpites_nao_incluem_slug_que_exige_login_nem_repetem():
    candidatos = _guess_slug_candidates("123456", "acme.com.br")
    assert "123456" not in candidatos
    assert len(candidatos) == len(set(candidatos))


def test_nome_da_pagina_confirma_palpite_quando_o_site_e_do_grupo():
    """
    Casos reais: /company/madero diz "Grupo Madero" mas declara
    grupomadero.com.br (buscamos restaurantemadero.com.br); /company/cia-hering
    declara o site da Azzas 2154, que incorporou a Hering. São a empresa certa
    — descartar por causa do domínio perderia o lead.
    """
    assert _names_match("Grupo Madero", "Madero", "restaurantemadero.com.br") is True
    assert _names_match("Cia. Hering", "Hering", "hering.com.br") is True


def test_nome_muito_curto_nao_confirma_palpite():
    """"SA"/"Cia" dentro de outro nome aceitaria a empresa errada."""
    assert _names_match("Companhia Vale do Rio Doce", "SA", "sa.com.br") is False
    assert _names_match("Outra Empresa Qualquer", "Acme", "acme.com.br") is False
    assert _names_match(None, "Acme", "acme.com.br") is False


def test_nome_do_linkedin_sai_sem_o_sufixo_da_rede():
    html = '<meta property="og:title" content="Farmatex do Brasil | LinkedIn"/>'
    assert parse_company_page(html)["name"] == "Farmatex do Brasil"


# ── funcionários via porte da Receita ────────────────────────────────────────

def test_porte_micro_e_pequeno_viram_faixa_declarada_como_estimativa():
    micro = employee_band_from_cnpj({"porte": "MICRO EMPRESA"})
    assert "estimado" in micro["band"]
    assert micro["source"] == "cnpj_porte"
    assert employee_band_from_cnpj({"porte": "EMPRESA DE PEQUENO PORTE"})["band"]


def test_porte_demais_nao_vira_numero():
    """
    "DEMAIS" não tem teto: cabe ali uma empresa de 100 e uma de 50.000
    funcionários. Qualquer número seria invenção.
    """
    assert employee_band_from_cnpj({"porte": "DEMAIS"}) is None
    assert employee_band_from_cnpj({"porte": ""}) is None
    assert employee_band_from_cnpj({}) is None


def test_faixa_por_porte_nunca_preenche_min_max_ou_exact():
    """
    A tela e o Excel mostram exact/min/max como número puro, sem a ressalva
    de estimativa — uma faixa fiscal ali viraria headcount real aos olhos do
    vendedor.
    """
    banda = employee_band_from_cnpj({"porte": "Microempresa"})
    assert banda["min"] is None and banda["max"] is None and banda["exact"] is None


# ── buscador bloqueado ───────────────────────────────────────────────────────

def test_pagina_de_bloqueio_de_buscador_nao_vira_resultado():
    assert looks_like_search_block("<html><body>captcha</body></html>") is True
    assert looks_like_search_block("") is True
    assert looks_like_search_block("<html>" + ("texto de resultado real " * 400) + "</html>") is False


# ── charset: acento corrompido silenciosamente ──────────────────────────────

class _FakeResp:
    def __init__(self, headers, encoding, apparent):
        self.headers = headers
        self.encoding = encoding
        self.apparent_encoding = apparent


def test_site_sem_charset_declarado_usa_o_encoding_detectado():
    """
    Bug real (ondunorte.com.br): o servidor manda "text/html" sem charset, o
    requests assume ISO-8859-1 por especificação e a página é UTF-8 — todo
    acento é decodificado errado sem erro nenhum. O nome da empresa, a
    descrição, o setor e a cidade chegam com lixo na ficha do vendedor.
    """
    resp = _FakeResp({"content-type": "text/html"}, "ISO-8859-1", "utf-8")
    fix_response_encoding(resp)
    assert resp.encoding == "utf-8"


def test_charset_declarado_pelo_servidor_e_respeitado():
    """Quem declara o charset sabe o que está servindo — não sobrescrever."""
    resp = _FakeResp({"content-type": "text/html; charset=ISO-8859-1"}, "ISO-8859-1", "utf-8")
    fix_response_encoding(resp)
    assert resp.encoding == "ISO-8859-1"


# ── provedor de e-mail a partir do MX ────────────────────────────────────────

def test_provedor_de_email_nao_vira_sufixo_do_dominio():
    """
    Bug real (bruc.com.br): o MX 'mx-ha.skymail.net.br' caía no fallback, que
    pegava os dois últimos rótulos e devolvia "Net.Br" — um sufixo público
    apresentado ao vendedor como se fosse o provedor de e-mail.
    """
    assert registrable_name("mx-ha.skymail.net.br") == "Skymail"
    assert identify_provider("mx-ha.skymail.net.br", None) == ("Skymail", "low")


def test_provedor_conhecido_continua_vindo_do_hostname():
    assert identify_provider("aspmx.l.google.com", None) == ("Google Workspace", "high")
    assert identify_provider("empresa.mail.protection.outlook.com", None) == ("Microsoft 365", "high")


def test_fallback_de_dominio_simples_usa_o_proprio_nome():
    assert registrable_name("mx1.exemplo.com") == "Exemplo"
    assert registrable_name("localhost") is None


def test_organizacao_do_asn_perde_prefixo_e_sufixo_de_pais():
    """A string crua do RDAP serve na tabela técnica; no campo "Hosting" da
    ficha, "AS265262 - ..., BR" só atrapalha a leitura."""
    assert clean_asn_org("AS265262 - Skymail Servicos de Computacao, BR") == "Skymail Servicos de Computacao"
    assert clean_asn_org("GOOGLE - Google LLC, US") == "GOOGLE - Google LLC"
    assert clean_asn_org(None) is None


def test_pais_do_asn_sai_por_extenso_em_portugues():
    assert country_label("BR") == "Brasil"
    assert country_label("us") == "Estados Unidos"
    assert country_label("ZZ") == "ZZ"       # desconhecido volta como veio
    assert country_label(None) is None
