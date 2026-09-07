"""Teto de respostas automáticas fora do horário comercial

Fora do expediente a automação responde curto, e só até um limite de trocas —
depois cala até o próximo dia útil. Este contador é o que torna esse limite
determinístico, em vez de depender do modelo obedecer a uma instrução.

Revision ID: b31e07c5d94a
Revises: 9f4d61c8a207
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b31e07c5d94a"
down_revision: Union[str, Sequence[str], None] = "9f4d61c8a207"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("after_hours_turns", sa.Integer(),
                      nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_column("after_hours_turns")
