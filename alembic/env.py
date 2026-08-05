"""
Ambiente do Alembic.

A URL do banco vem de `models.database` — que já lê DATABASE_URL do ambiente e
corrige o esquema legado "postgres://" — e não do alembic.ini. Assim a migração
sempre aponta para o mesmo banco que a aplicação, e não existe uma segunda
string de conexão para alguém esquecer de atualizar.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from models.database import Base, DATABASE_URL

# Importar o módulo é o que popula Base.metadata com todas as tabelas.
import models.database  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    """
    URL do banco, em ordem de precedência:

      1. `alembic -x db_url=...`      — aplicar numa base específica sem mexer no ambiente
      2. `sqlalchemy.url` do config   — é assim que os testes apontam para um banco temporário
      3. `DATABASE_URL` da aplicação  — o caso normal

    A aplicação continua sendo a fonte da string de conexão: não existe uma
    segunda URL no ini para alguém esquecer de atualizar.
    """
    argumento = context.get_x_argument(as_dictionary=True).get("db_url")
    if argumento:
        return argumento
    configurada = (config.get_main_option("sqlalchemy.url") or "").strip()
    return configurada or DATABASE_URL


def run_migrations_offline() -> None:
    """Gera o SQL sem conectar — útil para revisar o que será aplicado."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                # SQLite não altera coluna no lugar; o batch mode recria a
                # tabela. Sem isto, migração que mude tipo quebra no local.
                render_as_batch=connection.dialect.name == "sqlite",
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
