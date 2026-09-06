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

### 0.2 Enriquecimento assíncrono (fila) ✅ feito
`services/jobs.py` — tabela `jobs`, reserva por UPDATE condicional (seguro com
cron e navegador rodando juntos), recuperação de job travado
(`reclaim_stale`), retentativas (`MAX_ATTEMPTS`). Processado pelo navegador
(`POST /api/batches/{id}/run`) e pelo cron (`POST /api/internal/jobs/run`,
`vercel.json`). Desde 2026-09, a coleta de vários domínios roda em paralelo
dentro de cada rodada (`JOBS_MAX_WORKERS`) — só a coleta de rede é paralela;
a escrita no banco continua sequencial na mesma sessão.

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
- ✅ `crm_config` (`tests/test_integrations.py`): toggle e delete de conexão,
  incluindo conexão inexistente (404).
- `billing`/Stripe foi removido do produto (decisão de não cobrar por
  enquanto) — item cancelado, não pendente.

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

Fila server-side (item 0.2) feita — o cron cobre quem fecha a aba antes do
lote terminar, além do processamento no cliente descrito acima.

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

Fila server-side (herdava do 0.2) feita — ver nota acima.

### 1.2 Notas e edição manual do lead
Hoje o lead é 100 % automático. Vendedor precisa corrigir telefone, adicionar
contexto ("indicação do fulano") e marcar campos como confirmados.
- `PATCH /api/leads/{id}` (campos editáveis whitelist) + edição inline na UI.
- Esforço: 1 dia.

### 1.3 Digest diário por e-mail ✅ feito em 2026-09-06
`services/digest.py` + `POST /api/internal/digest` (cron 11h UTC,
`vercel.json`), via Resend (`services/mailer.py`, já existia para o opt-out).
Cobre: novos leads, conversas de WhatsApp iniciadas, respostas recebidas e
conversas aguardando resposta do usuário nas últimas 24h — usuário sem
nenhuma atividade não recebe nada, para não virar e-mail que ninguém abre.
Liga/desliga em `PATCH /api/me` (`digest_diario`, ligado por padrão).
- Pendente: não cobre follow-ups atrasados especificamente (isso ainda
  depende da tela de atividades, `routers/activities.py`) — se quiser esse
  recorte, é uma extensão pequena de `services/digest.py`.

### 1.4 Multi-cargo na busca de decisores
`DecisoresRequest.roles` já aceita lista, mas a UI manda 1 cargo por vez.
- Chips multi-seleção na UI + persistir "cargos favoritos" do usuário
  (ex.: sempre busca CTO + Diretor de TI).
- Esforço: meio dia (backend pronto).

### 1.5 Refresh automático de leads antigos ✅ feito em 2026-09-06
`services/jobs.py::enqueue_stale_refreshes` — todo lead com `relationship ==
LEAD` não revisitado há `STALE_LEAD_DAYS` (30 por padrão) volta sozinho para a
fila (mesma ficha, sem duplicar), até `MAX_STALE_REFRESH_PER_ROUND` por
rodada. Reaproveita o cron do item 0.2 (`POST /api/internal/jobs/run`) em vez
de precisar de um agendamento novo.
- Escopo diferente do que este item previa: aplica a **todo** lead ativo, não
  só `oportunidade`/`reuniao_agendada` (o campo `stage` não distingue isso
  hoje na query — daria para restringir por `stage` se fizer sentido depois).
- Pendente: notificar mudanças detectadas (ex.: "trocou de provedor de
  e-mail") — hoje só atualiza a ficha, sem comparar com o valor anterior.

### 1.6 Debounce e agrupamento de mensagens picotadas (WhatsApp)
Quando o lead manda 3-5 mensagens seguidas rapidamente, agrupar e responder
uma vez em vez de gerar N respostas. Melhora UX e reduz custo/latência da IA.
- Implementação: pequeno atraso (5-8s) antes de processar; se mensagem nova
  chega, reagendar; reusar cron já existente em vez de `sleep` (serverless).
- Esforço: 1 dia.

### 1.7 Múltiplos templates geridos pelo painel
Hoje só 1 template fixo (`WHATSAPP_TEMPLATE_NAME`) para abertura fria.
Criar tabela `wa_templates` com registro simples (nome, categoria Meta, corpo)
e permitir escolher ao reenviar template após janela de 24h fechar (reengajamento).
- Esforço: 1-2 dias.

### 1.8 Base de conhecimento institucional leve
Arquivo `.md` curto (não dinâmico) com "sobre nós: quem somos, o que vendemos,
3 diferenciais" injetado no prompt da IA. Melhora respostas em
`CONVERSANDO` sem tocar na regra "preço/condição → humano".
- Esforço: 1 dia.

### 1.9 Ferramenta de leitura de agenda (Google Calendar)
Dar ao LLM uma função de **leitura** (não escrita) para consultar horários
livres do vendedor via Google Calendar API (gratuita até cota generosa).
IA sugere 2-3 horários reais na conversa; a **criação do evento** continua
como ação determinística/humana.
- Esforço: 2-3 dias (integração OAuth por vendedor).

### 1.10 Script de QA com conversas sintéticas (teste adversário)
Script que gera N conversas automáticas (outro LLM faz o papel do lead,
variando intenção), roda contra `brain.ler()`, compara resultado esperado vs
obtido, gera relatório (markdown/CSV) com taxa de acerto por intenção.
Ferramenta interna: roda antes de qualquer mudança de prompt, sem impacto
em produção.
- Esforço: 2 dias.

### 1.11 Reorganizar o prompt em seções nomeadas
Extrair o prompt de `services/wa/brain.py` para um arquivo
`services/wa/prompt.md` versionado, com seções `## OBJETIVO`, `## INTENÇÕES`,
`## COMO AGIR`, `## NUNCA FAZER`. Facilita revisão e histórico de mudanças
sem mexer em código Python.
- Esforço: 1 dia.

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
