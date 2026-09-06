"""Cache de verificacao premium de e-mail (Hunter e afins)

Ate aqui, uma chave HUNTER_API_KEY configurada nao tinha efeito nenhum: nada
no fluxo real de decisores ou da extensao chamava o provedor. Esta tabela e o
que permite ligar o Hunter sem abrir mao de controle de custo — cache por
e-mail (nunca paga duas vezes pela mesma resposta) e teto diario de chamadas,
contado em `provider_calls` (tabela que ja existia, tambem sem nada
escrevendo nela ate agora).

Revision ID: b434d27066e6
Revises: d92b4e15c7a3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b434d27066e6"
down_revision: Union[str, Sequence[str], None] = "d92b4e15c7a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "premium_email_checks",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="hunter"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("email"),
    )

    if op.get_bind().dialect.name == "postgresql":
        # Mesmo tratamento que a 0006 deu as demais: e-mail e dado pessoal, e
        # o acesso a ele e sempre pelo backend, nunca pela API REST do Supabase.
        op.execute("ALTER TABLE public.premium_email_checks ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("premium_email_checks")
