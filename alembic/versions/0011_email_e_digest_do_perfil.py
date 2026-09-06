"""Email e preferencia de digest diario no perfil

O digest diario por e-mail precisa saber para quem escrever. O backend nunca
guardou o e-mail do usuario (so o `sub`, do JWT) — agora ele e capturado do
claim `email` a cada `/api/me` e mantido aqui. `digest_diario` e o
liga/desliga, ligado por padrao.

Revision ID: f41685301209
Revises: b434d27066e6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f41685301209"
down_revision: Union[str, Sequence[str], None] = "b434d27066e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column(
        "profiles",
        sa.Column("digest_diario", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("profiles", "digest_diario")
    op.drop_column("profiles", "email")
