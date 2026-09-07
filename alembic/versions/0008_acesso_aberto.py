"""Acesso aberto: sem planos, cotas nem cobranca

O produto deixou de ter plano pago, plano gratuito, creditos e modo
demonstracao: quem entra com uma conta tem tudo. O que sai daqui e a
estrutura que existia so para medir e cobrar.

  profiles   perde plan, searches_used/limit, reveals_used/limit,
             stripe_customer_id e quota_reset_at — restam id e datas,
             que sao o que da dono aos leads.
  reveals    perde credits_charged. A tabela fica: ela registra que
             aquela pessoa ja foi revelada por este usuario, o que a
             extensao mostra na tela.
  stripe_events  some inteira — existia so para dar idempotencia ao
             webhook de pagamento, que nao existe mais.

O downgrade recria as colunas vazias e a tabela: devolve a forma, nao os
numeros. Contador de uso perdido nao tem de onde ser reconstruido, e
inventar valor seria pior do que a coluna nula.

Revision ID: a4c7e208d5f1
Revises: f3a7d9c1b204
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4c7e208d5f1"
down_revision: Union[str, Sequence[str], None] = "f3a7d9c1b204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUNAS_DE_COTA = (
    "plan",
    "searches_used",
    "searches_limit",
    "reveals_used",
    "reveals_limit",
    "stripe_customer_id",
    "quota_reset_at",
)


def _colunas(tabela: str) -> set:
    inspector = sa.inspect(op.get_bind())
    return {c["name"] for c in inspector.get_columns(tabela)}


def upgrade() -> None:
    existentes = _colunas("profiles")
    # SQLite so ganhou DROP COLUMN no 3.35 e o batch_alter_table resolve o
    # resto; a conferencia evita erro em banco que ja subiu sem a coluna.
    with op.batch_alter_table("profiles") as batch:
        for coluna in _COLUNAS_DE_COTA:
            if coluna in existentes:
                batch.drop_column(coluna)

    if "credits_charged" in _colunas("reveals"):
        with op.batch_alter_table("reveals") as batch:
            batch.drop_column("credits_charged")

    inspector = sa.inspect(op.get_bind())
    if "stripe_events" in inspector.get_table_names():
        op.drop_table("stripe_events")


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(sa.Column("plan", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("searches_used", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("searches_limit", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reveals_used", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("reveals_limit", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("stripe_customer_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("quota_reset_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("reveals") as batch:
        batch.add_column(sa.Column("credits_charged", sa.Integer(), nullable=True))

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    if op.get_bind().dialect.name == "postgresql":
        # A 0006 fechou esta tabela; recria-la sem RLS reabriria o buraco.
        op.execute("ALTER TABLE public.stripe_events ENABLE ROW LEVEL SECURITY")
