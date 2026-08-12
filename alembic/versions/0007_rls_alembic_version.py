"""Habilita RLS na tabela de controle do Alembic

Ultima tabela apontada pelo linter de seguranca do Supabase depois da
0006. Nao guarda dado de lead nem de conversa — so o numero da revisao
atual — mas fica exposta pelo PostgREST como qualquer outra tabela
publica sem RLS, entao fecha a mesma lacuna por consistencia.

Revision ID: f3a7d9c1b204
Revises: e91b4c2a6f18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f3a7d9c1b204"
down_revision: Union[str, Sequence[str], None] = "e91b4c2a6f18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE public.alembic_version DISABLE ROW LEVEL SECURITY")
