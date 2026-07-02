"""Initial schema — v2 with new v3 columns (score, stage, ai_summary)

Revision ID: 001_initial
Revises:
Create Date: 2026-06-12

"""
from alembic import op
import sqlalchemy as sa

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # As tabelas já existem via init_db/create_all.
    # Esta migração documenta o estado atual para futuras mudanças.
    pass


def downgrade() -> None:
    pass
