"""
As migrações precisam descrever o mesmo schema que os modelos.

Sem este teste, a divergência só aparece no deploy: a aplicação espera uma
coluna que a migração nunca criou, e o erro chega como 500 no clique do
usuário. Aqui um banco temporário é construído pelas migrações e comparado com
o que `create_all` produziria.
"""
import pathlib
import re

import pytest
from sqlalchemy import create_engine, inspect

from models.database import ALEMBIC_HEAD, Base

alembic = pytest.importorskip("alembic", reason="Alembic é dependência de desenvolvimento")
from alembic import command                     # noqa: E402
from alembic.config import Config               # noqa: E402

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
_VERSOES = _RAIZ / "alembic" / "versions"


def _config(url: str) -> Config:
    cfg = Config(str(_RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(_RAIZ / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_head_registrado_bate_com_a_ultima_migracao():
    """
    `ALEMBIC_HEAD` é o que o init_db carimba em banco novo. Se ficar para trás,
    o carimbo aponta para uma revisão antiga e o próximo upgrade tenta aplicar
    migrações que o banco já tem.
    """
    revisoes = set()
    referenciadas = set()
    for arquivo in _VERSOES.glob("*.py"):
        texto = arquivo.read_text(encoding="utf-8")
        atual = re.search(r"^revision: str = ['\"]([^'\"]+)['\"]", texto, re.M)
        anterior = re.search(r"^down_revision: .*= ['\"]([^'\"]+)['\"]", texto, re.M)
        if atual:
            revisoes.add(atual.group(1))
        if anterior:
            referenciadas.add(anterior.group(1))

    heads = revisoes - referenciadas
    assert heads == {ALEMBIC_HEAD}, (
        f"models.database.ALEMBIC_HEAD={ALEMBIC_HEAD!r} não corresponde à última "
        f"migração ({heads}). Atualize a constante ao criar uma migração."
    )


def test_migracoes_produzem_o_mesmo_schema_dos_modelos(tmp_path):
    banco = tmp_path / "migrado.db"
    url = f"sqlite:///{banco}"

    command.upgrade(_config(url), "head")

    engine_migrado = create_engine(url)
    tabelas_migradas = {
        t: {c["name"] for c in inspect(engine_migrado).get_columns(t)}
        for t in inspect(engine_migrado).get_table_names()
        if t != "alembic_version"
    }
    engine_migrado.dispose()

    esperado = {
        t.name: {c.name for c in t.columns} for t in Base.metadata.sorted_tables
    }

    faltando = set(esperado) - set(tabelas_migradas)
    assert not faltando, f"tabelas que as migrações não criam: {sorted(faltando)}"

    for tabela, colunas in esperado.items():
        ausentes = colunas - tabelas_migradas[tabela]
        assert not ausentes, (
            f"{tabela}: colunas no modelo que a migração não cria: {sorted(ausentes)}"
        )


def test_banco_criado_por_create_all_ja_nasce_carimbado(tmp_path, monkeypatch):
    """
    Ambiente local cria o schema com create_all. Sem o carimbo, o primeiro
    `alembic upgrade head` tentaria criar tudo de novo e falharia.
    """
    import importlib
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'novo.db'}")
    import models.database as database
    database = importlib.reload(database)
    try:
        database.init_db()

        with database.engine.connect() as conn:
            from sqlalchemy import text
            versao = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert versao == database.ALEMBIC_HEAD
    finally:
        database.engine.dispose()
        monkeypatch.undo()
        importlib.reload(database)
