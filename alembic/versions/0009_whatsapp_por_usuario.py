"""WhatsApp por usuario: cada conta conecta o proprio numero

Ate aqui o WhatsApp era da instalacao: numero, token e template viviam em
variavel de ambiente, um jogo so para todo mundo, e trocar exigia deploy.
Esta tabela move a credencial para a conta — quem entra conecta o proprio
WhatsApp Business pela tela e envia pelo numero dele.

`phone_number_id` e unico porque e a chave que o webhook usa para descobrir
de quem e a mensagem que chegou: dois donos para o mesmo numero tornariam
essa resposta ambigua.

Revision ID: d92b4e15c7a3
Revises: a4c7e208d5f1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d92b4e15c7a3"
down_revision: Union[str, Sequence[str], None] = "a4c7e208d5f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_connections",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("phone_number_id", sa.String(length=64), nullable=False),
        sa.Column("waba_id", sa.String(length=64), nullable=True),
        sa.Column("display_phone_number", sa.String(length=32), nullable=True),
        # Os tres segredos sao gravados pelo tipo SegredoCriptografado, que no
        # banco e texto ("enc:v1:..."). A migracao declara String de propriedade:
        # a cifragem acontece na aplicacao, nao no schema.
        sa.Column("access_token", sa.String(), nullable=False),
        sa.Column("app_secret", sa.String(), nullable=True),
        sa.Column("verify_token", sa.String(), nullable=True),
        sa.Column("template_name", sa.String(length=120), nullable=True),
        sa.Column("template_language", sa.String(length=10), nullable=False,
                  server_default="pt_BR"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )
    with op.batch_alter_table("whatsapp_connections", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_whatsapp_connections_phone_number_id"),
            ["phone_number_id"], unique=True,
        )

    if op.get_bind().dialect.name == "postgresql":
        # Mesmo tratamento que a 0006 deu as demais: a tabela guarda credencial
        # de terceiro e o acesso a ela e sempre pelo backend, nunca pelo cliente.
        op.execute("ALTER TABLE public.whatsapp_connections ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    with op.batch_alter_table("whatsapp_connections", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_whatsapp_connections_phone_number_id"))
    op.drop_table("whatsapp_connections")
