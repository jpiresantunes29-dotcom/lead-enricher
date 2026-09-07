# 📋 CHECKLIST DE PENDÊNCIAS — LeadEnricher

> **Data de atualização**: 2026-09-07  
> **Estado do projeto**: v2.x (FastAPI + Supabase Auth + Stripe + SQLAlchemy)  
> **Status geral**: ~75% do plano V3 implementado

---

## ⚠️ Conferência de 2026-09-07 — o que este documento dizia de errado

A versão anterior foi auditada linha a linha contra o código. Ela errava **nos
dois sentidos**, e o erro mais caro era o otimista: quem lesse o documento
priorizaria a coisa errada.

**Dava como pronto o que não existia:**

- **Fase 1 (Lead Scoring)** aparecia como `✅ 80% pronto`, "CRÍTICA". Estava em
  **0%**: não havia `services/lead_scorer.py`, nem as colunas `score` /
  `priority` / `score_breakdown` no modelo, nem uma única ocorrência de
  `score_lead` no projeto. Os "badges de prioridade 🔴🟡🔵" marcados como
  feitos também não existiam — os `priority` que apareciam em `app.js` eram
  prioridade de registro MX, outra coisa.
- `GET /api/dashboard/metrics` era descrito devolvendo `leads_por_prioridade`.
  Não devolvia: não havia de onde tirar.

**Dava como pendente o que já estava pronto** — a maior parte das Fases 2 a 5:

| Marcado PENDENTE | Onde já estava |
|---|---|
| Endpoints de atividades, follow-ups, `.ics` | `routers/activities.py` |
| Regras automáticas (`meeting_scheduled`, `no_answer`, dias úteis) | `services/activity_rules.py` |
| Migração de `activities` e `crm_connections` | `alembic/versions/0001`, linhas 186 e 56 |
| Rate limit por `sub` do JWT em vez de IP | `middleware/auth.py:692` |
| Webhook CRM assinado + tela de conexões | `services/crm/webhook.py`, `routers/crm_config.py` |
| AI Insights (dado como "0% implementado") | `services/ai_insights.py`, exposto em `routers/integrations.py` |

**Moral, para quem for atualizar isto:** marque estado por leitura do código,
não por memória da última sessão. Um checklist que mente para o lado otimista
é pior que não ter checklist — ele esconde justamente a lacuna crítica.

---

## 📊 VISÃO GERAL POR CATEGORIA

| Categoria | Status | Progresso | Prioridade |
|-----------|--------|-----------|-----------|
| **Core de Enriquecimento** | ✅ 90% pronto | Coleta, decisores | ALTA |
| **Lead Scoring** | ✅ 90% pronto | Régua, badge, popover, recálculo | ALTA |
| **Execução Comercial** | ✅ 85% pronto | Atividades, regras, `.ics`, pipeline | ALTA |
| **Dashboard Comercial** | ⚠️ 50% pronto | Endpoint pronto; faltam gráficos | ALTA |
| **Integrações CRM** | ⚠️ 35% pronto | Webhook assinado pronto; faltam Dynamics/HubSpot/Pipedrive | ALTA |
| **WhatsApp Business** | ⚠️ 60% pronto | Orquestrador e portão prontos; falta painel | CRÍTICA |
| **Landing V3** | ⚠️ 20% pronto | Design visual, marketing | MÉDIA |
| **Segurança & Conformidade** | ✅ 85% pronto | Rate limit, anti-SSRF e LGPD prontos | ALTA |
| **Infraestrutura & DevOps** | ✅ 80% pronto | Alembic, Supabase | MÉDIA |
| **Extensão Chrome** | ✅ 85% pronto | Pareamento, revelação | MÉDIA |
| **Provedores Premium** | ⚠️ 60% pronto | Hunter e **Lusha (BYOA) feitos**; Apollo/Dropcontact pendentes | BAIXA |
| **IA (Claude API)** | ⚠️ 60% pronto | Resumo executivo pronto; falta roteiro de ligação | MÉDIA |

---

## 🎯 FASE 1: LEAD SCORING (NÚCLEO — Prioridade CRÍTICA)

**Status**: ✅ Implementado em 2026-09-07 | Estimativa: 90% pronto

> Estava em **0%** apesar de o documento anterior dar como 80% — ver a
> conferência no topo. Implementado do zero nesta sessão.

### Implementação
- [x] `services/lead_scorer.py` — função pura `score_lead()`, sem banco, rede
      nem relógio (é o que permite testar os 14 sinais e recalcular em lote)
- [x] 14 sinais em três eixos, com peso deliberadamente desigual:
      **alcance** (decisores, e-mail verificado, telefone) pesa 38 dos 75
      pontos; **maturidade** (MX, SPF, DMARC, DKIM, hosting) 17; **porte e
      identidade** (tamanho, LinkedIn, setor, descrição, localização) 20.
      A pergunta que a nota responde é "consigo falar com quem decide?",
      não "esta empresa é bem configurada?"
- [x] Colunas em `Lead`: `score`, `priority`, `score_breakdown`, `score_version`
      (migração `0015_score_do_lead.py`, com índice em `score` e `priority`)
- [x] Cálculo ao fim da coleta — em `finish_enrichment`, o ponto por onde
      passam os dois caminhos (busca avulsa e fila de lote)
- [x] Recálculo pós-decisores (`POST /api/decisores`), depois do commit e lendo
      da relação: a ficha pode ter decisores de uma busca anterior
- [x] `POST /api/leads/{id}/rescore` — recálculo sob demanda, idempotente e
      sem rede. Cobre o que nenhum ponto automático pega: telefone corrigido à
      mão, contato revelado na Lusha, ficha pontuada por régua anterior
- [x] `GET /api/leads?sort=score&priority=alta` — ordenação e filtro
      (`nullslast`: ficha não pontuada vai para o fim, não para o topo)
- [ ] **PENDENTE**: Pesos configuráveis por usuário (tabela `scoring_profiles`)
- [ ] **PENDENTE**: Recálculo em lote ao subir `SCORING_VERSION` (hoje as fichas
      da régua antiga só são repontuadas na próxima coleta ou no rescore manual)
- [ ] **PENDENTE**: Histórico de evolução do score do lead

### UI/UX
- [x] Badge de prioridade (🔴🟡🔵) com a nota, no cabeçalho da ficha, na coluna
      nova do Histórico e no resumo rápido
- [x] Popover com o **detalhamento sinal a sinal** ("por quê?") — inclusive o
      que **não** pontuou, que é a lista do que buscar para o lead subir
- [x] Coluna "Prioridade" ordenável no Histórico
- [x] `GET /api/dashboard/metrics` passa a devolver `leads_por_prioridade`,
      `leads_nao_pontuados` e `score_medio`
- [ ] **PENDENTE**: Distribuição de score em histograma (ver Fase 3)

### Testes
- [x] `tests/test_lead_scorer.py` — 25 testes: ficha vazia, ficha completa,
      `dns_report` nulo ou com formato inesperado, decisor sem e-mails,
      a inversão alcance > maturidade, curva de porte, faixas de prioridade,
      detalhamento que fecha com a nota, e os quatro caminhos HTTP
      (coleta pontua, decisores repontuam, rescore, ordenação/filtro)

---

## 📞 FASE 2: EXECUÇÃO COMERCIAL (ATIVIDADES & PIPELINE — Prioridade CRÍTICA)

**Status**: ✅ 85% implementado (a versão anterior dizia 50%)

### Modelo de dados
- [x] Tabela `activities` com campos: `id`, `lead_id`, `user_id`, `type`, `outcome`, `notes`, `due_at`, `completed_at`
- [x] Campo `stage` em `Lead`: novo → contatado → reunião_agendada → oportunidade → ganho/perdido
- [x] Migração Alembic — já em `0001_schema_inicial.py:186`, não faltava
- [ ] **PENDENTE**: Índice em `(due_at)` para a query de follow-ups pendentes

### Regras automáticas (`services/activity_rules.py`)
- [x] `meeting_scheduled` → muda `lead.stage` + cria evento + gera `.ics`
- [x] `no_answer`/`voicemail` → follow-up em +2 dias úteis (`add_business_days`)
- [x] `busy` → follow-up
- [x] `talked` → move para `contatado`
- [ ] **PENDENTE**: Sync com CRM ao agendar reunião

### Endpoints (`routers/activities.py`)
- [x] `POST /api/leads/{id}/activities` — registrar ligação/nota/tarefa
- [x] `GET /api/leads/{id}/activities` — timeline do lead
- [x] `GET /api/activities/pending` — follow-ups vencendo
- [x] `PATCH /api/activities/{id}` — concluir/reagendar
- [x] `GET /api/activities/{id}/ics` — download do convite
- [x] `GET /api/followups/today` — fila do dia
- [x] `PATCH /api/leads/{id}/stage` — mover no pipeline (`routers/leads.py`)

### Calendário (.ics)
- [x] Geração de `.ics` (VCALENDAR) em `activity_rules.build_ics()`
- [x] Download direto, sem salvar no servidor
- [x] Pré-preenchimento com contexto do lead

### UI/UX
- [x] Ação rápida de registrar ligação (botões no card da ficha)
- [x] Fila do dia e aba de follow-ups
- [x] Timeline por lead no detalhe
- [x] Pipeline por estágio
- [ ] **PENDENTE**: Drag-and-drop de cards no kanban
- [ ] **PENDENTE**: Notificações de follow-ups atrasados

### Testes
- [x] `tests/test_activities.py` — regras automáticas, `.ics`, timeline, pendentes
- [ ] **PENDENTE**: Teste do índice/performance com volume

---

## 📊 FASE 3: DASHBOARD COMERCIAL (MÉTRICAS & ANALYTICS — Prioridade ALTA)

**Status**: ⚠️ 50% implementado — o endpoint está pronto; falta a visualização

### Endpoint de agregação
- [x] `GET /api/dashboard/metrics?days=30` — retorna:
  - `leads_pesquisados`, `ligacoes_realizadas`, `taxa_contato`, `taxa_reuniao`
  - `conversao_oportunidade`, `funil_por_estagio`
  - `leads_por_prioridade`, `leads_nao_pontuados`, `score_medio` *(2026-09-07)*
  - `followups_pendentes`, `followups_atrasados`
  - ⚠️ O parâmetro é `days`, não `period` — a versão anterior documentava errado
  - Nota: a distribuição por prioridade fica **fora** da janela de período de
    propósito. O funil responde "o que andou nos últimos 30 dias"; a prioridade
    responde "o que tenho para trabalhar amanhã", e recortar por data esconderia
    o lead bom que entrou há 40 dias e nunca foi tocado
- [ ] **PENDENTE**: Filtros por período (7d, 30d, 90d, custom)
- [ ] **PENDENTE**: Filtros por usuário / time
- [ ] **PENDENTE**: Comparação período anterior (delta %)
- [ ] **PENDENTE**: Export XLSX do dashboard (reutilizar `services/exporter.py`)

### UI — Nova aba "Dashboard"
- [ ] **PENDENTE**: Cards de KPI (leads pesquisados, taxa de contato, conversão)
- [ ] **PENDENTE**: Funil por estágio (visualização barras/funnelchart)
- [ ] **PENDENTE**: Distribuição de score (histograma, cores por prioridade)
- [ ] **PENDENTE**: Timeline de atividades (gráfico de linha: calls, meetings, deals over time)
- [ ] **PENDENTE**: Heatmap de dias/horários com mais contato
- [ ] **PENDENTE**: Ranking de melhores leads (por score, por stage)

### Gráficos
- [ ] **PENDENTE**: Avaliação: Chart.js vs D3 vs Recharts (sem dependência obrigatória)
- [ ] **PENDENTE**: Implementar com CSS/Canvas se possível; Chart.js se necessário

### Testes
- [ ] **PENDENTE**: Testes de agregação de métricas
- [ ] **PENDENTE**: Testes de período customizado

---

## 🔗 FASE 4: INTEGRAÇÕES CRM (SAÍDA PARA ECOSSISTEMA — Prioridade ALTA)

**Status**: ⚠️ 35% implementado — o webhook genérico está pronto ponta a ponta

### Arquitetura de conectores (`services/crm/`)
- [x] Protocolo `push_lead(lead, decision_makers, activities)` — `services/crm/webhook.py`
- [x] Credenciais criptografadas (Fernet, `services/crypto.py` + `SegredoCriptografado`)
- [x] Tabela `crm_connections` — já em `0001_schema_inicial.py:56`
- [x] Tela de conexões (`routers/crm_config.py`): listar, criar, ativar/desativar, remover
- [ ] **PENDENTE**: Teste de conexão antes de salvar credenciais ("Test connection")

### Conectores específicos

#### Dynamics 365
- [ ] **PENDENTE**: Autenticação OAuth2 client-credentials via Entra ID
- [ ] **PENDENTE**: Web API REST (`/api/data/v9.2/leads`)
- [ ] **PENDENTE**: Mapeamento: `Lead → lead`, atividade → `phonecall`, reunião → `appointment`
- [ ] **PENDENTE**: Sincronização de campos customizados

#### HubSpot
- [ ] **PENDENTE**: Autenticação com token privado
- [ ] **PENDENTE**: Mapeamento: `Lead → company+contact`, atividade → `engagement`
- [ ] **PENDENTE**: Sincronização automática de updates

#### Pipedrive
- [ ] **PENDENTE**: Autenticação com API token
- [ ] **PENDENTE**: Mapeamento: `Lead → organization+person+deal`
- [ ] **PENDENTE**: Estágios do pipeline mapeados automaticamente

#### Webhook genérico
- [x] POST JSON assinado (HMAC-SHA256) — `services/crm/webhook.py`
- [x] Cobertura para Zapier, Make, n8n (é HTTP + assinatura, serve para os três)
- [x] `allow_redirects=False` e validação do alvo (`is_valid_target`)
- [x] Chave de deduplicação (`dedup_key`) para não empurrar o mesmo lead duas vezes
- [ ] **PENDENTE**: Retry com backoff exponencial

### Sincronização
- [x] **Fase 1 (v1)**: Manual — botão "Enviar ao CRM" no detalhe do lead
- [ ] **PENDENTE (Fase 2)**: Automático por regra (`PATCH /api/leads/{id}/stage` → sync CRM)
- [ ] **PENDENTE**: Log de sincronização (sucesso/erro) para auditoria
- [ ] **PENDENTE**: Tratamento de conflitos (CRM foi atualizado externamente)

### Testes
- [ ] **PENDENTE**: Testes de mock para cada conector
- [ ] **PENDENTE**: Testes de criptografia de credenciais
- [ ] **PENDENTE**: Testes de reconexão após falha

---

## 🤖 FASE 5: INTELIGÊNCIA COM IA (Claude API — Prioridade MÉDIA)

**Status**: ⚠️ 60% implementado — o resumo executivo está no ar

### Serviço (`services/ai_insights.py`)
- [x] Resumo executivo da empresa (`generate_summary`, com contexto de
      descrição, setor, DNS e decisores), exposto em `routers/integrations.py`
- [x] Cache do resumo no `Lead` (coluna `ai_summary`)
- [x] `is_configured()` — degrada em silêncio quando não há chave
- [ ] **PENDENTE**: Limite de chamadas por plano (free = 0, pro/enterprise = ilimitado)

### Funcionalidades
- [x] Resumo: "Quem são, o que fazem, por que importa"
- [ ] **PENDENTE**: Roteiro de ligação personalizado (baseado em resumo + cargo do decisor + produto do usuário)
- [ ] **PENDENTE**: Sugestão de próxima ação (classificar notas de atividade, sugerir follow-up)
- [ ] ~~Melhor horário para contato~~ — **Adiado**: requer histórico volumoso

### UI/UX
- [x] Card "Insights de IA" no detalhe do lead (`ai-box`), com botão "regenerar"
- [ ] **PENDENTE**: Loading state enquanto gera

### Testes
- [ ] **PENDENTE**: Testes com mock da Claude API

---

## 💬 WHATSAPP BUSINESS (AUTOMAÇÃO — Prioridade CRÍTICA)

**Status**: 🔴 Iniciado (branch `feat/whatsapp-dynamics-fases-1-10`) | Estimativa: 3-4 sessões

### Configuração inicial
- [x] Rotas de webhook (`routers/wa.py`, `wa_connection.py`, `wa_sandbox.py`)
- [x] Autenticação com Meta Business Account
- [x] Sandbox de teste funcional
- [ ] **PENDENTE**: Guia de setup para o usuário final
- [ ] **PENDENTE**: Validação de número telefônico do negócio
- [ ] **PENDENTE**: Aprovação junto à Meta (submissão de aplicação)

### Orquestrador de turnos (`services/wa/orchestrator.py`)
- [x] Fluxo: portão → IA lê → ação → portão → envia
- [x] Tabela de ações por intenção (`_ACOES`)
- [x] Regra "Silêncio é falha" (→ `HUMAN_HANDOFF` se erro)
- [x] Regra "Na dúvida, não envia" (confiança baixa → humano)
- [x] Proteção contra turno rodar 2x pela mesma mensagem
- [ ] **PENDENTE**: Testes de ponta a ponta com sandbox

### Brain (classificação de intenção)
- [x] Módulo `services/wa/brain.py`
- [x] Intenções: CONFIRMOU_PESSOA, CONVERSANDO, QUER_HUMANO, NEGOCIANDO, PESSOA_ERRADA, FORA_DA_BASE, AMBIGUO, JA_E_CLIENTE, PEDIU_PARAR
- [ ] **PENDENTE**: Testes de precisão em português (LATEX, CNAE, domínios técnicos)
- [ ] **PENDENTE**: Prompt otimizado para o caso de uso (enriquecimento de lead)

### Gate de autorização
- [x] Módulo `services/wa/gate.py`
- [x] Proteção contra spam e abuso (MAX_TURNOS_FORA_DO_HORARIO = 3)
- [x] Silêncio entre 22h-8h (horário comercial)
- [ ] **PENDENTE**: Rate limiting por número de telefone
- [ ] **PENDENTE**: Detecção de números "bagunçados" (muito tráfego, múltiplos leads)

### Persistência de estado
- [x] Modelo `Conversation` e `WaMessage`
- [x] Histórico de mensagens indexado
- [x] Estados: WAITING, RESPONDED, HUMAN_HANDOFF, ENDED
- [ ] **PENDENTE**: Teste de recuperação em case de Vercel timeout

### Regras comerciais
- [x] Integração com `Lead` (associação bidirecional)
- [x] Associação automática de conversa a lead (match por telefone)
- [ ] **PENDENTE**: Atualização de `stage` do lead conforme progresso (CONVERSANDO → CONTATADO → etc)
- [ ] **PENDENTE**: Registro automático de atividade quando conversa progride
- [ ] **PENDENTE**: Handoff → cria task para humano com contexto completo

### Envio de mensagens
- [x] Cliente `services/wa/client.py` — comunicação com Meta Cloud API
- [x] Retry com backoff exponencial
- [ ] **PENDENTE**: Tratamento de erros Meta (número inválido, restringido, etc)
- [ ] **PENDENTE**: Logging estruturado de cada envio (para auditoria)

### UI - Painel de controle
- [ ] **PENDENTE**: Aba "WhatsApp" no app (lista de conversas)
- [ ] **PENDENTE**: Detalhe da conversa com histórico completo
- [ ] **PENDENTE**: Campo para "Assumir conversa" (humano passa para modo manual)
- [ ] **PENDENTE**: Badge de status (automático, handoff, ended)
- [ ] **PENDENTE**: Botão "Responder" para humano escrever manualmente

### Testes
- [x] `tests/test_wa_cliente.py` — testes de client
- [ ] **PENDENTE**: `tests/test_wa_orchestrator.py` — testes de orquestrador
- [ ] **PENDENTE**: `tests/test_wa_brain.py` — testes de classificação de intenção
- [ ] **PENDENTE**: `tests/test_wa_gate.py` — testes de gate de autorização
- [ ] **PENDENTE**: Teste de integração end-to-end

---

## 🎨 LANDING PAGE V3 (DESIGN & MARKETING — Prioridade MÉDIA)

**Status**: ⚠️ 20% implementado (esqueleto criado) | Estimativa: 2-3 sessões

### Setup (pronto)
- [x] Rotas divididas: `/app` (canônico), `/landing` (preview dev, noindex), `/` (híbrida temporária)
- [x] Template `templates/landing.html`
- [x] Assets em `static/landing/`
- [x] Vendor self-hosted: GSAP 3.13.0 + ScrollTrigger + Lenis 1.3.4
- [ ] **PENDENTE**: Adicionar `/app` à allowlist de Redirect URLs no Supabase (confirmação com config)

### Design Visual
- [ ] **PENDENTE**: Paleta Carvão & Ouro implementada (CSS custom properties)
- [ ] **PENDENTE**: Tipografia: Space Grotesk (display) + Inter (corpo) + JetBrains Mono (dados)
- [ ] **PENDENTE**: Grid de PCB (background-image CSS em camadas)
- [ ] **PENDENTE**: Film grain (PNG tile overlay)
- [ ] **PENDENTE**: Vinheta (radial-gradient)
- [ ] **PENDENTE**: Parallax (3 planos)

### Hero Section
- [ ] **PENDENTE**: Input de domínio (seletor/autofocus)
- [ ] **PENDENTE**: Animação de envio (simulado, sem realmente enriquecer)
- [ ] **PENDENTE**: Resultado fake bem formatado (DNS, decisores, score)

### Data Spine (motivo de assinatura)
- [ ] **PENDENTE**: SVG contínua percorrendo a página
- [ ] **PENDENTE**: Pulso dourado sincronizado com scroll (ScrollTrigger)
- [ ] **PENDENTE**: Ramificações para seções
- [ ] **PENDENTE**: Versão mobile (linha vertical simplificada)

### Seções de conteúdo
- [ ] **PENDENTE**: Ato I — Promessa (boot, hero, logos/fé)
- [ ] **PENDENTE**: Ato II — Prova (scrollytelling "Jornada do Domínio", 4 estações; bento de features; pipeline vivo; dashboard)
- [ ] **PENDENTE**: Ato III — Decisão (comparação, prova social, pricing, CTA final)

### Interatividades
- [ ] **PENDENTE**: Terminal vivo (simula DNS lookup)
- [ ] **PENDENTE**: Network animation (simula decisor sendo encontrado)
- [ ] **PENDENTE**: Demo ao vivo (sem realmente enriquecer; dados fake)
- [ ] **PENDENTE**: Scroll trigger reveais

### Copy (conteúdo)
- [ ] **PENDENTE**: Manifesto "Todo domínio esconde uma empresa inteira. Nós acendemos as luzes."
- [ ] **PENDENTE**: Headlines para cada seção
- [ ] **PENDENTE**: CTAs (Sign up, Try now, Learn more)
- [ ] **PENDENTE**: Prova social (logos de clientes, testimonials)

### Performance & SEO
- [ ] **PENDENTE**: Imagens otimizadas (WebP, srcset)
- [ ] **PENDENTE**: Lazy loading de scripts (GSAP, Lenis)
- [ ] **PENDENTE**: Core Web Vitals (LCP, CLS, FID)
- [ ] **PENDENTE**: Meta tags (OG, Twitter)
- [ ] **PENDENTE**: Sitemap + robots.txt (com `/landing` noindex)

### Testes
- [ ] **PENDENTE**: Teste de responsividade (mobile, tablet, desktop)
- [ ] **PENDENTE**: Teste de performance (Lighthouse)
- [ ] **PENDENTE**: Teste de acessibilidade (WCAG 2.1 AA)

---

## 🔐 SEGURANÇA & CONFORMIDADE (CROSS-CUTTING — Prioridade ALTA)

**Status**: ✅ 85% implementado (a versão anterior dizia 70% e listava como
pendente três travas que já existiam)

### Rate limiting
- [x] Base: `slowapi` com `key_func=rate_limit_key` (60/min)
- [x] Chave por `sub` do JWT, com queda para IP — `middleware/auth.py:692`.
      Já estava feito: atrás de proxy todos compartilham IP, e limitar só por
      IP puniria todos os usuários juntos
- [x] Limite específico em `/enrich` (10/min) e `/api/decisores` (20/min)
- [ ] **PENDENTE**: Rate limit diferenciado por plano (free: 60/min, pro: 600/min)
- [ ] **PENDENTE**: Rate limit no `/reveal` (hoje sem limite explícito)

### Anti-SSRF
- [x] `is_public_host()` / `is_public_url()` em `services/_utils.py` — resolve o
      host e recusa se **qualquer** IP cair em faixa privada, loopback,
      link-local (que cobre o metadata `169.254.169.254` de AWS, GCP e Azure),
      reservada, multicast ou não especificada
- [x] Aplicado em `scraper._fetch()` e `dns_intel.fetch_http_banner()`
- [x] Timeout de conexão de 5s (tupla `(connect, read)`)
- [x] **Revalidação a cada redirect** — `safe_get()`, 2026-09-07. Era o buraco
      real: a checagem valia só para a URL inicial, e `allow_redirects=True`
      seguia o `Location` sem perguntar de novo. Um domínio público
      respondendo `302` para `169.254.169.254` ou `10.0.0.5` passava por dentro
      do guard. Agora cada salto é resolvido e validado antes de ser buscado,
      com teto de 30 saltos
- [x] `tests/test_seguranca.py` — quatro alvos internos parametrizados
      (metadata, rede privada, loopback, roteador), cada um **alcançável na
      resposta falsa**, para o teste falhar de verdade se o guard cair;
      mais redirect relativo legítimo, cadeia longa demais e fechamento do
      salto intermediário

### Criptografia de credenciais
- [x] Base: `services/crypto.py` com Fernet
- [x] Aplicado via o tipo `SegredoCriptografado` em `crm_connections.credentials`,
      `profiles.lusha_api_key` e nas credenciais de WhatsApp
- [x] Descriptografia só no uso — nunca sai em JSON
- [x] `tests/test_segredos.py` cobre o ciclo e a chave trocada

### HMAC em webhooks de saída
- [x] Assinatura HMAC-SHA256 em `services/crm/webhook.py`
- [ ] **PENDENTE**: Documentação pública: como o destinatário verifica a assinatura

### Trilha de auditoria
- [x] Tabela `activities` como audit log comercial
- [ ] **PENDENTE**: Logs estruturados (JSON) para eventos sensíveis:
  - Conexão CRM criada/modificada
  - Export em massa (>100 leads)
  - Mudança de plano
  - Mudança de senha
  - Acesso a dados de outro usuário (403)
- [ ] **PENDENTE**: Retenção de logs (90 dias min, 1 ano ideal)

### LGPD
- [x] Oposição/eliminação (`/remover-meus-dados` público, sem cadastro)
- [x] Sem nova base de dados pessoais (guarda SHA-256)
- [x] Bloqueio definitivo na entrega e gravação
- [x] Anti-enumeração (resposta genérica idêntica)
- [ ] **PENDENTE**: Origem de cada dado (coluna `source` + `verified_at` — verificar se 100% dos dados têm isso)
- [ ] **PENDENTE**: Retenção de dados (purga decisores de leads inativos > N meses)
- [ ] **PENDENTE**: Documentação de finalidade (para DPPO/DPO responder SAR)

### Migrações versionadas
- [x] `alembic` setup
- [x] Migrations 0001–0015 (a última: `0015_score_do_lead.py`)
- [x] `activities` e `crm_connections` já vinham na `0001` — não faltavam
- [x] `tests/test_migracoes.py` constrói um banco pelas migrações e compara com
      os modelos, e confere que `ALEMBIC_HEAD` acompanha a última revisão
- [ ] **PENDENTE**: Migração de `scoring_profiles` (pesos por usuário)
- [ ] **PENDENTE**: Teste de rollback (downgrade safety)
- [ ] **PENDENTE**: CI/CD check: migração falha na PR

---

## 🏗️ INFRAESTRUTURA & DEVOPS (FOUNDATION — Prioridade MÉDIA)

**Status**: ✅ 80% implementado | Estimativa: 0.5 sessões

### Banco de dados
- [x] Supabase + PostgreSQL
- [x] Alembic para versionamento
- [x] RLS (Row Level Security) habilitado
- [ ] **PENDENTE**: Índices de performance (verificar plano de query `EXPLAIN`)
- [ ] **PENDENTE**: Backup automático documentado (Vercel + Supabase)
- [ ] **PENDENTE**: Disaster recovery playbook

### Auth
- [x] Supabase Auth (JWT)
- [x] JWKS + verificação por chave pública
- [ ] **PENDENTE**: Fallback SUPABASE_JWT_SECRET documentado
- [ ] **PENDENTE**: Refresh token rotation
- [ ] **PENDENTE**: SSO (SAML/OIDC) para enterprise

### Deployment
- [x] Vercel + git push auto-deploy
- [x] .vercelignore configurado
- [ ] **PENDENTE**: Environment variables em `.vercel/env`
- [ ] **PENDENTE**: Staging branch (`staging`) com deploy separado
- [ ] **PENDENTE**: Rollback automático se health check falhar

### Monitoramento
- [x] Logging estruturado (JSON)
- [ ] **PENDENTE**: Sentry para exceções (ou similar)
- [ ] **PENDENTE**: Prometheus/StatsD para métricas (latência, throughput, erros)
- [ ] **PENDENTE**: Alertas: 5xx errors, rate limit exceeded, slow queries

### CI/CD
- [ ] **PENDENTE**: GitHub Actions: lint (ruff, mypy)
- [ ] **PENDENTE**: GitHub Actions: testes (pytest)
- [ ] **PENDENTE**: GitHub Actions: segurança (bandit, safety)
- [ ] **PENDENTE**: GitHub Actions: migração safe (no down time)
- [ ] **PENDENTE**: Branch protection rules (require CI pass)

---

## 🧩 EXTENSÃO CHROME (MV3 — Prioridade MÉDIA)

**Status**: ✅ 85% implementado | Estimativa: 0.5 sessões

### Funcionalidade
- [x] Pareamento com o app (`pair-code` + `pair`)
- [x] Extração de dados do LinkedIn (DOM scrape: nome, cargo, empresa)
- [x] Envio para `/api/extension/reveal` (revelação de contato)
- [x] Máscara prévia (sem nome completo)
- [x] Salvamento direto na pipeline
- [ ] **PENDENTE**: Indicador visual "já revelado" (cache)

### Ícones & branding
- [ ] **PENDENTE**: Ícones próprios (16x, 48x, 128x)
- [ ] **PENDENTE**: Banner para Chrome Web Store
- [ ] **PENDENTE**: Descrição & screenshots para submissão

### Submissão Chrome Web Store
- [ ] **PENDENTE**: Preparar arquivo .zip para submissão
- [ ] **PENDENTE**: Política de privacidade alinhada
- [ ] **PENDENTE**: Submeter para review (pode levar 1-2 semanas)

### Testes
- [ ] **PENDENTE**: Teste de pareamento
- [ ] **PENDENTE**: Teste de extração (com mock LinkedIn)
- [ ] **PENDENTE**: Teste de offline behavior

---

## 💳 PROVEDORES PREMIUM (OPCIONAL — Prioridade BAIXA)

**Status**: 🔴 0% implementado | Estimativa: 2-3 sessões (futuro)

### Arquitetura plugável (`services/providers/`)
- [x] Base: protocolo `EnrichmentProvider` + `CRMConnector`
- [ ] **PENDENTE**: Sistema de registro (`CONTACT_FINDERS`, `EMAIL_VERIFIERS`)
- [ ] **PENDENTE**: Fallback automático (se API falha, usa gratuita)
- [ ] **PENDENTE**: Billing integrado (Stripe): custo da API + margem do app

### Provedores sugeridos (por custo-benefício no Brasil)
1. **Dropcontact / Prospeo** (e-mail)
   - [ ] **PENDENTE**: Módulo `services/providers/dropcontact.py`
   - [ ] **PENDENTE**: Dedupe com padrão local
   - [ ] **PENDENTE**: Testes

2. **Apollo / People Data Labs** (celular)
   - [ ] **PENDENTE**: Módulo `services/providers/apollo.py`
   - [ ] **PENDENTE**: Priorização por confiança
   - [ ] **PENDENTE**: Testes

3. **Lusha** ✅ feito em 2026-09-06 — no modelo **BYOA**: cada usuário conecta a
   própria chave e gasta os próprios créditos, então não há custo para a
   instalação e o produto continua inteiro para quem não conecta.
   - [x] `services/providers/lusha.py` — revela uma pessoa pelo nome (`/v2/person`)
   - [x] `services/providers/lusha_prospecting.py` — lista contatos de uma
         empresa (search, 1 crédito/25) e revela sob clique (enrich, 1 crédito
         por e-mail e 5 por telefone)
   - [x] `GET /api/leads/{id}/contacts`, `POST /api/decision-makers/{id}/reveal`,
         `GET /api/lusha/filters`
   - [x] Tela com filtros, paginação e custo escrito no botão antes do clique
   - [x] 87 testes (60 de provedor, 27 de endpoint)
   - [ ] **PENDENTE (bloqueado por credencial)**: capturar a resposta real da
         API numa fixture. O parser está escrito contra o formato
         **documentado**, não contra o observado — o mesmo tipo de risco que
         fez a primeira tentativa chamar o endpoint errado.
   - [x] **Ferramenta pronta** *(2026-09-07)*:
         `scripts/capturar_fixtures_lusha.py` faz a captura num comando só e
         ainda compara o que voltou com o que `parse_contact()` consegue ler,
         apontando campo por campo onde o nome diverge. Substitui os quatro
         passos manuais da §11.

         ```bash
         LUSHA_API_KEY=... python -m scripts.capturar_fixtures_lusha --enrich
         ```

         Não foi executado porque **não há chave disponível**: o modelo é BYOA
         e cada usuário guarda a própria chave criptografada em
         `profiles.lusha_api_key`. Custa 1 crédito (search) + 1 por e-mail
         revelado, e os créditos são de quem executa.

### Testes
- [x] Fallback quando o provedor falha — coberto para a Lusha
      (`test_contatos_prospecting.py`): sem chave, 401, 402, 429 e Lusha fora
      do ar caem no caminho gratuito sem derrubar a tela
- [x] Não cobrar duas vezes pelo mesmo dado — revelar um contato já revelado
      devolve o gravado sem chamar a Lusha
- [ ] **PENDENTE**: Testes de dedupe entre provedores (só faz sentido com dois
      provedores pagos ativos)
- [ ] **PENDENTE**: Testes de billing para Apollo/Dropcontact

---

## 📝 CONFIGURAÇÕES & SETUP (USUÁRIO FINAL — Prioridade MÉDIA)

**Status**: ⚠️ 50% implementado | Estimativa: 1-2 sessões

### Configuração de perfil
- [x] Fields básicos: nome, email, plano
- [ ] **PENDENTE**: "O que você vende?" (para roteiro de ligação IA)
- [ ] **PENDENTE**: Timezone (para fila do dia, follow-ups)
- [ ] **PENDENTE**: Horário comercial (para automação WhatsApp)

### Integração CRM
- [ ] **PENDENTE**: Página de setup com wizard
- [ ] **PENDENTE**: Step-by-step: Dynamics 365
- [ ] **PENDENTE**: Step-by-step: HubSpot
- [ ] **PENDENTE**: Step-by-step: Pipedrive
- [ ] **PENDENTE**: Step-by-step: Webhook genérico
- [ ] **PENDENTE**: Teste de conexão (botão "Test connection")
- [ ] **PENDENTE**: Revogação de credenciais (botão "Disconnect")

### WhatsApp Business
- [ ] **PENDENTE**: Guia de obtenção de Meta Business Account
- [ ] **PENDENTE**: Guia de criação de app no Meta Developers
- [ ] **PENDENTE**: Guia de verificação de número
- [ ] **PENDENTE**: Teste de webhook (Vercel logs)
- [ ] **PENDENTE**: Status de aprovação (badge no painel)

### Extensão Chrome
- [ ] **PENDENTE**: Guia de instalação da extensão
- [ ] **PENDENTE**: Guia de pareamento (código 6 dígitos)
- [ ] **PENDENTE**: Verificação de permissões (ativa/inativa)

### Provedores premium
- [ ] **PENDENTE**: Página de ativar/desativar por plano
- [ ] **PENDENTE**: Instruções de API key (Hunter, Apollo, etc)
- [ ] **PENDENTE**: Teste de credenciais antes de salvar

### Documentação
- [ ] **PENDENTE**: Onboarding doc (passo a passo)
- [ ] **PENDENTE**: API docs (OpenAPI/Swagger)
- [ ] **PENDENTE**: Troubleshooting (FAQ)
- [ ] **PENDENTE**: Vídeo tutorial (3-5 min)

---

## 🧪 TESTES & QUALIDADE (CROSS-CUTTING — Prioridade ALTA)

**Status**: ⚠️ 60% implementado | Estimativa: 1-2 sessões

**Suíte atual: 926 testes passando** (`python -m pytest -q`).

> ⚠️ **Instabilidade conhecida, pré-existente**: rodando a suíte inteira,
> alguns testes de WhatsApp falham de forma intermitente — testes diferentes a
> cada rodada (`test_wa_conversas.py`, `test_wa_jornada.py`). Passam quando
> rodados isolados ou por arquivo. Confirmado que **não** vem das mudanças de
> 2026-09-07: reproduz na base limpa. Hipótese: vazamento de estado entre
> testes — as asserções dependem de contagem global (`/api/wa/status`) e do
> primeiro item da lista (`conversations[0]`).

### Testes unitários
- [x] `test_precisao.py` (Nubank, 2.6 mi de funcionários, etc)
- [x] `test_extension.py`
- [x] `test_wa_cliente.py`
- [x] `test_lead_scorer.py` — 25 testes *(2026-09-07)*
- [x] `test_activities.py` — cobre as regras de `activity_rules.py`
- [x] `test_people.py`, `test_phone_normalizer.py`, `test_dns_intel.py`
- [x] `test_seguranca.py` — JWT, cabeçalhos e **anti-SSRF em redirects**
- [ ] **PENDENTE**: `test_email_patterns.py` dedicado

### Testes de integração
- [ ] **PENDENTE**: `test_crm_connectors.py` (mocks Dynamics, HubSpot, Pipedrive)
- [ ] **PENDENTE**: `test_wa_orchestrator.py` (mock Meta API)
- [ ] **PENDENTE**: `test_enrich_full.py` (domain → complete lead)

### Testes de carga
- [ ] **PENDENTE**: K6 / Locust: teste de 100 enriquecimentos simultâneos
- [ ] **PENDENTE**: Teste de timeout (>12s no `/reveal`)

### Cobertura
- [ ] **PENDENTE**: Meta: 80% coverage em `services/`
- [ ] **PENDENTE**: Meta: 90% coverage em `models/`
- [ ] **PENDENTE**: CI/CD: fail se coverage cai

### Type hints
- [x] FastAPI usa Pydantic (type-safe)
- [ ] **PENDENTE**: Mypy: checar `services/`, `routers/`, `models/`
- [ ] **PENDENTE**: CI/CD: mypy strict no pre-commit

### Linting
- [ ] **PENDENTE**: Ruff: style & imports
- [ ] **PENDENTE**: Pylint: código suspeito
- [ ] **PENDENTE**: Black: formatação automática

### Documentação de código
- [ ] **PENDENTE**: Docstrings em funções públicas (Google style)
- [ ] **PENDENTE**: Exemplos em routers (FastAPI /docs)

---

## 📞 SUPORTE & SLA (OPERACIONAL — Prioridade BAIXA)

**Status**: 🔴 0% implementado | Estimativa: futuro

- [ ] **PENDENTE**: Página de status (status.leadenricher.com)
- [ ] **PENDENTE**: Política de SLA (99.9% uptime)
- [ ] **PENDENTE**: On-call schedule
- [ ] **PENDENTE**: Incident response playbook
- [ ] **PENDENTE**: Customer support email / chat

---

## 📅 ROADMAP CONSOLIDADO

> A versão anterior marcava **todos** os itens do roadmap com ✅, inclusive os
> que nunca começaram — o ✅ ali significava "planejado", não "pronto". Isso
> tornava o roadmap ilegível. Aqui ✅ é feito, ⬜ é a fazer.

### **Sprint 1 — Fundação comercial** ✅ concluído
- ✅ Tabela de atividades + regras automáticas
- ✅ Endpoints de atividades e `.ics`
- ✅ Lead scoring completo, com popover de detalhamento *(2026-09-07)*

### **Sprint 2 (AGORA) — Dashboard & CRM**
- ✅ Endpoint de métricas (com distribuição por prioridade)
- ✅ Webhook CRM assinado + tela de conexões
- ⬜ **UI do dashboard**: KPIs, funil, histograma de score — o endpoint já
      entrega tudo o que os gráficos precisam
- ⬜ Conector Dynamics 365
- ⬜ Conector HubSpot
- **Duração**: 2-3 sessões

### **Sprint 3 — WhatsApp**
- ✅ Orquestrador, portão, brain e persistência
- ✅ Testes de cliente, portão, turno e jornada
- ⬜ Painel de conversas (aba no app)
- ⬜ Handoff com contexto para o humano
- ⬜ Meta approval + submissão
- **Duração**: 2-3 sessões

### **Sprint 4 — Landing & IA**
- ✅ Integração Claude API + resumo executivo
- ⬜ Landing Page V3 (design, scrollytelling, copy)
- ⬜ Roteiro de ligação personalizado
- **Duração**: 2-3 sessões

### **Sprint 5 — Provedores & Polish**
- ✅ Anti-SSRF, rate limit por usuário, criptografia, LGPD
- ⬜ Fixture real da Lusha (ferramenta pronta; falta a chave)
- ⬜ Provedores premium (Dropcontact, Apollo)
- ⬜ CI/CD (ruff, mypy, pytest, bandit)
- ⬜ Suíte determinística (ver instabilidade de WhatsApp em Testes)
- **Duração**: 2-3 sessões

### **Ongoing**
- Monitoramento & alertas
- Patches & bug fixes
- Customer support
- SEO & marketing

---

## 🎓 NOTAS IMPORTANTES

1. **Marcas de status**:
   - ✅ Pronto ou em uso
   - ⚠️ Parcialmente implementado
   - 🔴 Não iniciado
   - ~~Riscado~~ Adiado indefinidamente

2. **Estimativas** são em "sessões de código" (2-4h cada). Tempo real depende de:
   - Testes necessários
   - Integração com sistemas externos (Meta, Supabase, etc)
   - Reviews e feedback

3. **Prioridades**:
   - 🔴 CRÍTICA: bloqueia outras features ou receita
   - 🔴 ALTA: vai pro produto
   - 🟡 MÉDIA: importante mas pode esperar
   - 🟢 BAIXA: nice-to-have

4. **Dependências**: A ordem sugerida no roadmap respeita dependências (ex: dashboard precisa de atividades antes).

5. **Branches ativos**:
   - `feat/whatsapp-dynamics-fases-1-10` — WhatsApp em andamento
   - `fix/diagnostico-de-login` — Auth fix
   - `fix/login-oauth-silencioso` — OAuth silent mode
   - `backup/pre-reconstrucao` — backup antigo

---

## 📊 PRÓXIMAS AÇÕES

**Feito em 2026-09-07:**
- [x] Lead scoring completo — régua, colunas, migração `0015`, cálculo na
      coleta, recálculo pós-decisores, `POST /leads/{id}/rescore`, ordenação e
      filtro na listagem, badge, popover de detalhamento e métricas do dashboard
- [x] Anti-SSRF revalidando cada salto de redirect (`safe_get`)
- [x] `scripts/capturar_fixtures_lusha.py` — fecha a §11 num comando
- [x] Este checklist conferido linha a linha contra o código

**Próximo (Sprint 2):**
1. [ ] **UI do dashboard** — é o maior retorno agora: o endpoint já entrega
       KPIs, funil, distribuição por prioridade e score médio, e nada disso
       aparece na tela
2. [ ] Conector Dynamics 365 (OAuth2 via Entra ID)
3. [ ] Conector HubSpot

**Dívidas que valem uma sessão curta:**
- [ ] Suíte de testes determinística (instabilidade de WhatsApp — ver Testes)
- [ ] CI/CD: ruff + mypy + pytest na PR
- [ ] Fixture real da Lusha, quando houver uma chave à mão (1 crédito)

---

**Última atualização**: 2026-09-07 por Claude  
**Mantido em**: a raiz do repositório — `CHECKLIST_PENDENCIAS.md`
(a versão anterior dizia `/docs/`, onde o arquivo não está).
A versão em HTML (`CHECKLIST_PENDENCIAS.html`) é escrita à mão e espelha este
arquivo; ao editar um, atualize o outro.
