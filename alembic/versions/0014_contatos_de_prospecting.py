"""Estado de revelacao dos contatos vindos da Lusha Prospecting

A Prospecting API separa listar (barato: 1 credito por 25) de revelar (caro:
1 credito por e-mail, 5 por telefone). Para a tela poder mostrar 25 contatos
por 1 credito e so gastar o resto sob clique, o banco precisa lembrar tres
coisas que antes nao existiam:

  - qual o ID daquele contato na Lusha, para conseguir revelar depois;
  - se ele JA foi revelado, para nao cobrar duas vezes pelo mesmo dado;
  - o que da para revelar e por quanto, para o botao dizer o preco antes.

Os demais campos (localizacao, departamento, senioridade, setores) vem de
graca no search e existem para os filtros da barra lateral e para o card. A
localizacao em especial substitui um "Sao Paulo, Brazil" que estava escrito
fixo no JavaScript — certo para uma minoria dos contatos e errado para todos
os outros.

`source` distingue contato pago de contato gratuito na mesma tabela: sem ele
nao da para saber se um card em branco e "a Lusha nao tinha" ou "nunca foi
perguntado a Lusha".

Revision ID: 7d3c1a94ef60
Revises: c5a71f0e3b92
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7d3c1a94ef60"
down_revision: Union[str, Sequence[str], None] = "c5a71f0e3b92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("decision_makers", sa.Column("lusha_contact_id", sa.String(length=64), nullable=True))
    op.add_column("decision_makers", sa.Column("revealed", sa.Boolean(), nullable=True))
    op.add_column("decision_makers", sa.Column("can_reveal", sa.JSON(), nullable=True))
    op.add_column("decision_makers", sa.Column("data_points", sa.JSON(), nullable=True))
    op.add_column("decision_makers", sa.Column("department", sa.String(length=100), nullable=True))
    op.add_column("decision_makers", sa.Column("seniority", sa.String(length=50), nullable=True))
    op.add_column("decision_makers", sa.Column("location", sa.String(length=255), nullable=True))
    op.add_column("decision_makers", sa.Column("company_industries", sa.JSON(), nullable=True))
    op.add_column("decision_makers", sa.Column("source", sa.String(length=20), nullable=True))

    op.create_index(
        "ix_decision_makers_lusha_contact_id",
        "decision_makers",
        ["lusha_contact_id"],
    )

    # Linha que ja existia veio do caminho gratuito, por definicao: a Lusha
    # Prospecting nunca gravou nada antes desta migracao. Deixar `revealed`
    # nulo faria o endpoint de revelacao tratar contato antigo como
    # "nao revelado" e tentar chamar a Lusha com um ID que nao existe.
    op.execute("UPDATE decision_makers SET revealed = FALSE, source = 'free' WHERE source IS NULL")


def downgrade() -> None:
    op.drop_index("ix_decision_makers_lusha_contact_id", table_name="decision_makers")
    op.drop_column("decision_makers", "source")
    op.drop_column("decision_makers", "company_industries")
    op.drop_column("decision_makers", "location")
    op.drop_column("decision_makers", "seniority")
    op.drop_column("decision_makers", "department")
    op.drop_column("decision_makers", "data_points")
    op.drop_column("decision_makers", "can_reveal")
    op.drop_column("decision_makers", "revealed")
    op.drop_column("decision_makers", "lusha_contact_id")
