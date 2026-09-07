"""Nota de prioridade do lead

Quatro colunas que passam a guardar o resultado de services/lead_scorer.py:
a nota normalizada, a faixa derivada dela, o detalhamento sinal a sinal (o que
responde "por que 47?" na tela) e a versao da regua que produziu tudo isso.

`score` e `priority` nascem indexados porque o uso normal da tela e ordenar e
filtrar por eles — sem indice, cada abertura do historico varre a tabela.

Sem backfill de proposito: pontuar aqui exigiria importar a regua dentro da
migracao e congelar a versao dela no arquivo, e uma migracao que embute regra
de negocio envelhece errado. As fichas antigas ficam com nota nula ate a
proxima coleta ou ate `POST /api/leads/{id}/rescore` — nulo significa "ainda
nao pontuada", que e a verdade.

Revision ID: a1c7e93b4d20
Revises: 7d3c1a94ef60
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c7e93b4d20"
down_revision: Union[str, Sequence[str], None] = "7d3c1a94ef60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("score", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("priority", sa.String(length=10), nullable=True))
    op.add_column("leads", sa.Column("score_breakdown", sa.JSON(), nullable=True))
    op.add_column("leads", sa.Column("score_version", sa.String(length=20), nullable=True))
    op.create_index("ix_leads_score", "leads", ["score"])
    op.create_index("ix_leads_priority", "leads", ["priority"])


def downgrade() -> None:
    op.drop_index("ix_leads_priority", table_name="leads")
    op.drop_index("ix_leads_score", table_name="leads")
    op.drop_column("leads", "score_version")
    op.drop_column("leads", "score_breakdown")
    op.drop_column("leads", "priority")
    op.drop_column("leads", "score")
