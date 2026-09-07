"""Chave da Lusha por usuario (BYOA)

O enriquecimento gratuito tem um teto estrutural: celular de executivo nao
existe em fonte publica e site corporativo moderno nao publica e-mail nominal,
entao o padrao de dominio raramente e aprendido. Quem ja paga Lusha passa a
poder conectar a propria conta e furar esse teto com os creditos dele.

A coluna e nula no estado normal — sem chave conectada nada e chamado e nada
custa. Gravada pelo tipo SegredoCriptografado, que no banco e texto
("enc:v1:..."): a cifragem acontece na aplicacao, nao no schema.

Revision ID: c5a71f0e3b92
Revises: b8ded539b15b
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5a71f0e3b92"
down_revision: Union[str, Sequence[str], None] = "b8ded539b15b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("lusha_api_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "lusha_api_key")
