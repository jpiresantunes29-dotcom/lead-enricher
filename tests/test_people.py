"""Testes da camada de Contact Intelligence (identity, padrões, CNPJ, LGPD)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models.database import Base
from services.people import email_patterns as ep
from services.people import identity, optout, repository as repo
from services.providers import cnpj_receita


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# ── identity ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("João da Silva", ("joao", "silva")),
    ("Maria Gonçalves de Souza", ("maria", "souza")),
    ("Ana (She/Her) Costa 🚀", ("ana", "costa")),
    ("Pedro Álvares Cabral, MBA", ("pedro", "cabral")),
    ("Madonna", ("madonna", None)),
])
def test_split_name(raw, expected):
    assert identity.split_name(raw) == expected


def test_clean_name_remove_ruido_do_linkedin():
    assert identity.clean_name("Carla Dias · 2nd") == "Carla Dias"
    assert identity.clean_name("Rafael Lima | Growth") .startswith("Rafael Lima")


@pytest.mark.parametrize("title,seniority", [
    ("CEO", "founder"),      # CEO cai em founder/c_level — ambos são decisão máxima
    ("Diretor Comercial", "director"),
    ("Head of Engineering", "head"),
    ("Gerente de TI", "manager"),
    ("Analista de Marketing", "other"),
])
def test_title_seniority(title, seniority):
    result = identity.title_seniority(title)
    if title == "CEO":
        assert result in ("founder", "c_level")
    else:
        assert result == seniority


@pytest.mark.parametrize("title,dept", [
    ("Diretor de Tecnologia", "tech"),
    ("Gerente Comercial", "sales"),
    ("CFO", "finance"),
    ("Head de RH", "hr"),
])
def test_title_department(title, dept):
    assert identity.title_department(title) == dept


def test_parse_headline():
    assert identity.parse_headline("CTO at Acme Tecnologia") == ("CTO", "Acme Tecnologia")
    assert identity.parse_headline("Diretor Comercial na Acme") == ("Diretor Comercial", "Acme")


def test_dedupe_key_estavel_e_distinto():
    a = identity.dedupe_key("joao-silva", "João Silva", "acme.com")
    b = identity.dedupe_key(None, "João Silva", "acme.com")
    c = identity.dedupe_key(None, "João Silva", "outra.com")
    assert a == "li:joao-silva"
    assert b != c
    assert identity.dedupe_key(None, "João Silva", None) is None


# ── padrões de e-mail ────────────────────────────────────────────────────────

@pytest.mark.parametrize("email,name,pattern", [
    ("joao.silva@acme.com", "João da Silva", "{first}.{last}"),
    ("jsilva@acme.com", "João Silva", "{f}{last}"),
    ("joaosilva@acme.com", "João Silva", "{first}{last}"),
    ("joao@acme.com", "João Silva", "{first}"),
    ("silva.joao@acme.com", "João Silva", "{last}.{first}"),
])
def test_infer_pattern(email, name, pattern):
    assert ep.infer_pattern(email, name) == pattern


def test_email_generico_nao_ensina_padrao(db):
    assert ep.is_generic("contato@acme.com")
    assert ep.is_generic("vendas2@acme.com")
    assert not ep.is_generic("joao.silva@acme.com")
    assert ep.learn_from_email(db, "acme.com", "contato@acme.com", "Contato Acme") is None


def test_aprende_padrao_e_usa_para_o_dominio_inteiro(db):
    ep.learn_from_email(db, "acme.com", "joao.silva@acme.com", "João Silva", source="smtp")
    ep.learn_from_email(db, "acme.com", "maria.souza@acme.com", "Maria Souza", source="smtp")
    db.flush()

    row = ep.get_pattern(db, "acme.com")
    assert row.pattern == "{first}.{last}"
    assert row.confidence >= 80

    # Terceira pessoa da MESMA empresa: palpite de alta confiança, custo zero
    candidates = ep.candidates(db, "acme.com", "Carlos Pereira")
    assert candidates[0]["email"] == "carlos.pereira@acme.com"
    assert candidates[0]["source"] == "pattern"
    assert candidates[0]["confidence"] >= 80


def test_dominio_desconhecido_usa_ranking_de_mercado(db):
    candidates = ep.candidates(db, "novaempresa.com.br", "Ana Lima")
    assert candidates[0]["email"] == "ana.lima@novaempresa.com.br"
    assert candidates[0]["source"] == "common"
    # Sem evidência, a confiança tem de ser baixa — é palpite, não fato
    assert candidates[0]["confidence"] < 60


def test_amostras_conflitantes_derrubam_a_confianca(db):
    ep.learn_from_email(db, "mix.com", "joao.silva@mix.com", "João Silva")
    ep.learn_from_email(db, "mix.com", "msouza@mix.com", "Maria Souza")
    db.flush()
    assert ep.get_pattern(db, "mix.com").confidence <= 72


def test_catch_all_limita_confianca(db):
    ep.learn_from_email(db, "cat.com", "joao.silva@cat.com", "João Silva")
    ep.learn_from_email(db, "cat.com", "maria.souza@cat.com", "Maria Souza")
    ep.learn_from_email(db, "cat.com", "ana.lima@cat.com", "Ana Lima")
    ep.record_domain_health(db, "cat.com", catch_all=True)
    db.flush()
    assert ep.candidates(db, "cat.com", "Pedro Rocha")[0]["confidence"] <= 70


# ── CNPJ / Receita ───────────────────────────────────────────────────────────

def test_valida_cnpj():
    assert cnpj_receita.is_valid_cnpj("11.222.333/0001-81")
    assert not cnpj_receita.is_valid_cnpj("11.222.333/0001-80")
    assert not cnpj_receita.is_valid_cnpj("11111111111111")
    assert not cnpj_receita.is_valid_cnpj("123")


def test_extrai_cnpj_de_rodape():
    texto = "Acme Ltda · CNPJ 11.222.333/0001-81 · Todos os direitos reservados"
    assert cnpj_receita.extract_cnpj(texto) == "11222333000181"
    assert cnpj_receita.extract_cnpj("CNPJ 00.000.000/0000-00") is None


def test_qsa_vira_decisores_sem_pessoa_juridica():
    data = {"qsa": [
        {"nome": "JOAO DA SILVA", "qualificacao": "Sócio-Administrador"},
        {"nome": "ACME PARTICIPACOES LTDA", "qualificacao": "Sócio"},
        {"nome": "MARIA SOUZA", "qualificacao": "Diretor"},
    ]}
    out = cnpj_receita.decision_makers_from_qsa(data)
    nomes = [d["name"] for d in out]
    assert "Joao Da Silva" in nomes
    assert "Maria Souza" in nomes
    assert not any("PARTICIPACOES" in n.upper() for n in nomes)


# ── LGPD / opt-out ───────────────────────────────────────────────────────────

def test_optout_bloqueia_independente_de_formatacao(db):
    optout.register(db, "email", "  Joao.Silva@ACME.com  ")
    db.flush()
    assert optout.is_blocked(db, "email", "joao.silva@acme.com")
    assert not optout.is_blocked(db, "email", "outro@acme.com")


def test_optout_telefone_ignora_mascara(db):
    optout.register(db, "phone", "+55 11 98888-7777")
    db.flush()
    assert optout.is_blocked(db, "phone", "5511988887777")


def test_optout_guarda_apenas_hash(db):
    from models.database import OptOut
    optout.register(db, "email", "pessoa@empresa.com", reason="não quero")
    db.flush()
    row = db.query(OptOut).first()
    assert "pessoa@empresa.com" not in (row.value_hash or "")
    assert len(row.value_hash) == 64


def test_filter_contacts_remove_bloqueados(db):
    optout.register(db, "email", "bloqueado@acme.com")
    db.flush()
    result = optout.filter_contacts(
        db, emails=["bloqueado@acme.com", "livre@acme.com"], phones=[],
    )
    assert result["emails"] == ["livre@acme.com"]
    assert result["person_blocked"] is False


def test_pessoa_bloqueada_esconde_tudo(db):
    optout.register(db, "linkedin", "https://www.linkedin.com/in/joao-silva")
    db.flush()
    result = optout.filter_contacts(
        db, emails=["joao@acme.com"], phones=["+5511999999999"], linkedin="joao-silva",
    )
    assert result["person_blocked"] is True
    assert result["emails"] == [] and result["phones"] == []


def test_contato_bloqueado_nao_e_regravado(db):
    """
    Regressão de LGPD: o palpite do padrão regravava um e-mail que a pessoa
    tinha pedido para remover. A página pública promete remoção definitiva.
    """
    person = repo.upsert_person(db, full_name="João Silva", slug="joao-silva",
                                company_domain="acme.com")
    optout.register(db, "email", "joao.silva@acme.com")
    optout.register(db, "phone", "+5511999998888")
    db.flush()

    assert repo.add_email(db, person, "joao.silva@acme.com", status="valid", confidence=97) is None
    assert repo.add_phone(db, person, "+5511999998888", confidence=80) is None
    # Outro contato da mesma pessoa continua funcionando normalmente
    assert repo.add_email(db, person, "j.silva@acme.com", confidence=70) is not None

    db.flush()
    assert [e.email for e in person.emails] == ["j.silva@acme.com"]


def test_purge_remove_contato_da_base(db):
    company = repo.upsert_company(db, "acme.com", name="Acme")
    person = repo.upsert_person(db, full_name="João Silva", slug="joao-silva",
                                company_domain="acme.com", company=company)
    repo.add_email(db, person, "joao.silva@acme.com", status="valid", confidence=97)
    db.flush()

    optout.register(db, "email", "joao.silva@acme.com")
    removed = optout.purge(db, "email", "joao.silva@acme.com")
    db.flush()
    assert removed == 1


# ── repositório ──────────────────────────────────────────────────────────────

def test_nao_rebaixa_email_verificado(db):
    person = repo.upsert_person(db, full_name="João Silva", slug="joao-silva",
                                company_domain="acme.com")
    repo.add_email(db, person, "joao@acme.com", status="valid", confidence=97, source="smtp")
    db.flush()
    # Uma rodada seguinte sem rede não pode derrubar o que já foi confirmado
    repo.add_email(db, person, "joao@acme.com", status="unknown", confidence=40, source="pattern")
    db.flush()
    row = repo.best_email(person)
    assert row.status == "valid"
    assert row.confidence == 97


def test_upsert_person_deduplica_por_slug(db):
    a = repo.upsert_person(db, full_name="João Silva", slug="joao-silva", company_domain="acme.com")
    db.flush()
    b = repo.upsert_person(db, full_name="Joao Silva", slug="joao-silva",
                           company_domain="acme.com", title="CTO")
    db.flush()
    assert a.id == b.id
    assert b.title == "CTO"
    assert b.seniority in ("c_level", "founder")


def test_pessoa_sem_identidade_nao_e_gravada(db):
    assert repo.upsert_person(db, full_name="Fulano") is None
