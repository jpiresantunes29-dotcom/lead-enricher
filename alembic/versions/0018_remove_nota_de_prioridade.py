"""Remove a nota de prioridade (0-100)

A regua de pontuacao (services/lead_scorer.py, migracao 0015) sai do produto:
os pesos e as faixas ainda nao foram definidos pelo time comercial, e uma nota
com numeros provisorios na tela atrapalha mais do que ajuda. A priorizacao
fica so com o tier (0017), que segue o fluxograma do time.

A nota era derivada — recalculavel a qualquer momento a partir da ficha —,
entao apagar as colunas nao perde dado de origem. O downgrade recria as
colunas vazias.

Revision ID: e81f4c2a9b6d
Revises: d7e3a915f0c2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e81f4c2a9b6d"
down_revision: Union[str, Sequence[str], None] = "d7e3a915f0c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_leads_score", table_name="leads")
    op.drop_index("ix_leads_priority", table_name="leads")
    with op.batch_alter_table("leads") as batch:
        batch.drop_column("score_version")
        batch.drop_column("score_breakdown")
        batch.drop_column("priority")
        batch.drop_column("score")


def downgrade() -> None:
    op.add_column("leads", sa.Column("score", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("priority", sa.String(length=10), nullable=True))
    op.add_column("leads", sa.Column("score_breakdown", sa.JSON(), nullable=True))
    op.add_column("leads", sa.Column("score_version", sa.String(length=20), nullable=True))
    op.create_index("ix_leads_score", "leads", ["score"])
    op.create_index("ix_leads_priority", "leads", ["priority"])
