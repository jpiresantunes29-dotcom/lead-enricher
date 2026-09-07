"""Marca quando um lead foi coletado pela ultima vez

Base para a atualizacao automatica de leads antigos (services/jobs.py,
enqueue_stale_refreshes): sem saber quando a ultima coleta rodou, um lead
recoletado ontem voltaria para a fila hoje so porque `created_at` continua
velho.

Revision ID: b8ded539b15b
Revises: f41685301209
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8ded539b15b"
down_revision: Union[str, Sequence[str], None] = "f41685301209"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "refreshed_at")
