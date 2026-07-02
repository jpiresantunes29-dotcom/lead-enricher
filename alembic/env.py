"""Alembic migration environment."""
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
from models.database import Base

# Carrega a config
config = context.config

# Lê de sqlalchemy.url na config, ou use DATABASE_URL env var
import os
db_url = os.getenv('DATABASE_URL', 'sqlite:///./lead_enricher.db')
config.set_main_option("sqlalchemy.url", db_url)

# Pega target_metadata de models.database
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Roda migrações em modo 'offline'."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Roda migrações em modo 'online'."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
