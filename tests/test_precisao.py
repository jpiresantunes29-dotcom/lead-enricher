"""
Regressões de PRECISÃO do enriquecimento.

Cada teste aqui existe porque o dado errado apareceu de verdade na ficha do
lead. Dado absurdo é pior do que dado ausente: destrói a confiança do vendedor
na ferramenta inteira.
"""
from bs4 import BeautifulSoup

from services.employee_count import (
    MAX_PLAUSIBLE_EMPLOYEES, _find_in_text, fetch_employee_count,
    normalize_employee_count, _count_from_site_page,
)
from services.scraper import (
    _looks_like_slogan, _pick_company_name, _pick_corporate_email, _extract_linkedin,
    _links_institucionais,    _localizacao_plausivel,
)
from services._utils import (
    LINKEDIN_PAGE_RE, is_public_linkedin_slug, looks_like_search_block,
    fix_response_encoding, domain_mentioned, linkedin_ref,
)
from services.linkedin_search import (
    _confidence, _normalize, _slug_from_url, parse_company_page,
    _guess_slug_candidates, _names_match, _extract_candidates_from_html,
    _pick_best_candidate, _tipos_de_pagina,    _palpite_utilizavel,
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


def test_opcao_de_formulario_nao_vira_contagem_de_funcionarios():
    """
    Bug real (pucpr.br): a ficha da universidade dizia "19 colaboradores".
    O número saía do `<option>` "Microempresa (até 19 colaboradores)" do
    formulário de contato — o coletor lia a PERGUNTA do site como se fosse uma
    resposta sobre ele.
    """
    html = """
    <h1>PUCPR para Empresas</h1>
    <form><label>Porte da empresa</label>
      <select>
        <option value="">Selecione</option>
        <option>Microempresa (até 19 colaboradores)</option>
        <option>Pequeno porte (de 20 a 99 colaboradores)</option>
      </select>
    </form>
    """
    assert _count_from_site_page(html) is None


def test_classificacao_de_porte_em_prosa_tambem_e_recusada():
    """Fora do formulário, "até N colaboradores" continua sendo faixa."""
    html = "<p>Atendemos microempresas com até 19 colaboradores.</p>"
    assert _count_from_site_page(html) is None


def test_numero_declarado_pela_empresa_tem_prioridade():
    """`numberOfEmployees` do JSON-LD é a empresa declarando o próprio tamanho."""
    html = """
    <script type="application/ld+json">
    {"@type": "Organization", "name": "Acme", "numberOfEmployees": 4200}
    </script>
    <p>Somos uma equipe enxuta de 12 colaboradores no marketing.</p>
    """
    assert _count_from_site_page(html)["exact"] == 4200


def test_contagem_institucional_legitima_continua_passando():
    """Endurecer não pode cegar a fonte: o texto real da empresa ainda vale."""
    html = "<main><p>Somos mais de 3.500 colaboradores em todo o Brasil.</p></main>"
    assert _count_from_site_page(html)["exact"] == 3500


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
    m = LINKEDIN_PAGE_RE.search("https://www.linkedin.com/company/acme/people/?trk=x")
    assert m.group("slug") == "acme"


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


def test_pagina_que_nao_respondeu_vale_pelo_que_a_empresa_declarou():
    """
    Sem página, o que sustenta a afirmação é a origem do link.

    Se a própria empresa publicou o perfil no site dela, o LinkedIn fora do ar
    não desmente nada — segue "probable". Mas um palpite de slug ou um
    resultado de buscador sem página não tem prova nenhuma, e mostrar isso na
    ficha é como o perfil errado chegava à tela do vendedor.
    """
    assert _confidence(None, "acme.com.br", None, declarado_pelo_site=True) == "probable"
    assert _confidence(None, "acme.com.br", None) == "unverified"


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


# ── LinkedIn de universidade: /school/ e a sub-marca que se passava por ela ──
#
# Caso real e completo (pucpr.br). A home publica UM link do LinkedIn,
# /school/pontificia-universidade-catolica-do-parana, e a ficha saía com
# /company/hotmilk-pucpr — o hub de inovação da universidade. Três defeitos
# em série produziam isso, e cada teste abaixo tranca um deles.

def test_link_de_escola_no_site_e_reconhecido():
    """
    Defeito 1: só /company/ era enxergado. O link certo da PUCPR estava na
    home, em primeiro lugar na hierarquia de confiança, e era descartado —
    o que empurrava a busca para páginas internas, onde estava a sub-marca.
    """
    html = ('<footer><a href="https://www.linkedin.com/school/'
            'pontificia-universidade-catolica-do-parana">LinkedIn</a></footer>')
    assert _extract_linkedin(BeautifulSoup(html, "html.parser"), html) == (
        "https://www.linkedin.com/school/pontificia-universidade-catolica-do-parana"
    )
    assert linkedin_ref("https://www.linkedin.com/school/pucproficial/") == (
        "school", "pucproficial"
    )


def test_caminho_da_escola_nao_e_reescrito_como_empresa():
    """/school/<slug> e /company/<slug> são páginas diferentes; trocar dá 404."""
    assert _normalize("pucproficial", "school") == "https://www.linkedin.com/school/pucproficial"
    assert _normalize("acme") == "https://www.linkedin.com/company/acme"


def test_dominio_citado_nao_casa_com_subdominio_de_outra_entidade():
    """
    Defeito 2, a raiz do falso-positivo: `"pucpr.br" in html` casava com
    `hotmilk.pucpr.br`. Num HTML de 330 KB, quase tudo passava.
    """
    assert domain_mentioned("contato@pucpr.br", "pucpr.br") is True
    assert domain_mentioned("https://hotmilk.pucpr.br/", "pucpr.br") is False
    assert domain_mentioned("visite pucpr.br.uk hoje", "pucpr.br") is False
    # Ponto de fim de frase não pode ser confundido com domínio maior
    assert domain_mentioned("Acesse pucpr.br.", "pucpr.br") is True
    assert domain_mentioned("pucpr.br/contato", "pucpr.br") is True


def test_site_declarado_diferente_e_decidido_pelo_nome():
    """
    Endereços declarados medidos ao vivo nas três páginas reais:

        hotmilk.pucpr.br             buscando pucpr.br       → outra entidade
        international.nubank.com.br  buscando nubank.com.br  → a mesma empresa
        totvs.com                    buscando totvs.com.br   → a mesma empresa

    Os dois primeiros são subdomínios do domínio buscado e têm exatamente a
    mesma forma, então nenhuma regra sobre o formato do domínio separa um do
    outro. Só o nome da página separa — e rejeitar pela forma foi uma
    regressão real: derrubou TOTVS e Nubank junto com o hub.
    """
    html = "<p>conteudo</p>"
    assert _confidence(html, "pucpr.br", "https://hotmilk.pucpr.br/",
                       page_name="HOTMILK | Ecossistema de Inovação PUCPR") == "unverified"
    assert _confidence(html, "nubank.com.br", "https://international.nubank.com.br/about/",
                       page_name="Nubank") == "probable"
    assert _confidence(html, "totvs.com.br", "https://www.totvs.com",
                       page_name="TOTVS") == "probable"


def test_sub_marca_nao_confirma_a_instituicao():
    """
    Defeito 3: conter o nome não é ser a empresa. "Grupo Madero" só acrescenta
    um qualificador; "Hotmilk ... PUCPR" acrescenta uma marca própria.
    """
    assert _names_match("HOTMILK | Ecossistema de Inovação PUCPR", "PUCPR", "pucpr.br") is False
    # O que já funcionava continua funcionando
    assert _names_match("Grupo Madero", "Madero", "restaurantemadero.com.br") is True
    assert _names_match("Cia. Hering", "Hering", "hering.com.br") is True


def test_universidade_prefere_a_pagina_de_escola():
    """
    A semelhança do slug puxa para o lado errado justamente aqui:
    "hotmilk-pucpr" parece MAIS com "pucpr" do que o nome por extenso da
    universidade. O tipo de página precisa desempatar.
    """
    assert _tipos_de_pagina("pucpr.br", "Pontifícia Universidade Católica do Paraná")[0] == "school"
    assert _tipos_de_pagina("acme.com.br", "Acme Ltda") == ("company",)

    html = ('<footer>'
            '<a href="https://www.linkedin.com/company/hotmilk-pucpr">a</a>'
            '<a href="https://www.linkedin.com/school/pontificia-universidade-catolica-do-parana">b</a>'
            '</footer>')
    candidatos = _extract_candidates_from_html(html, BeautifulSoup(html, "html.parser"))
    escolhido = _pick_best_candidate(candidatos, "pucpr.br", "Universidade Católica do Paraná")
    assert escolhido == (
        "https://www.linkedin.com/school/pontificia-universidade-catolica-do-parana"
    )

# ── CNPJ: a porta para localização e setor oficiais ─────────────────────────

def test_cnpj_do_titular_do_dominio_e_validado():
    """
    O registro.br publica o CNPJ do titular de qualquer domínio .br — é o que
    dá localização e setor oficiais a sites que não publicam o próprio CNPJ
    (a maioria fora do varejo). Campo de texto livre, então os verificadores
    decidem: um CPF de titular pessoa física cairia aqui do mesmo jeito.
    """
    from services.enricher import _cnpj_do_titular

    assert _cnpj_do_titular({"owner_cnpj": "76.659.820/0003-13"}) == "76659820000313"
    assert _cnpj_do_titular({"owner_cnpj": "11.111.111/1111-11"}) == ""
    assert _cnpj_do_titular({"owner_cnpj": "123.456.789-00"}) == ""
    assert _cnpj_do_titular({}) == ""
    assert _cnpj_do_titular(None) == ""


def test_links_institucionais_saem_do_proprio_site_e_so_do_mesmo_dominio():
    """
    Adivinhar /sobre e /contato só acha o site que segue a convenção: medido ao
    vivo, nenhum caminho adivinhado existe em pucpr.br, e o CNPJ do Madero mora
    em /pt/politica. Link para fora fica de fora — levaria ao CNPJ da agência
    ou do gateway de pagamento, gravando a ficha do lead errado.
    """
    html = """
    <a href="/pt/politica">Política de Privacidade</a>
    <a href="/a-universidade/sobre-a-pucpr/">Sobre a PUCPR</a>
    <a href="https://outrodominio.com.br/termos">Termos do parceiro</a>
    <a href="/produtos">Produtos</a>
    """
    achados = _links_institucionais("https://acme.com.br", BeautifulSoup(html, "html.parser"),
                                    "acme.com.br")
    assert "https://acme.com.br/pt/politica" in achados
    assert "https://acme.com.br/a-universidade/sobre-a-pucpr/" in achados
    assert not any("outrodominio" in u for u in achados)
    assert not any("/produtos" in u for u in achados)


def _stub_coleta(monkeypatch, *, site, rdap=None, cnpj_data=None, page=None):
    """Isola o enricher da rede para testar só a precedência entre as fontes."""
    from services import enricher

    monkeypatch.setattr(enricher, "scrape_website", lambda url: site)
    monkeypatch.setattr(enricher, "get_dns_report", lambda d: None)
    monkeypatch.setattr(enricher, "fetch_rdap", lambda d: rdap)
    monkeypatch.setattr(enricher, "fetch_employee_count", lambda *a, **k: None)
    monkeypatch.setattr(enricher, "lookup_cnpj", lambda c, timeout=6: cnpj_data)
    monkeypatch.setattr(
        enricher, "find_company_linkedin",
        lambda d, n: {"url": None, "confidence": "none", "source": None, "page": page},
    )
    return enricher


_SITE_COM_GEO_LIXO = {
    "status": "enriched", "company_name": "Boticario", "sector": None,
    # Metatag geo.placename real do boticario.com.br: resquício de template
    "location": "г.Симферополь, АР Крым",
    "cnpj": None, "linkedin_url": None, "description": None,
    "emails": [], "phones": [], "block_reason": None,
}


def test_metatag_geo_do_site_nao_ganha_da_sede_oficial(monkeypatch):
    """
    Bug real: a ficha do Boticário mostrava "г.Симферополь, АР Крым"
    (Simferopol, Crimeia) como localização. Sai da metatag geo.* que o site
    herdou do template e nunca corrigiu — e, por vir do site, ganhava da sede
    que a Receita e o LinkedIn informam certo.
    """
    enricher = _stub_coleta(
        monkeypatch, site=dict(_SITE_COM_GEO_LIXO),
        rdap={"owner_cnpj": "11.137.051/0719-54"},
        cnpj_data={"municipio": "SAO JOSE DOS PINHAIS", "uf": "PR",
                   "cnae": "Fabricação de cosméticos"},
    )
    resultado = enricher.enrich_company("boticario.com.br")

    assert resultado["location"] == "Sao Jose Dos Pinhais, PR"
    assert resultado["sector"] == "Fabricação de cosméticos"
    # O CNPJ do titular do domínio abriu os dois campos
    assert resultado["cnpj"] == "11137051071954"


def test_metatag_geo_ainda_vale_quando_nao_ha_fonte_melhor(monkeypatch):
    """Rebaixar a metatag não pode cegá-la: vazio seria pior."""
    enricher = _stub_coleta(
        monkeypatch,
        site={**_SITE_COM_GEO_LIXO, "location": "Curitiba, PR"},
        rdap=None, cnpj_data=None,
    )
    assert enricher.enrich_company("acme.com.br")["location"] == "Curitiba, PR"


def test_cnpj_publicado_no_site_prevalece_sobre_o_do_titular(monkeypatch):
    """
    O titular do domínio é quase sempre a empresa, mas pode ser a holding do
    grupo ou a agência que registrou. Com os dois em mãos, o que a empresa
    publica sobre si mesma vale mais.
    """
    enricher = _stub_coleta(
        monkeypatch,
        site={**_SITE_COM_GEO_LIXO, "cnpj": "76484013000145"},
        rdap={"owner_cnpj": "11.137.051/0719-54"},
        cnpj_data={"municipio": "CURITIBA", "uf": "PR", "cnae": "Saneamento"},
    )
    assert enricher.enrich_company("sanepar.com.br")["cnpj"] == "76484013000145"


def test_localizacao_em_alfabeto_nao_latino_e_lixo_de_template():
    """
    boticario.com.br declara "г.Симферополь, АР Крым" (Simferopol, Crimeia) na
    metatag geo.* — sobra do tema que originou o site. Não é fonte ambígua, é
    lixo, e não deve entrar na ficha nem como último recurso. Acento não pode
    ser confundido com isso.
    """
    assert _localizacao_plausivel("г.Симферополь, АР Крым") is False
    assert _localizacao_plausivel("东京") is False
    assert _localizacao_plausivel("São Paulo") is True
    assert _localizacao_plausivel("Paraná") is True
    assert _localizacao_plausivel("Curitiba, PR") is True
    assert _localizacao_plausivel(None) is False


def test_json_ld_com_endereco_de_template_nao_vira_localizacao(monkeypatch):
    """
    Em boticario.com.br o "г.Симферополь, АР Крым" não vem da metatag geo.*:
    vem do JSON-LD (addressLocality), que é a PRIMEIRA fonte de localização —
    o template foi copiado inteiro, dados estruturados junto. Por isso a
    guarda tem de estar na saída da coleta, não só na metatag.
    """
    from services import scraper

    html = """
    <html><head><title>Boticario</title>
    <script type="application/ld+json">
    {"@type": "Organization", "name": "Boticario",
     "address": {"addressLocality": "г.Симферополь", "addressRegion": "АР Крым"}}
    </script></head><body><p>site</p></body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    monkeypatch.setattr(scraper, "_fetch_com_motivo", lambda url, timeout=None: ((soup, html), None))
    monkeypatch.setattr(scraper, "_fetch", lambda url, timeout=None: None)
    monkeypatch.setattr(scraper, "_cnpj_seguindo_links_do_site", lambda *a, **k: None)

    assert scraper.scrape_website("https://boticario.com.br")["location"] is None


def test_site_fora_do_ar_nao_gera_palpite_que_e_so_prefixo():
    """
    Bug real, e o mais perigoso da série: com boticario.com.br devolvendo 403,
    o nome da empresa chegava vazio e os prefixos eram colados em NADA — saíam
    os palpites "o-", "cia-", "grupo-" e "-brasil". E linkedin.com/company/o-
    existe: é a "ООО Мануфактура Дом Природы", de Simferopol. Era daí que vinha
    a sede na Crimeia na ficha do Boticário.
    """
    palpites = _guess_slug_candidates(None, "boticario.com.br")

    assert "o-" not in palpites
    assert "cia-" not in palpites
    assert "grupo-" not in palpites
    assert "-brasil" not in palpites
    # A raiz do domínio ainda sustenta os palpites bons
    assert "boticario" in palpites
    assert "grupo-boticario" in palpites

    assert _palpite_utilizavel("o-") is False
    assert _palpite_utilizavel("-do-brasil") is False
    assert _palpite_utilizavel("cia-hering") is True
    assert _palpite_utilizavel("farmatex-do-brasil") is True


def test_nome_de_pagina_curto_demais_nao_confirma_empresa():
    """
    A segunda trava do mesmo caso: "ООО Мануфактура Дом Природы" vira a chave
    "o" — o único caractere latino do nome — e "o" está dentro de "boticario".
    Uma letra não prova identidade; o piso é o mesmo já exigido do nome buscado.
    """
    assert _names_match("ООО Мануфактура Дом Природы", None, "boticario.com.br") is False
    assert _names_match("東京", "Acme", "acme.com.br") is False
    # Nomes de verdade seguem passando
    assert _names_match("Grupo Boticário", None, "boticario.com.br") is True


def test_localizacao_toda_em_caixa_baixa_ganha_capitalizacao(monkeypatch):
    """
    "curitiba, parana" está certo e lê como descuido. Sigla de UF não pode ser
    estragada no caminho: "Curitiba, PR" tem de sair intacto.
    """
    enricher = _stub_coleta(
        monkeypatch,
        site={**_SITE_COM_GEO_LIXO, "location": "curitiba, parana"},
        rdap=None, cnpj_data=None,
    )
    assert enricher.enrich_company("acme.com.br")["location"] == "Curitiba, Parana"

    enricher = _stub_coleta(
        monkeypatch, site=dict(_SITE_COM_GEO_LIXO),
        rdap={"owner_cnpj": "76.484.013/0001-45"},
        cnpj_data={"municipio": "CURITIBA", "uf": "PR", "cnae": "Saneamento"},
    )
    assert enricher.enrich_company("sanepar.com.br")["location"] == "Curitiba, PR"


# ── setor: qual fonte descreve o negócio ────────────────────────────────────

def test_rotulo_administrativo_e_reconhecido_como_generico():
    from services.enricher import setor_generico

    assert setor_generico("Atividades de sedes de empresas e unidades administrativas")
    assert setor_generico("Gestão de ativos intangíveis não-financeiros")
    assert setor_generico("Serviços combinados de escritório e apoio administrativo")
    assert not setor_generico("Ensino médio")
    assert not setor_generico("Restaurantes e outros serviços de alimentação")
    assert not setor_generico(None)
    # Fica de fora de propósito: é exato para uma empresa de facilities, e
    # cegar o rótulo tiraria o setor certo de quem vive dele.
    assert not setor_generico("Serviços combinados para apoio a edifícios")


def test_setor_generico_cede_a_vez_para_o_proximo_candidato():
    """
    A escolha não é por ordem fixa: o primeiro rótulo que descreve o negócio
    ganha, seja qual for a fonte que o trouxe.
    """
    from services.enricher import _melhor_setor

    assert _melhor_setor("Holdings de instituições não-financeiras",
                         "Fabricação de cosméticos") == "Fabricação de cosméticos"
    assert _melhor_setor("Ensino médio", "Serviços combinados para apoio a edifícios") ==         "Ensino médio"


def test_setor_generico_ainda_e_melhor_que_campo_vazio():
    """Escolher entre fontes não é apagar o campo quando só há uma."""
    from services.enricher import _melhor_setor

    assert _melhor_setor(None, None, "Atividades de sedes de empresas") ==         "Atividades de sedes de empresas"
    assert _melhor_setor(None, None, None) is None


def test_cnae_generico_perde_para_o_setor_do_linkedin(monkeypatch):
    """O caso que o pedido descreve, ponta a ponta."""
    from services import enricher

    pagina = {"confidence": "verified", "sector": "Tecnologia da informação",
              "location": None, "size": None, "name": "Acme", "html": None}
    enricher_mod = _stub_coleta(
        monkeypatch, site=dict(_SITE_COM_GEO_LIXO),
        rdap={"owner_cnpj": "76.484.013/0001-45"},
        cnpj_data={"municipio": "CURITIBA", "uf": "PR",
                   "cnae": "Holdings de instituições não-financeiras"},
        page=pagina,
    )
    monkeypatch.setattr(
        enricher_mod, "find_company_linkedin",
        lambda d, n: {"url": "https://www.linkedin.com/company/acme",
                      "confidence": "verified", "source": "site", "page": pagina},
    )
    assert enricher_mod.enrich_company("acme.com.br")["sector"] == "Tecnologia da informação"


def _setor_resolvido(monkeypatch, *, cnae, linkedin):
    """Roda a coleta inteira com as duas fontes fixadas, e devolve o setor."""
    from services import enricher

    pagina = {"confidence": "verified", "sector": linkedin, "location": None,
              "size": None, "name": "Empresa", "html": None}
    mod = _stub_coleta(
        monkeypatch, site=dict(_SITE_COM_GEO_LIXO),
        rdap={"owner_cnpj": "76.484.013/0001-45"},
        cnpj_data={"municipio": "CURITIBA", "uf": "PR", "cnae": cnae},
        page=pagina,
    )
    monkeypatch.setattr(
        mod, "find_company_linkedin",
        lambda d, n: {"url": "https://www.linkedin.com/company/x",
                      "confidence": "verified", "source": "site", "page": pagina},
    )
    return mod.enrich_company("acme.com.br")["sector"]


def test_setor_sai_do_cnae_e_nao_do_rotulo_do_linkedin(monkeypatch):
    """
    O CNAE é a atividade que a empresa REGISTRA para operar. O "setor" do
    LinkedIn é escolhido a dedo numa lista curta por quem montou o perfil, e
    erra muito — medido ao vivo, o Grupo Positivo aparece como "apoio a
    edifícios" e a Unimed Curitiba como "bem-estar e condicionamento físico".
    """
    assert _setor_resolvido(
        monkeypatch, cnae="Ensino médio",
        linkedin="Serviços combinados para apoio a edifícios") == "Ensino médio"

    assert _setor_resolvido(
        monkeypatch, cnae="Planos de saúde",
        linkedin="Atividades de bem-estar e condicionamento físico") == "Planos de saúde"


def test_cnae_administrativo_devolve_a_vez_ao_linkedin(monkeypatch):
    """
    A outra trava, sem a qual preferir o CNAE quebraria os casos em que ele é
    que é o rótulo inútil: Boticário e Nubank têm CNAE de holding.
    """
    assert _setor_resolvido(
        monkeypatch, cnae="Gestão de ativos intangíveis não-financeiros",
        linkedin="Fabricação de cosméticos") == "Fabricação de cosméticos"

    assert _setor_resolvido(
        monkeypatch,
        cnae="Outras atividades auxiliares dos serviços financeiros não especificadas anteriormente",
        linkedin="Atividades de serviços financeiros") == "Atividades de serviços financeiros"
