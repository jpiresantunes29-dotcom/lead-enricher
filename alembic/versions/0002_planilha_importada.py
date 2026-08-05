"""Planilha importada: lote de importação e células originais no lead

A importação de .xlsx/.csv guarda a planilha como o usuário a montou — ordem
das colunas, rótulos e o valor de cada célula — para a tela de grade reproduzir
o arquivo dele, e não uma tabela genérica nossa.

Revision ID: 7c2a1f4b9e33
Revises: 528805047d04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c2a1f4b9e33"
down_revision: Union[str, Sequence[str], None] = "528805047d04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=True),
        sa.Column("sheet_name", sa.String(length=255), nullable=True),
        sa.Column("columns", sa.JSON(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("draft_rows", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_import_batches_user_id"), ["user_id"], unique=False
        )

    # Colunas novas em tabela existente: batch_alter_table para o SQLite, que
    # não sabe fazer ALTER TABLE completo.
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cells", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("sheet_row", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("import_batch_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_leads_import_batch_id"), ["import_batch_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_leads_import_batch_id"))
        batch_op.drop_column("import_batch_id")
        batch_op.drop_column("sheet_row")
        batch_op.drop_column("cells")

    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_import_batches_user_id"))
    op.drop_table("import_batches")
