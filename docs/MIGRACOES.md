# Migrações de banco

A fonte da verdade do schema é o **Alembic** (`alembic/versions/`). Os modelos
em `models/database.py` descrevem o que a aplicação espera; as migrações
descrevem como chegar lá a partir de um banco existente.

## Fluxo normal (mudou um modelo)

```bash
python -m alembic revision --autogenerate -m "descrição curta da mudança"
```

Depois:

1. **Leia o arquivo gerado.** O autogenerate acerta na maior parte das vezes e
   erra nas que importam: renomear coluna vira "dropar + criar" (perde dados),
   e mudança de tipo em SQLite precisa de batch mode.
2. Atualize `ALEMBIC_HEAD` em `models/database.py` com a nova revisão — é o que
   o `init_db()` carimba em banco novo. O teste `tests/test_migracoes.py`
   falha se você esquecer.
3. Aplique:

```bash
python -m alembic upgrade head
```

## Ambientes

| Ambiente | Como o schema é criado | Por quê |
|---|---|---|
| Desenvolvimento e testes | `init_db()` → `create_all` + carimbo da revisão | Criar do zero é mais rápido que aplicar o histórico inteiro; o carimbo mantém o banco compatível com o Alembic |
| Produção (Postgres/Supabase) | `alembic upgrade head` | Só a migração preserva os dados existentes |

O `_sync_schema()` continua no `init_db()` como rede de segurança: se faltar
alguma coluna, ele aplica o `ADD COLUMN` e **loga um WARNING** — que significa
"alguém mudou o modelo sem gerar a migração". Um schema incompleto quebra o
produto inteiro; preferimos consertar e avisar a recusar o boot.

## Comandos úteis

```bash
python -m alembic current
```

```bash
python -m alembic history --verbose
```

```bash
python -m alembic upgrade head --sql
```

O último não conecta em nada: imprime o SQL para revisão antes de aplicar em
produção.

## Banco que já existia antes do Alembic

```bash
python -m alembic stamp head
```

Marca o banco como estando na revisão atual sem executar nada. É exatamente o
que o `init_db()` faz sozinho ao criar um schema novo.

## Onde a URL do banco é lida

`alembic/env.py` usa a `DATABASE_URL` da aplicação (via `models.database`), não
o `sqlalchemy.url` do `alembic.ini`. Assim não existe uma segunda string de
conexão para alguém esquecer de atualizar. Para apontar para outro banco numa
execução específica:

```bash
python -m alembic -x db_url=postgresql://... upgrade head
```

ou simplesmente defina `DATABASE_URL` no ambiente do comando.
