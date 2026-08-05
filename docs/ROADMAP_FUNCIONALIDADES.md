# Roadmap de Funcionalidades — LeadEnricher

> Criado em 2026-07-05, após auditoria completa do código.
> Ordem sugerida: Fase 0 (dívidas) → Fase 1 (retenção) → Fase 2 (monetização corporativa).

---

## Fase 0 — Fundação (antes de qualquer feature nova)

Itens que destravam as fases seguintes e fecham riscos conhecidos.

### 0.1 Modo demo seguro ✅ feito em 2026-07-05
O user demo compartilhado (`demo-user-2026`) foi substituído por users demo
**efêmeros por navegador** (`demo-<sufixo do token>`) em `middleware/auth.py` —
sessões demo não veem mais os dados umas das outras.
- Pendente: rotina de limpeza de perfis/leads demo antigos (TTL ~7 dias);
  e `DEMO_MODE=0` nas env vars da Vercel se quiser desligar o demo em produção.

### 0.2 Enriquecimento assíncrono (fila)
Hoje `/api/enrich` é síncrono: a função fica presa ~10–30 s por busca
(scraping + DNS + LinkedIn). **Na Vercel isso é crítico**: o `maxDuration` da
função é 60 s (já configurado em vercel.json) — buscas lentas estouram o limite.
- Ação: tabela `jobs` (id, lead_id, status, tentativa) + processamento via
  Vercel Cron / função dedicada com fluid compute + polling do front
  (`GET /api/leads/{id}` já devolve status).
- Esforço: 1–2 dias. Pré-requisito para o 1.1 (lote).

### 0.3 Alembic como fonte única de migração
`models/database.py::_ensure_new_columns()` e `alembic/` coexistem.
- Ação: gerar migração Alembic do estado atual, apagar `_ensure_new_columns`,
  rodar `alembic upgrade head` no build da Vercel (ou manualmente contra o
  Postgres do Supabase a cada mudança de schema).
- Esforço: meio dia.

### 0.4 Limpeza LGPD de verdade
O serviço antigo (`services/lgpd.py`) foi removido nesta auditoria porque estava
morto e com a query errada (anonimizava leads ativos). Reimplementar:
- Query correta: leads cuja **última** atividade (`MAX(created_at)`) < 90 dias.
- Agendamento: endpoint protegido `/api/internal/lgpd-purge` + Vercel Cron
  (crons no vercel.json).
- Registrar purga em log estruturado (auditoria).
- Esforço: 1 dia. Vira argumento de venda corporativo ("retenção automática LGPD").

### 0.5 Cobertura de testes dos routers críticos
Parcialmente feito em 2026-07-05 (suíte foi de 68 → 80 testes):
- ✅ `services/exporter.py` (`tests/test_exporter.py`): colunas de decisor,
  formatação de `employee_count`, header e XLSX válido.
- ✅ `/api/followups/today` (`tests/test_activities.py`): o filtro por tipo
  inexistente que zerava a fila do dia.
- ✅ páginas institucionais (`tests/test_api.py`).
- Pendente: `billing` (webhook Stripe — idempotência, upgrade/downgrade) e
  `crm_config`. Esforço: ~1 dia.

---

## Fase 1 — Retenção e uso diário (usuário volta todo dia)

### 1.1b Planilha dentro do sistema ✅ feito em 2026-08-05
Validado contra uma base real de prospecção (968 empresas, 19 colunas,
janeiro a julho). O objetivo é substituir o Excel, não conversar com ele:

- **Fidelidade total.** `Lead.cells` guarda todas as células da linha com o
  rótulo original (emoji incluso) e `import_batches.columns` guarda a ordem
  das colunas. Data vira ISO, número continua número, texto multi-linha fica
  intacto. Teste de ida e volta na base real: 18.392 células comparadas entre
  o arquivo do usuário e o exportado, 0 divergências.
- **Duas famílias de colunas.** As do arquivo (do usuário) e as do sistema
  (Domínio, Setor, Score…). O enriquecimento só escreve nas do sistema — é o
  que garante que a planilha original continue valendo depois da coleta.
- **Grid em `#sheet`**: cabeçalho e coluna de linha fixos, edição inline com
  teclado (Enter, Tab, setas, digitar substitui), busca em todas as colunas,
  ordenação que entende "1000 a 5000" como número, filtro por situação,
  nova linha, exclusão em lote e exportação do que está na tela.
- **Fila paralela**: N requisições simultâneas (2 a 12, padrão 6) contra
  `POST /api/leads/{id}/enrich`. Concorrência medida em teste de navegador.
  Cada lead consome 1 busca da cota; 402 para a fila com recado.
- **Descoberta de domínio** (`services/domain_finder.py`): a base real tinha
  968 empresas e só 67 domínios confiáveis. Sem isso, 93% das linhas não
  teriam como ser enriquecidas. Busca pelo nome, descarta rede social e
  agregador, confirma abrindo o site e recusa quando não dá para ter certeza.
- **E-mail da linha errada**: planilhas antigas acumulam isso. O domínio só
  é herdado do e-mail quando combina com o nome da empresa (8 casos na base
  real teriam enriquecido a empresa errada).

Pendente: fila server-side (item 0.2) para o usuário poder fechar a aba;
hoje o enriquecimento em lote depende da aba aberta.

### 1.1 Importação de planilha + lote ✅ feito em 2026-08-05
Relançado sem depender da fila do item 0.2 — a fila roda **no cliente**, um
lead por requisição, porque cada coleta leva 10–30 s e o `maxDuration` da
função na Vercel é 60 s.

Como funciona hoje:
- `services/importer.py` lê .xlsx/.csv, acha a linha de cabeçalho (pula
  títulos), mapeia colunas por alias PT/EN sem acento (Domínio, Site, Razão
  Social, Setor, Telefone…), normaliza domínio sujo e deriva domínio do e-mail
  quando não há coluna de site. Limites: 5 MB e 500 linhas.
- `POST /api/import/preview` só lê o arquivo (não grava) e devolve o
  diagnóstico linha a linha: ok, inválida, repetida no arquivo, já no
  histórico. `POST /api/import` grava os leads confirmados com
  `status="imported"` — sem consumir cota e sem score (scoring depende de
  sinais da coleta).
- `POST /api/leads/{id}/enrich` enriquece **no mesmo lead** (nada de
  duplicata), consome 1 busca e preserva o que veio da planilha nos campos que
  a coleta não achou. A busca manual de um domínio já importado cai no mesmo
  caminho. `GET /api/import/template` baixa o modelo de planilha.
- UI em `#import`: drop zone → preview com colunas reconhecidas → barra de
  progresso com "Parar", tratamento de 402 (cota) e 429 (rate limit). No
  histórico, lead importado ganha a tag "planilha" e um botão "Enriquecer".

Pendente (herda do 0.2): fila server-side para o usuário poder fechar a aba
durante o enriquecimento em lote.

### 1.2 Notas e edição manual do lead
Hoje o lead é 100 % automático. Vendedor precisa corrigir telefone, adicionar
contexto ("indicação do fulano") e marcar campos como confirmados.
- `PATCH /api/leads/{id}` (campos editáveis whitelist) + edição inline na UI.
- Esforço: 1 dia.

### 1.3 Digest diário de follow-ups por e-mail
O badge "hoje" só aparece com o app aberto. Um e-mail 8h com a fila do dia
(follow-ups atrasados + de hoje, link direto pro lead) cria hábito.
- Resend/Postmark + cron. Opt-in em Configurações.
- Esforço: 1–2 dias.

### 1.4 Multi-cargo na busca de decisores
`DecisoresRequest.roles` já aceita lista, mas a UI manda 1 cargo por vez.
- Chips multi-seleção na UI + persistir "cargos favoritos" do usuário
  (ex.: sempre busca CTO + Diretor de TI).
- Esforço: meio dia (backend pronto).

### 1.5 Refresh automático de leads quentes
Lead em estágio `oportunidade`/`reuniao_agendada` re-enriquece a cada 30 dias
(funcionários, MX e decisores mudam). Notificar mudanças ("trocou de provedor
de e-mail — gancho de abordagem").
- Esforço: 2 dias (depende de 0.2).

---

## Fase 2 — Cara corporativa e monetização Enterprise

### 2.1 Times e organizações (multiusuário)
Maior alavanca de receita: hoje `user_id` é individual. Modelo:
`organizations`, `org_members` (role: admin/member), leads visíveis pelo time,
dashboard consolidado do gestor (pipeline por vendedor).
- Esforço: 1 semana. Pré-requisito para plano Enterprise real.

### 2.2 Integrações CRM nativas (OAuth)
`CRMConnection` já tem colunas `access_token`/`refresh_token`/`account_id`
esperando por isso. Ordem por demanda BR: **HubSpot → Pipedrive → Dynamics**.
Push cria/atualiza Company + Contacts + Deal no estágio equivalente.
- Esforço: 3–4 dias por provedor.

### 2.3 API pública com API keys
`POST /v1/enrich` autenticado por chave (tabela `api_keys`, hash + prefixo),
rate limit por chave, doc OpenAPI já existe de graça no FastAPI.
Consumo de cota unificado com a UI.
- Esforço: 2–3 dias. Abre o plano Enterprise para uso programático.

### 2.4 Relatório executivo em PDF
Dashboard exportável (funil, taxas, ranking de leads por score) com logo,
período e comentário de IA — o que o vendedor manda pro gerente na sexta.
- Esforço: 2 dias (weasyprint ou template HTML + print CSS).

### 2.5 Auditoria e segurança Enterprise
- Log de auditoria (quem exportou, quem deletou, quem enviou ao CRM).
- SSO corporativo (Supabase já suporta SAML no plano pago).
- Página `/seguranca` pública documentando LGPD, retenção e criptografia.
- Esforço: contínuo; começar pelo log de auditoria (1 dia).

---

## Ideias avaliadas e adiadas (com motivo)

| Ideia | Motivo do adiamento |
|---|---|
| Extensão Chrome (enriquecer a partir do LinkedIn) | Alto custo de manutenção (LinkedIn muda o DOM); revisitar quando houver base de usuários |
| Discador/telefonia integrada | Regulatório + custo; o registro manual de ligação cobre o fluxo hoje |
| Enriquecimento de pessoa física (e-mail → perfil) | Risco LGPD alto; manter foco B2B por domínio |
| App mobile | A UI atual é responsiva; PWA resolve 90 % por fração do custo |
