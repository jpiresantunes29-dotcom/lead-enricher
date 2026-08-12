"""Conversas de WhatsApp e o relacionamento do lead

Fundação do contato automatizado: `leads.relationship` diz quem a empresa é
(prospecto, cliente, pediu para não receber) e é a trava que impede a
automação de abordar quem não deve ser abordado. `conversations` e
`wa_messages` guardam a conversa em si.

Revision ID: 9f4d61c8a207
Revises: 7c2a1f4b9e33
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f4d61c8a207"
down_revision: Union[str, Sequence[str], None] = "7c2a1f4b9e33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Coluna nova em tabela com linhas: entra nullable com default e só depois
    # vira NOT NULL, já com as linhas antigas preenchidas. O default do
    # servidor fica para que uma inserção feita por fora do ORM (import, script
    # de manutenção) não crie lead sem relacionamento.
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("relationship", sa.String(length=20),
                      nullable=True, server_default="LEAD")
        )
    op.execute("UPDATE leads SET relationship = 'LEAD' WHERE relationship IS NULL")
    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.alter_column("relationship", nullable=False,
                              existing_type=sa.String(length=20),
                              existing_server_default="LEAD")

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("decision_maker_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False,
                  server_default="whatsapp"),
        sa.Column("ai_status", sa.String(length=20), nullable=False,
                  server_default="AI_ACTIVE"),
        sa.Column("window_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_body", sa.Text(), nullable=True),
        sa.Column("handoff_reason", sa.String(length=500), nullable=True),
        sa.Column("after_hours", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["decision_maker_id"], ["decision_makers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_conversations_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_conversations_lead_id"), ["lead_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_conversations_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_conversations_phone_e164"), ["phone_e164"], unique=False)
        batch_op.create_index("ix_conversations_user_updated", ["user_id", "updated_at"], unique=False)

    op.create_table(
        "wa_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("wa_message_id", sa.String(length=128), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("template_name", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("sent_by", sa.String(length=10), nullable=True),
        sa.Column("intent_detected", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("wa_messages", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wa_messages_id"), ["id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_wa_messages_conversation_id"), ["conversation_id"], unique=False
        )
        # Único: a Meta reentrega a mesma mensagem quando não recebe 200 a
        # tempo, e o banco é o último lugar onde isso pode ser barrado.
        batch_op.create_index(
            batch_op.f("ix_wa_messages_wa_message_id"), ["wa_message_id"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("wa_messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_wa_messages_wa_message_id"))
        batch_op.drop_index(batch_op.f("ix_wa_messages_conversation_id"))
        batch_op.drop_index(batch_op.f("ix_wa_messages_id"))
    op.drop_table("wa_messages")

    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_index("ix_conversations_user_updated")
        batch_op.drop_index(batch_op.f("ix_conversations_phone_e164"))
        batch_op.drop_index(batch_op.f("ix_conversations_user_id"))
        batch_op.drop_index(batch_op.f("ix_conversations_lead_id"))
        batch_op.drop_index(batch_op.f("ix_conversations_id"))
    op.drop_table("conversations")

    with op.batch_alter_table("leads", schema=None) as batch_op:
        batch_op.drop_column("relationship")
