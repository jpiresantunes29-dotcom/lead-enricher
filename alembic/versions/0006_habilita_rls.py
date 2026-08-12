"""Habilita Row Level Security em todas as tabelas do app

O Supabase expõe automaticamente qualquer tabela do schema `public` pela API
REST (PostgREST), para quem tiver a chave publicável — que é feita para ser
pública, embutida no navegador. Sem RLS, essa mesma chave lê e escreve direto
nas tabelas, inclusive `wa_messages` (conteúdo de conversa) e `audit_logs`.

Isto não muda nada para o app: a conexão dele usa o papel `postgres` do
pooler, que tem BYPASSRLS no Supabase — RLS só se aplica a quem entra pela
API REST com as chaves `anon`/`authenticated`, que este app nunca usa. Sem
política nenhuma criada, RLS habilitada é "negar tudo" para esses papéis —
suficiente, já que ninguém deveria estar lendo por ali.

Revision ID: e91b4c2a6f18
Revises: c58a2f9017bd
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e91b4c2a6f18"
down_revision: Union[str, Sequence[str], None] = "c58a2f9017bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABELAS = [
    "profiles", "stripe_events", "leads", "decision_makers", "activities",
    "conversations", "wa_messages", "audit_logs", "companies", "persons",
    "person_emails", "person_phones", "email_patterns", "reveals",
    "provider_calls", "opt_outs", "jobs", "extension_tokens",
    "import_batches", "crm_connections",
]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite local nao tem RLS; a protecao so existe em producao
    for tabela in TABELAS:
        op.execute(f"ALTER TABLE public.{tabela} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for tabela in TABELAS:
        op.execute(f"ALTER TABLE public.{tabela} DISABLE ROW LEVEL SECURITY")
