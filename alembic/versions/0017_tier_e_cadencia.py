"""Tier comercial, cadencia de contato e base de clientes do time

Automatiza os quatro fluxogramas de prospeccao (Captura e Priorizacao,
Agendamento do 1o contato, Contatos e Qualificacao, Aquecimento de lead frio).

- `leads.tier*`: o tier calculado ao fim da coleta, o manual e o caminho
  percorrido no fluxograma.
- `leads.cadence*`: onde a ficha esta nos fluxos de contato. Nulo em toda
  ficha existente, o que le certo: "base nao executada".
- `activities.step` / `decision_maker_id`: qual etapa da cadencia gerou a
  tarefa e com quem ela fala.
- `decision_makers.is_primary`: o contato principal da cadencia.
- `customer_domains`: clientes Skymail/revenda por time.

Revision ID: d7e3a915f0c2
Revises: c4b8f21a7e35
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7e3a915f0c2"
down_revision: Union[str, Sequence[str], None] = "c4b8f21a7e35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("tier", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("tier_auto", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("tier_manual", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("tier_reason", sa.String(length=500), nullable=True))
    op.add_column("leads", sa.Column("tier_details", sa.JSON(), nullable=True))
    op.add_column("leads", sa.Column("cadence", sa.String(length=20), nullable=True))
    op.add_column("leads", sa.Column("cadence_attempt", sa.Integer(), nullable=False,
                                     server_default="0"))
    op.add_column("leads", sa.Column("first_contact_at", sa.DateTime(timezone=True),
                                     nullable=True))
    op.create_index("ix_leads_tier", "leads", ["tier"])
    op.create_index("ix_leads_cadence", "leads", ["cadence"])

    op.add_column("activities", sa.Column("step", sa.String(length=30), nullable=True))
    op.add_column("activities", sa.Column("decision_maker_id", sa.Integer(), nullable=True))
    op.create_index("ix_activities_step", "activities", ["step"])
    with op.batch_alter_table("activities") as batch:
        batch.create_foreign_key(
            "fk_activities_decision_maker", "decision_makers",
            ["decision_maker_id"], ["id"], ondelete="SET NULL",
        )

    op.add_column("decision_makers", sa.Column("is_primary", sa.Boolean(), nullable=False,
                                               server_default=sa.false()))

    op.create_table(
        "customer_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="skymail"),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team", "domain", name="uq_customer_team_domain"),
    )
    op.create_index("ix_customer_domains_id", "customer_domains", ["id"])
    op.create_index("ix_customer_domains_team", "customer_domains", ["team"])
    op.create_index("ix_customer_domains_domain", "customer_domains", ["domain"])

    if op.get_bind().dialect.name == "postgresql":
        # Mesmo tratamento da 0006: acesso so pelo backend.
        op.execute("ALTER TABLE public.customer_domains ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("customer_domains")
    op.drop_column("decision_makers", "is_primary")
    with op.batch_alter_table("activities") as batch:
        batch.drop_constraint("fk_activities_decision_maker", type_="foreignkey")
    op.drop_index("ix_activities_step", table_name="activities")
    op.drop_column("activities", "decision_maker_id")
    op.drop_column("activities", "step")
    op.drop_index("ix_leads_cadence", table_name="leads")
    op.drop_index("ix_leads_tier", table_name="leads")
    for col in ("first_contact_at", "cadence_attempt", "cadence", "tier_details",
                "tier_reason", "tier_manual", "tier_auto", "tier"):
        op.drop_column("leads", col)
