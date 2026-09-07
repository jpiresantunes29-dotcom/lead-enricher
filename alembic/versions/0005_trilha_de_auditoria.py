"""Trilha de auditoria das decisões de contato

Guarda decisões, não mensagens: quem pausou, quem assumiu, quando a automação
levantou a mão, quando uma empresa deixou de ser lead. O conteúdo trocado
continua só em `wa_messages` — duplicá-lo aqui criaria uma segunda cópia de
dado pessoal para manter em dia.

Revision ID: c58a2f9017bd
Revises: b31e07c5d94a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c58a2f9017bd"
down_revision: Union[str, Sequence[str], None] = "b31e07c5d94a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("acao", sa.String(length=40), nullable=False),
        sa.Column("ator", sa.String(length=20), nullable=False,
                  server_default="sistema"),
        sa.Column("detalhe", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_logs_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_audit_logs_conversation_id"), ["conversation_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_audit_logs_lead_id"), ["lead_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_audit_logs_created_at"), ["created_at"], unique=False
        )
        # A consulta é sempre "as minhas, mais recentes primeiro".
        batch_op.create_index(
            "ix_audit_logs_user_created", ["user_id", "created_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_logs_user_created")
        batch_op.drop_index(batch_op.f("ix_audit_logs_created_at"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_lead_id"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_conversation_id"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_user_id"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_id"))
    op.drop_table("audit_logs")
