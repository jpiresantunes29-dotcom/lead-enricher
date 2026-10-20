"""Coexistencia do WhatsApp Business: conversas sem lead, agenda e historico

O numero conectado pelo Embedded Signup em modo Coexistencia continua no
celular do dono e traz para ca o historico e as mensagens de qualquer contato
-- clientes, fornecedores, conhecidos --, nao so de leads. Por isso:

  - `conversations.lead_id` deixa de ser obrigatorio: a conversa existe sem
    lead e pode ser vinculada a um depois. A automacao continua so com lead
    (o portao recusa sem ele).
  - `conversations.contact_name`: o nome do contato, para a lista ter o que
    mostrar quando nao ha empresa.
  - `wa_contacts`: a agenda do WhatsApp Business, que chega antes das
    conversas e inclui quem nunca escreveu.
  - `whatsapp_connections.onboarding/historico_*`: como o numero foi
    conectado e em que pe esta a importacao do historico.

O downgrade apaga as conversas sem lead antes de voltar a coluna para NOT
NULL -- sem isso ele falharia em qualquer banco que ja recebeu historico.

Revision ID: f3a91c7d2e58
Revises: e81f4c2a9b6d
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a91c7d2e58"
down_revision: Union[str, Sequence[str], None] = "e81f4c2a9b6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.alter_column("lead_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("contact_name", sa.String(length=255), nullable=True))

    with op.batch_alter_table("whatsapp_connections") as batch:
        batch.add_column(sa.Column("onboarding", sa.String(length=20), nullable=False,
                                   server_default="manual"))
        batch.add_column(sa.Column("historico_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("historico_progresso", sa.Integer(), nullable=True))

    op.create_table(
        "wa_contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "phone_e164", name="uq_wa_contact_user_phone"),
    )
    op.create_index("ix_wa_contacts_id", "wa_contacts", ["id"])
    op.create_index("ix_wa_contacts_user_id", "wa_contacts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_wa_contacts_user_id", table_name="wa_contacts")
    op.drop_index("ix_wa_contacts_id", table_name="wa_contacts")
    op.drop_table("wa_contacts")

    with op.batch_alter_table("whatsapp_connections") as batch:
        batch.drop_column("historico_progresso")
        batch.drop_column("historico_status")
        batch.drop_column("onboarding")

    sem_lead = "SELECT id FROM conversations WHERE lead_id IS NULL"
    op.execute(f"DELETE FROM wa_messages WHERE conversation_id IN ({sem_lead})")
    op.execute(f"DELETE FROM audit_logs WHERE conversation_id IN ({sem_lead})")
    op.execute("DELETE FROM conversations WHERE lead_id IS NULL")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("contact_name")
        batch.alter_column("lead_id", existing_type=sa.Integer(), nullable=False)
