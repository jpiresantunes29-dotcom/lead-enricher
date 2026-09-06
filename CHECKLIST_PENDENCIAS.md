# 📋 CHECKLIST DE PENDÊNCIAS — LeadEnricher

> **Data de atualização**: 2026-09-06  
> **Estado do projeto**: v2.x (FastAPI + Supabase Auth + Stripe + SQLAlchemy)  
> **Status geral**: ~60% do plano V3 está implementado; 40% em desenvolvimento ou pendente

---

## 📊 VISÃO GERAL POR CATEGORIA

| Categoria | Status | Progresso | Prioridade |
|-----------|--------|-----------|-----------|
| **Core de Enriquecimento** | ✅ 90% pronto | Lead scoring, decisores | ALTA |
| **Execução Comercial** | ⚠️ 50% pronto | Atividades, pipeline | ALTA |
| **Dashboard Comercial** | ⚠️ 30% pronto | Métricas, KPIs | ALTA |
| **Integrações CRM** | 🔴 10% pronto | Dynamics, HubSpot, Pipedrive | ALTA |
| **WhatsApp Business** | 🔴 Iniciado | Automação de conversa | CRÍTICA |
| **Landing V3** | ⚠️ 20% pronto | Design visual, marketing | MÉDIA |
| **Segurança & Conformidade** | ⚠️ 70% pronto | Rate limit, LGPD, Anti-SSRF | ALTA |
| **Infraestrutura & DevOps** | ✅ 80% pronto | Alembic, Supabase | MÉDIA |
| **Extensão Chrome** | ✅ 85% pronto | Pareamento, revelação | MÉDIA |
| **Provedores Premium** | 🔴 0% pronto | Hunter, Apollo, Lusha | BAIXA |

---

## 🎯 FASE 1: LEAD SCORING (NÚCLEO — Prioridade CRÍTICA)

**Status**: ✅ Parcialmente implementado | Estimativa: 80% pronto

### Implementação
- [x] Criar `services/lead_scorer.py` com função pura `score_lead()`
- [x] Critérios de scoring (14 sinais): MX provider, SPF/DMARC, hosting, tamanho, LinkedIn, decisores, e-mail SMTP, telefone
- [x] Colunas no modelo `Lead`: `score`, `priority`, `score_breakdown`, `score_version`
- [x] Cálculo ao fim do enriquecimento (`POST /enrich`)
- [x] Recálculo pós-decisores (`POST /api/decisores` → rescore automático)
- [ ] **PENDENTE**: Endpoint `POST /api/leads/{id}/rescore` para recálculo sob demanda
- [ ] **PENDENTE**: Pesos configuráveis por usuário (tabela `scoring_profiles`)
- [ ] **PENDENTE**: Versionamento de pesos (hoje só SCORING_V1)
- [ ] **PENDENTE**: Cache de scores para performance

### UI/UX
- [x] Badges de prioridade (🔴🟡🔵) nos cards de resultado
- [x] Exibição de score normalizado (0-100)
- [ ] **PENDENTE**: Popover interativo com **breakdown detalhado** ("Por que 47 pontos?")
- [ ] **PENDENTE**: Visualização de contribuição de cada critério
- [ ] **PENDENTE**: Histórico de evolução do score do lead

### Testes
- [x] Testes unitários de scoring
- [ ] **PENDENTE**: Testes de recálculo pós-decisores
- [ ] **PENDENTE**: Testes de casos extremos (empresas com 0 decisores, sem SPF, etc)

---

## 📞 FASE 2: EXECUÇÃO COMERCIAL (ATIVIDADES & PIPELINE — Prioridade CRÍTICA)

**Status**: ⚠️ 50% implementado | Estimativa: 2-3 sessões

### Modelo de dados
- [x] Tabela `activities` com campos: `id`, `lead_id`, `user_id`, `type`, `outcome`, `notes`, `due_at`, `completed_at`
- [x] Campo `stage` em `Lead`: novo → contatado → reunião_agendada → oportunidade → ganho/perdido
- [ ] **PENDENTE**: Migração Alembic versionada para `activities` table
- [ ] **PENDENTE**: Índices em `(lead_id, user_id)` e `(due_at)` para performance

### Regras automáticas (`services/activity_rules.py`)
- [ ] **PENDENTE**: Quando `meeting_scheduled` → muda `lead.stage` + cria evento + gera `.ics` + sync CRM
- [ ] **PENDENTE**: Quando `no_answer`/`voicemail` → cria task de follow-up +2 dias úteis
- [ ] **PENDENTE**: Quando `busy` → cria follow-up +1 dia útil
- [ ] **PENDENTE**: Quando `talked` → muda para `contatado` + sugere follow-up opcional

### Endpoints novos
- [ ] **PENDENTE**: `POST /api/leads/{id}/activities` — registrar ligação/nota/tarefa
- [ ] **PENDENTE**: `GET /api/leads/{id}/activities` — timeline do lead
- [ ] **PENDENTE**: `GET /api/activities/pending` — follow-ups vencendo (ordenado por `due_at`)
- [ ] **PENDENTE**: `PATCH /api/activities/{id}` — concluir/reagendar
- [ ] **PENDENTE**: `GET /api/activities/{id}/ics` — download convite `.ics`
- [ ] **PENDENTE**: `PATCH /api/leads/{id}/stage` — mover no pipeline
- [x] `GET /api/dashboard/metrics` — ver seção Dashboard

### Calendário (.ics)
- [ ] **PENDENTE**: Geração de `.ics` (VCALENDAR) universal (Outlook, Google, Apple Calendar)
- [ ] **PENDENTE**: Download direto sem salvar no servidor
- [ ] **PENDENTE**: Pré-preenchimento com contexto do lead (titulo, descrição, local)

### UI/UX
- [ ] **PENDENTE**: Visão alternável lista/kanban por estágio
- [ ] **PENDENTE**: Drag-and-drop de cards de lead (muda `stage` via `PATCH`)
- [ ] **PENDENTE**: Ação rápida "📞 Registrar ligação" — modal em 2 cliques
- [ ] **PENDENTE**: Fila do dia: seção "Follow-ups de hoje" no topo (consumindo `/api/activities/pending`)
- [ ] **PENDENTE**: Timeline por lead no detalhe (histórico de atividades)
- [ ] **PENDENTE**: Notificações de follow-ups atrasados

### Testes
- [ ] **PENDENTE**: Testes de regras automáticas (meeting_scheduled, no_answer, etc)
- [ ] **PENDENTE**: Testes de geração `.ics`
- [ ] **PENDENTE**: Testes de transação única (sem fila)

---

## 📊 FASE 3: DASHBOARD COMERCIAL (MÉTRICAS & ANALYTICS — Prioridade ALTA)

**Status**: 🔴 10% implementado | Estimativa: 1-2 sessões

### Endpoint de agregação
- [x] `GET /api/dashboard/metrics?period=30d` — retorna:
  - `leads_pesquisados`, `ligacoes_realizadas`, `taxa_contato`, `taxa_reuniao`
  - `conversao_oportunidade`, `funil_por_estagio`, `leads_por_prioridade`
  - `followups_pendentes`, `followups_atrasados`
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

**Status**: 🔴 ~5% implementado | Estimativa: 2-3 sessões

### Arquitetura de conectores (`services/crm/`)
- [ ] **PENDENTE**: Definir protocolo `CRMConnector.push_lead(lead, decision_makers, activities)`
- [ ] **PENDENTE**: Camada de credenciais criptografadas (Fernet, em env)
- [ ] **PENDENTE**: Tabela `crm_connections` com `credentials` **criptografado**
- [ ] **PENDENTE**: Teste de conexão antes de salvar credenciais

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
- [ ] **PENDENTE**: POST JSON assinado (HMAC-SHA256)
- [ ] **PENDENTE**: Cobertura para Zapier, Make, n8n
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

**Status**: 🔴 0% implementado | Estimativa: 1 sessão

### Serviço (`services/ai_insights.py`)
- [ ] **PENDENTE**: Resumo executivo da empresa (prompt com description + sector + DNS + decisores)
- [ ] **PENDENTE**: Cache do resumo no `Lead` (coluna `ai_summary`)
- [ ] **PENDENTE**: Limite de chamadas por plano (free = 0, pro/enterprise = ilimitado)

### Funcionalidades
- [ ] **PENDENTE**: Resumo: "Quem são, o que fazem, por que importa"
- [ ] **PENDENTE**: Roteiro de ligação personalizado (baseado em resumo + cargo do decisor + produto do usuário)
- [ ] **PENDENTE**: Sugestão de próxima ação (classificar notas de atividade, sugerir follow-up)
- [ ] ~~Melhor horário para contato~~ — **Adiado**: requer histórico volumoso

### UI/UX
- [ ] **PENDENTE**: Card "Insights de IA" no detalhe do lead
- [ ] **PENDENTE**: Loading state enquanto gera
- [ ] **PENDENTE**: Botão "Regenerar" para refazer

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

**Status**: ⚠️ 70% implementado | Estimativa: 1-2 sessões

### Rate limiting
- [x] Base: `slowapi` com `key_func=rate_limit_key` (60/min)
- [ ] **PENDENTE**: Trocar `get_remote_address` por `sub` do JWT (por usuário, não por IP)
- [ ] **PENDENTE**: Rate limit diferenciado por plano (free: 60/min, pro: 600/min)
- [ ] **PENDENTE**: Rate limit no `/reveal` (hoje sem limite explícito)
- [ ] **PENDENTE**: Rate limit no `/api/activities/pending` (query pesada)

### Anti-SSRF
- [ ] **PENDENTE**: `scrape_website()` — resolver IP antes do fetch
- [ ] **PENDENTE**: Bloquear faixas privadas: `10/8`, `172.16/12`, `192.168/16`, `127/8`, `169.254/16`
- [ ] **PENDENTE**: Bloquear IP de metadata (AWS, Google Cloud, Azure)
- [ ] **PENDENTE**: Timeout de conexão (5s max)

### Criptografia de credenciais
- [x] Base: `services/crypto.py` com Fernet
- [ ] **PENDENTE**: Aplicar em `crm_connections.credentials` antes de salvar
- [ ] **PENDENTE**: Descriptografar apenas ao usar (nunca retornar em JSON)
- [ ] **PENDENTE**: Teste de descriptografia falhada (chave perdida)

### HMAC em webhooks de saída
- [ ] **PENDENTE**: Assinatura HMAC-SHA256 em `POST` para webhook customizado
- [ ] **PENDENTE**: Header `X-LeadEnricher-Signature`
- [ ] **PENDENTE**: Documentação: como verificar a assinatura

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
- [x] Migrations: 0001-0008 já existem
- [ ] **PENDENTE**: Migração para tabelas novas (activities, crm_connections, scoring_profiles)
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

3. **Lusha** (legacy — consideração futura)
   - [ ] **PENDENTE**: Módulo `services/providers/lusha.py` (se houver ROI)

### Testes
- [ ] **PENDENTE**: Testes de fallback (API provider falha)
- [ ] **PENDENTE**: Testes de dedupe
- [ ] **PENDENTE**: Testes de billing

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

### Testes unitários
- [x] `test_precisao.py` (Nubank, 2.6 mi de funcionários, etc)
- [x] `test_extension.py`
- [x] `test_wa_cliente.py`
- [ ] **PENDENTE**: `test_lead_scorer.py` (100% de coverage)
- [ ] **PENDENTE**: `test_activity_rules.py`
- [ ] **PENDENTE**: `test_email_patterns.py`
- [ ] **PENDENTE**: `test_dns_lookup.py`

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

### **Sprint 1 (AGORA)** — Fundação comercial
- ✅ Lead scoring completo (popover breakdown)
- ✅ Tabela de atividades + regras automáticas
- ✅ Endpoints de atividades
- ✅ `.ics` generation
- **Duração**: 2-3 sessões

### **Sprint 2** — Dashboard & CRM
- ✅ Endpoint de métricas
- ✅ UI do dashboard (aba nova)
- ✅ Conector Dynamics 365
- ✅ Conector HubSpot
- **Duração**: 2-3 sessões

### **Sprint 3** — WhatsApp
- ✅ Testes de orchestrator + brain
- ✅ UI de conversas
- ✅ Handoff automático
- ✅ Meta approval + submissão
- **Duração**: 2-3 sessões

### **Sprint 4** — Landing & IA
- ✅ Landing Page V3 (completa)
- ✅ AI Insights (resumo + roteiro)
- ✅ Integração Claude API
- **Duração**: 2-3 sessões

### **Sprint 5** — Provedores & Polish
- ✅ Provedores premium (Dropcontact, Apollo)
- ✅ Segurança & compliance (rate limit, anti-SSRF, LGPD)
- ✅ Testes completos
- ✅ Documentação
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

**Hoje (2026-09-06):**
1. [ ] Priorizar qual sprint começar (recomendação: Sprint 1)
2. [ ] Criar issues no GitHub por feature
3. [ ] Alocar tempo com a equipe
4. [ ] Setup de CI/CD se não tiver

**Esta semana:**
- [ ] Migração Alembic para `activities` table
- [ ] Endpoint `POST /api/leads/{id}/activities`
- [ ] UI de atividades (timeline)

**Este mês:**
- [ ] Scoring com popover
- [ ] Pipeline kanban
- [ ] Regras automáticas (meeting_scheduled, follow-ups)
- [ ] `.ics` generation

---

**Última atualização**: 2026-09-06 por Claude  
**Mantido em**: `/docs/CHECKLIST_PENDENCIAS.md`
