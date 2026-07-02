# Proposta v3 — Prospecção Inteligente

> Análise estratégica da ideia "Sistema de Prospecção Inteligente (Lusha + DNS + Dynamics 365)"
> e plano de incorporação ao LeadEnricher.
> Data: 2026-06-12 · Base de código: v2.x (FastAPI + Supabase Auth + Stripe + SQLAlchemy)

---

## 1. Intenção central da ideia original

A ideia descreve um **cockpit de prospecção**: o vendedor digita um domínio, o sistema
enriquece automaticamente (DNS + decisores), **prioriza** a oportunidade via lead scoring
e **registra a execução comercial** (ligações, follow-ups, reuniões) em um CRM, alimentando
dashboards de conversão.

A intenção não é "enriquecer leads" — isso o LeadEnricher já faz. A intenção é **fechar o
ciclo**: transformar dado enriquecido em *ação comercial rastreável*. Esse é o valor novo
a incorporar.

---

## 2. Mapeamento: ideia original × estado atual do LeadEnricher

| Componente da ideia | Ferramenta proposta lá | Estado no LeadEnricher | Veredito |
|---|---|---|---|
| Entrada por domínio | Power Apps / Streamlit | `POST /api/enrich` + SPA própria | ✅ **Já temos, superior** (3-5s, paralelo, cache 7 dias) |
| Pesquisa DNS (MX, A, SPF) | SecurityTrails / WhoisXML / dnspython | `services/dns_lookup.py` — relatório estilo DNS Dumpster: MX+ASN+país, A/AAAA, NS, TXT, SPF, DMARC, DKIM, SOA, hosting via ASN | ✅ **Já temos, superior à proposta** — e sem API paga |
| Busca de decisores | Lusha API / Apollo / Hunter | `services/decision_finder.py` — aba People do LinkedIn + multi-engine search, com priorização por cargo | ✅ Já temos (gratuito); ⚠️ APIs pagas viram **plugins opcionais** (§5.4) |
| Validação de e-mails | ZeroBounce / NeverBounce | `services/email_verifier.py` — SMTP probe + detecção de catch-all | ✅ Já temos (gratuito); mesma estratégia de plugin |
| Lead Scoring | Planilha/Python manual | **Não existe** | 🔴 **Gap #1 — prioridade máxima** |
| Registro de ligações + regras | Dynamics 365 (Phone Call Activity) | **Não existe** | 🔴 **Gap #2 — camada de execução comercial** |
| Follow-up / agenda | Power Automate + Outlook | **Não existe** | 🔴 **Gap #3 — tarefas + arquivo .ics universal** |
| Dashboard comercial | Power BI Pro | **Não existe** | 🔴 **Gap #4 — dashboard nativo** |
| Gestão de leads | Google Sheets / Excel | `routers/leads.py` + export XLSX/CSV | ✅ Já temos; evoluir para **pipeline com estágios** |
| Segurança | Entra ID P2 + Defender | Supabase Auth (JWT) + ownership por `user_id` + rate limit + Stripe webhook assinado | ✅ Base sólida; reforços no §7 |
| Banco | Dataverse | SQLite → Postgres (Supabase) via `DATABASE_URL` | ✅ Já temos, sem lock-in |
| Automação | Power Automate | Regras de negócio em Python no próprio backend | ✅ Adaptado (§5.2) |

**Conclusão do diagnóstico:** ~60% da ideia já está implementada — frequentemente melhor
que a proposta original (DNS report próprio vs. API paga; SMTP probe vs. ZeroBounce).
O valor a capturar está nos 4 gaps: **scoring, execução comercial, follow-ups e dashboard**.

---

## 3. Decisões estratégicas (o que adotar, adaptar e rejeitar)

### 3.1 Rejeitar: migrar para Power Platform (Power Apps + Power Automate + Dataverse)

**Por quê:** o LeadEnricher é um SaaS self-serve com auth, billing e quota próprios.
Migrar para Power Platform significaria: licença por usuário (mata o modelo freemium),
lock-in total na Microsoft, descarte de todo o código funcional, e UX limitada ao canvas
do Power Apps. A ideia original assume um cenário de *ferramenta interna* com licença E5
já paga — não é o nosso caso.

### 3.2 Adaptar: Dynamics 365 vira **integração**, não plataforma

Em vez de construir *dentro* do Dynamics, o LeadEnricher **exporta para** CRMs via uma
camada de conectores (§5.5): Dynamics 365 Web API (OAuth2 client-credentials no Entra ID),
HubSpot, Pipedrive e webhook genérico. Quem usa Dynamics ganha a integração; quem não usa
não é penalizado. Isso transforma uma limitação da ideia (atada a um CRM) em diferencial
de produto (CRM-agnóstico).

### 3.3 Adaptar: APIs pagas viram **provedores plugáveis com fallback gratuito**

A ideia depende de 6 APIs pagas (Lusha, Apollo, Hunter, SecurityTrails, WhoisXML,
ZeroBounce). Nossa pipeline gratuita continua sendo o default; APIs pagas entram como
*enhancers* opcionais ativados por env var (§5.4). Benefício: custo zero para operar o
free tier, e qualidade premium como upsell dos planos pagos — alinhado ao billing Stripe
que já existe.

### 3.4 Adotar: Lead Scoring, execução comercial, follow-ups e dashboard

São os 4 gaps. Implementação 100% dentro da stack atual (FastAPI + SQLAlchemy + SPA),
sem dependências novas obrigatórias.

### 3.5 Rejeitar (por ora): disparo automático de e-mail/WhatsApp de prospecção

Consta na "próxima evolução" da ideia. Envolve risco real de LGPD/anti-spam (cold outreach
automatizado com dados raspados) e reputação de domínio. O sistema **prepara** o contato
(e-mail verificado, telefone, roteiro); o disparo fica com o usuário. Reavaliar quando
houver opt-in/consentimento estruturado.

---

## 4. O coração da proposta: Lead Scoring explicável

### 4.1 Princípio

A ideia original propõe 3 critérios somados numa planilha. Nossa versão é superior em
três pontos: **(a)** usa sinais que já coletamos de graça e que a ideia nem menciona
(SPF/DMARC, hosting, confiança do LinkedIn); **(b)** é *explicável* — cada ponto tem
justificativa exibida na UI; **(c)** é *versionada e recalculável* — mudar pesos não
corrompe histórico.

### 4.2 Critérios (v1 do modelo)

Sinais já disponíveis no `Lead` e `DecisionMaker` — nenhuma coleta nova é necessária:

| Sinal | Fonte existente | Pontos | Racional |
|---|---|---|---|
| MX = Microsoft 365 ou Google Workspace | `mx_provider` | +10 | Maturidade digital (critério original) |
| MX = gateway de segurança (Proofpoint, Mimecast…) | `mx_provider` | +12 | Investe em segurança ⇒ orçamento de TI |
| SPF publicado | `dns_report.spf` | +3 | Higiene técnica |
| DMARC publicado | `dns_report.dmarc` | +5 | Maturidade acima da média no Brasil |
| Hosting em cloud (AWS/Azure/GCP/Cloudflare) | `hosting_provider` | +5 | Infraestrutura moderna |
| 11–50 funcionários | `employee_count.band` | +8 | Porte (adaptado do critério original) |
| 51–200 funcionários | `employee_count.band` | +15 | Critério original: ">50 = +15" |
| 200+ funcionários | `employee_count.band` | +20 | Extensão natural |
| LinkedIn da empresa verificado | `linkedin_confidence == "verified"` | +5 | Presença digital ativa |
| Site com descrição + setor extraídos | `description`, `sector` | +3 | Site profissional (critério "futuro" da ideia) |
| ≥1 decisor encontrado | `decision_makers` | +10 | Caminho de entrada existe |
| Decisor com e-mail **SMTP-válido** | `probable_emails[].status == "valid"` | +15 | Contato acionável |
| Decisor com telefone | `decision_makers.phone` | +20 | Critério original: "celular direto = +20" |
| Cargo C-level/founder encontrado | `TITLE_PRIORITY ≤ 2` | +5 | Acesso direto ao decisor |

### 4.3 Faixas (mantidas da ideia, com teto normalizado)

Score bruto máximo ≈ 110; exibir também como 0–100 normalizado.

| Faixa bruta | Classificação | UI |
|---|---|---|
| 0–20 | 🔵 Baixa prioridade | badge azul |
| 21–40 | 🟡 Média prioridade | badge âmbar |
| 41+ | 🔴 Alta prioridade | badge vermelho/quente |

### 4.4 Implementação

- **Novo serviço** `services/lead_scorer.py`: função pura `score_lead(lead, decision_makers) -> {score, priority, breakdown, version}`. `breakdown` é uma lista `[{criterion, points, evidence}]` — é o que torna o score auditável na UI.
- **Colunas novas em `Lead`**: `score: Integer`, `priority: String(10)`, `score_breakdown: JSON`, `score_version: String(10)`.
- **Quando calcular**: ao fim de `enrich_company` (router) e **recalcular** após `POST /api/decisores` (os sinais de decisor mudam o score — a ideia original ignora isso).
- **Endpoint** `POST /api/leads/{id}/rescore` para recálculo sob demanda (ex.: após mudança de pesos).
- Pesos em dict constante versionado (`SCORING_V1`); futuro: pesos configuráveis por usuário (ICP próprio) em tabela `scoring_profiles`.

---

## 5. Camada de execução comercial (o "Dynamics interno")

### 5.1 Modelo de dados

Nova tabela `activities` — substitui Phone Call Activity / Tasks do Dynamics:

```python
class Activity(Base):
    __tablename__ = "activities"
    id          = Column(Integer, primary_key=True)
    lead_id     = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    user_id     = Column(String(36), nullable=False, index=True)
    type        = Column(String(20))    # call | email | meeting | note | task
    outcome     = Column(String(30))    # no_answer | busy | voicemail | talked | meeting_scheduled
    notes       = Column(Text)
    due_at      = Column(DateTime)      # para follow-ups/tarefas
    completed_at = Column(DateTime)
    created_at  = Column(DateTime, default=now_utc)
```

E estágio de pipeline no `Lead`: `stage: String(20)` —
`novo → contatado → reuniao_agendada → oportunidade → ganho | perdido`.

### 5.2 Regras automáticas (o "Power Automate" em Python)

Implementadas em `services/activity_rules.py`, disparadas ao registrar uma atividade
(transação única, sem fila — volume não justifica):

| Resultado da ligação | Ações automáticas |
|---|---|
| `meeting_scheduled` | `lead.stage = "reuniao_agendada"` · cria `Activity(type=meeting, due_at=<escolhida>)` · gera **arquivo .ics** para download (funciona com Outlook, Google e Apple Calendar — mais universal que a integração Outlook da ideia) · dispara sync CRM se conectado |
| `no_answer` / `voicemail` | cria `Activity(type=task, due_at=+2 dias úteis, notes="Refazer contato")` · mantém stage |
| `busy` | follow-up `+1 dia útil` |
| `talked` | `lead.stage = "contatado"` · sugere follow-up opcional |

### 5.3 Endpoints novos

```
POST   /api/leads/{id}/activities      # registrar ligação/nota/tarefa (aplica regras)
GET    /api/leads/{id}/activities      # timeline do lead
GET    /api/activities/pending         # follow-ups vencendo (ordenado por due_at)
PATCH  /api/activities/{id}            # concluir/reagendar
GET    /api/activities/{id}/ics        # download do convite .ics
PATCH  /api/leads/{id}/stage           # mover no pipeline
GET    /api/dashboard/metrics          # ver §6
```

Todos com `Depends(get_current_user)` + filtro `user_id` (padrão já estabelecido no projeto).

### 5.4 Camada de provedores plugáveis (enriquecimento premium)

`services/providers/` com protocolo único:

```python
class EnrichmentProvider(Protocol):
    name: str
    def is_configured(self) -> bool        # checa env var da API key
    def find_contacts(self, domain, roles) -> list[Contact] | None
    def verify_email(self, email) -> str | None
```

- `free_pipeline` (atual `decision_finder` + `email_verifier`) — sempre disponível, default.
- `hunter.py`, `lusha.py`, `apollo.py` — ativados se `HUNTER_API_KEY` etc. existirem; resultados *mesclados* com a pipeline gratuita (dedupe por slug do LinkedIn/e-mail).
- Gating por plano: `free` usa só a pipeline gratuita; `pro`/`enterprise` ganham os provedores premium. Resolve o blocker de custo do v3 (o custo da API é coberto pela assinatura).

### 5.5 Conectores CRM (saída, não plataforma)

`services/crm/` com protocolo `CRMConnector.push_lead(lead, decision_makers, activities)`:

| Conector | Mecanismo | Mapeamento |
|---|---|---|
| **Dynamics 365** | Web API REST (`/api/data/v9.2/leads`), OAuth2 client-credentials via app registration no Entra ID | `Lead → lead`, atividade → `phonecall`, reunião → `appointment` |
| **HubSpot** | API privada (token) | `Lead → company+contact`, atividade → `engagement` |
| **Pipedrive** | API token | `Lead → organization+person+deal` |
| **Webhook genérico** | POST JSON assinado (HMAC) para URL do usuário | payload completo — cobre Zapier/Make/n8n |

Credenciais por usuário em tabela `crm_connections` com campo `credentials` **criptografado
(Fernet, chave em env)** — nunca em texto plano. Sync manual ("Enviar ao CRM") na v1;
sync automático por regra na v2.

---

## 6. Dashboard comercial (o "Power BI interno")

A ideia usa Power BI Pro; nós temos os dados no nosso banco e uma SPA — um endpoint de
agregação + uma aba de dashboard entregam as mesmas métricas sem licença:

`GET /api/dashboard/metrics?period=30d` retorna:

```json
{
  "leads_pesquisados": 142,
  "ligacoes_realizadas": 87,
  "taxa_contato": 0.43,          // talked+meeting / total calls
  "taxa_reuniao": 0.12,          // meetings / total calls
  "conversao_oportunidade": 0.08,
  "funil_por_estagio": {"novo": 80, "contatado": 35, "reuniao_agendada": 15, ...},
  "leads_por_prioridade": {"alta": 22, "media": 61, "baixa": 59},
  "followups_pendentes": 9,
  "followups_atrasados": 3
}
```

UI: nova aba "Dashboard" na SPA — cards de KPI, funil por estágio, distribuição de score
(barras CSS/Canvas no padrão visual atual; sem lib de chart obrigatória — se quiser
gráficos ricos, Chart.js é a única dependência nova aceitável). Export XLSX do dashboard
reutiliza `services/exporter.py`.

---

## 7. Reforços de segurança e conformidade (além da ideia)

A ideia delega segurança ao Entra ID P2 + Defender. Equivalências e melhorias na nossa stack:

1. **Rate limit por usuário, não por IP** — hoje `slowapi` usa `get_remote_address`; atrás de proxy (Vercel/render) todos os usuários compartilham IP. Trocar `key_func` para extrair o `sub` do JWT com fallback para IP.
2. **Anti-SSRF no scraper** — `scrape_website` aceita qualquer domínio digitado; resolver o IP antes do fetch e bloquear faixas privadas/loopback/metadata (`10/8`, `172.16/12`, `192.168/16`, `127/8`, `169.254/16`). Crítico ao expor publicamente.
3. **Criptografia de credenciais CRM** (Fernet) + **HMAC nos webhooks de saída** (§5.5).
4. **Trilha de auditoria** — a tabela `activities` já é o audit log comercial; adicionar log estruturado (JSON logger já existe) para eventos sensíveis: conexão CRM, export em massa, troca de plano.
5. **LGPD** — dados de decisores são dados pessoais coletados de fontes públicas (base legal: legítimo interesse, art. 7º IX): documentar finalidade, implementar retenção (purgar decisores de leads inativos > N meses), endpoint de exclusão já existe (`DELETE /api/leads/{id}` com cascade). Sem disparo automático de mensagens (§3.5).
6. **Migrações com Alembic** — as colunas/tabelas novas (§4.4, §5.1) são a hora certa de sair do `create_all` e adotar migração versionada, antes do Postgres em produção.

---

## 8. UX/UI — evolução da SPA

Mantendo o design system atual (dark, aurora canvas, animações):

1. **Card de resultado**: badge de prioridade (🔴🟡🔵) + score com **popover de breakdown** ("por que 47 pontos?") — transparência que nenhuma das ferramentas da ideia original oferece.
2. **Lista de leads → Pipeline**: visão alternável lista/kanban por estágio; drag-and-drop muda `stage` via `PATCH`.
3. **Ação rápida "📞 Registrar ligação"** no card do lead: modal com os 5 resultados (não atendeu / ocupado / caixa postal / conversou / reunião agendada), notas e — se reunião — date-picker que devolve o `.ics`. Fluxo da ideia original, em 2 cliques.
4. **Fila do dia**: seção "Follow-ups de hoje" no topo (consome `/api/activities/pending`) — o vendedor abre o app e sabe quem ligar primeiro, ordenado por prioridade do lead.
5. **Timeline por lead**: histórico de atividades no detalhe do lead.

---

## 9. Evoluções com IA (fase posterior, gated por plano)

Da lista "próxima evolução" da ideia, ordenadas por valor/custo usando a Claude API:

1. **Resumo executivo da empresa** — prompt com `description + sector + dns_report + decisores` ⇒ parágrafo de contexto pré-ligação. Alto valor, custo baixo (1 chamada curta).
2. **Roteiro de ligação personalizado** — gerado a partir do resumo + cargo do decisor + produto do usuário (campo de configuração "o que você vende").
3. **Sugestão de próxima ação** — classificar notas das atividades e sugerir follow-up.
4. ~~Melhor horário para contato~~ — adiado: exige histórico volumoso que ainda não temos.

Implementação: `services/ai_insights.py` com a Claude API, cache do resumo no `Lead`
(`ai_summary: Text`), disponível nos planos pagos.

---

## 10. Roadmap de implementação

| Fase | Entrega | Itens | Dependências novas |
|---|---|---|---|
| **1 — Scoring** (núcleo, ~1 sessão) | Score em todo lead novo e existente | `lead_scorer.py` · colunas score/priority/breakdown · recálculo pós-decisores · `POST /rescore` · badges + popover na UI · testes | nenhuma |
| **2 — Execução comercial** (~1-2 sessões) | Registrar ligações e nunca perder follow-up | tabela `activities` · regras automáticas · `.ics` · fila do dia · timeline · stage no Lead · Alembic | `alembic`, `ics` (ou geração manual do VCALENDAR) |
| **3 — Pipeline + Dashboard** (~1 sessão) | Visão gerencial | kanban · `/api/dashboard/metrics` · aba Dashboard | (opcional) Chart.js |
| **4 — Integrações** (~2 sessões) | Saída para o ecossistema | conectores Dynamics/HubSpot/Pipedrive/webhook · provedores premium (Hunter/Lusha) · criptografia de credenciais · batch CSV | `cryptography` |
| **5 — IA** (~1 sessão) | Inteligência de abordagem | resumo de empresa · roteiro de ligação | `anthropic` |
| Contínuo | Segurança | rate-limit por usuário (fase 1) · anti-SSRF (fase 1) · LGPD retention (fase 2) | nenhuma |

**Critério de sequência:** a Fase 1 entrega o diferencial mais visível (priorização) usando
apenas dados que já coletamos — zero risco, zero custo. As fases 2-3 completam o ciclo
da ideia original. As fases 4-5 a superam.

---

## 11. Síntese — onde a proposta supera a ideia original

| Limitação da ideia original | Nossa solução |
|---|---|
| Lock-in total em Microsoft (E5 + Dynamics + Power Platform) | Stack própria, CRM-agnóstica; Dynamics é um conector entre quatro |
| 6 APIs pagas obrigatórias | Pipeline gratuita default; APIs pagas como upsell de plano |
| Score opaco em planilha, 3 critérios | 14 critérios explicáveis, versionados, recalculáveis, com evidência na UI |
| Score estático pós-enriquecimento | Recalcula quando decisores são encontrados |
| Gestão em Google Sheets/Excel | Pipeline kanban + dashboard nativo + export XLSX |
| Outlook como única agenda | `.ics` universal (Outlook, Google, Apple) |
| Segurança terceirizada ao Entra ID | JWT + ownership + rate-limit por usuário + anti-SSRF + credenciais criptografadas + LGPD |
| Disparo automático de cold outreach (risco legal) | Preparação do contato; disparo fica com o humano |
| Sem modelo de receita | Freemium já operante (Stripe) — features premium financiam APIs pagas |
