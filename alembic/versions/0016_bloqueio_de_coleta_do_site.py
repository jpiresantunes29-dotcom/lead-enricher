"""Motivo do bloqueio do site na coleta

Site que responde 403 ou devolve um desafio anti-bot (Akamai, Cloudflare) faz
o scraping voltar vazio, e a ficha ficava indistinguivel de uma empresa que
simplesmente nao publica nada: setor, localizacao e descricao em branco, sem
dizer por que. Sao situacoes opostas para quem vende — uma nao tem dado, a
outra tem e nao deixou coletar.

Guarda so o motivo (`http_403`, `bot_wall`, ...), nao a resposta: o que a tela
precisa e a frase "o site bloqueou a coleta", e o corpo do desafio nao serve
para mais nada depois disso.

Nulo em toda ficha ja existente, o que le certo: "nao houve bloqueio
registrado nesta coleta".

Revision ID: c4b8f21a7e35
Revises: a1c7e93b4d20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4b8f21a7e35"
down_revision: Union[str, Sequence[str], None] = "a1c7e93b4d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("site_block_reason", sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "site_block_reason")
