# Planejamento técnico — LeadEnricher + Dynamics 365 + Agente WhatsApp

> Data: 2026-08-11  
> Status: **Fases 1–10 concluídas em código. O que resta não é código** —
> conta na Meta, licença do M365, planos Vercel/Supabase e as variáveis da
> virada. A lista exata está no § 17 e o passo a passo em [PRODUCAO.md](PRODUCAO.md).  
> Versão: 1.1

---

## ⚠️ Premissas corrigidas

Antes de começar, 4 correções técnicas que mudam o planejamento:

### 1. "O lead possui um número de WhatsApp do decisor" — FALSO

O pipeline coleta o telefone **da empresa** (site, 0800, CNPJ da Receita), marcado como `type="company"`. O celular pessoal do decisor **não é obtido de forma gratuita** (código: `services/people/waterfall.py:390`).

**Implicação:** O agente WhatsApp inicialmente mandará mensagem para a **central/recepção**, não para o decisor. Isso não inviabiliza o projeto, mas muda o fluxo:
- Seu pai informa o celular manualmente (FASE 1), **ou**
- Você paga provedor de dados (Hunter, Lusha, etc.), **ou**
- "Confirmar se é a pessoa certa" vira **literalmente a primeira função do agente.**

### 2. "A análise de leads está praticamente pronta" — PARCIALMENTE VERDADE

Os **5 campos-alvo** (LinkedIn, MX, funcionários, localização, setor) estão sólidos. A **precisão dos decisores** é limitada por natureza das fontes gratuitas:
- LinkedIn People sem auth retorna vazio na maioria das vezes
- Fallback por buscadores é ruidoso

"Finalizar rápido" = acabamento. "Aumentar muito a precisão de graça" = problema difícil.

### 3. "100% gratuito exceto hospedagem/domínio/banco" — IMPOSSÍVEL NO WHATSAPP

A abordagem inicial (primeiro contato partindo de você) na API **oficial** exige um **template de marketing pago por mensagem** e aprovado pela Meta.

**Onde está o custo:**
- Número/WABA: **gratuito**
- Receber/responder (24h depois que lead responde): **gratuito** (desde nov/2024)
- **Enviar 1º template (abertura fria): pago** — aprox. **R$ 0,30–0,45/msg no BR** (confira tabela atual da Meta)

Responder (a parte que a IA faz) é **grátis**. Abrir é **pago**.

### 4. "Proposta v3 já havia rejeitado disparo automático" — VERDADE

Seção 3.5 rejeitou por risco de LGPD/anti-spam. Estamos reabrindo a decisão, mas **é o ponto mais arriscado** do sistema — banimento de número é real.

---

## 1. Diagnóstico do sistema atual

### Stack confirmada no código

| Camada | Tecnologia | Versão |
|---|---|---|
| **Backend** | FastAPI + SQLAlchemy + Pydantic | 0.115, 2.0, v2 |
| **Frontend** | HTML/Jinja2 + JavaScript puro | views por hash, ~2.500 linhas totais |
| **Auth** | Supabase Auth (JWT HS256) + modo demo efêmero | por navegador |
| **Banco** | SQLite (dev) / Postgres Supabase (prod) + Alembic | migrações parciais |
| **Billing** | Stripe webhook idempotente | Free/Pro/Enterprise |
| **Deploy** | Vercel serverless, `maxDuration: 60s` | 2 crons (fila, limpeza demo) |
| **IA** | Claude Haiku via REST (ai_insights.py) | resumo, atrás de chave + plano pago |
| **Dados grátis** | Scraping site, DNS, CNPJ Receita, LinkedIn público, buscadores | 5 campos-alvo com alta precisão |
| **Extras** | Extensão Chrome, rate-limit, anti-SSRF, opt-out LGPD | completo |

### O que funciona bem

✅ Enriquecimento de empresa (v4, cache versionado)  
✅ Pipeline/kanban (`Lead.stage`)  
✅ Execução comercial completa (`Activity`: call/email/meeting/note/task + regras automáticas + `.ics`)  
✅ Importação de planilha (fidelidade célula a célula)  
✅ Fila de enriquecimento (`Job`, resistente a concorrência)  
✅ Banco global de contatos (`Company`/`Person`/`EmailPattern`, auto-alimentado)  
✅ Webhook assinado para CRM (Dynamics/Zapier/Make via Power Automate)  

### Onde está frágil / incompleto

⚠️ **Decisores:** fonte principal (LinkedIn People sem auth) quebra fácil; celular do decisor inexistente sem pagar  
⚠️ **Fila server-side:** enriquecimento em lote **depende da aba aberta** (cron cobre parcialmente)  
⚠️ **Dynamics:** só webhook genérico; `CRMConnection` tem campos `access_token/refresh_token/account_id` prontos, mas **sem cliente nativo do Dataverse**  
⚠️ **WhatsApp:** **zero código**  

### A limitação que domina tudo: 60s do serverless Vercel

Todo cronômetro no código existe por causa disso. **Impede um processo sempre-ligado** — decisivo para escolher WhatsApp oficial (via webhooks) vs não-oficial (precisaria de VPS 24h).

---

## 2. O que precisamos construir

| Categoria | Itens | Prioridade |
|---|---|---|
| **Reaproveitar** | Enriquecimento, pipeline, `Activity`, webhook CRM, IA resumo, opt-out hash, fila `Job`, banco global pessoas | — |
| **Finalizar** | (a) Fila server-side real; (b) precisão decisores (item 1.2 roadmap); (c) `PATCH /api/leads/{id}` edição manual | **FASE 1** |
| **Construir novo** | (a) `Lead.relationship` (LEAD/CUSTOMER/DO_NOT_CONTACT) + portão determinístico; (b) WhatsApp; (c) orquestrador agente; (d) aba "Conversas" | **FASES 2–5** |
| **Opcional depois** | Cliente nativo Dynamics (OAuth Entra ID); multiusuário/times | **Opcional** |
| **Não fazer** | WhatsApp não-oficial; LLM local em cluster; deixar IA decidir regras críticas | **Nunca** |

---

## 3. Arquitetura proposta

```
┌───────────────────────────── VERCEL (o que já existe + novo) ─────────────────────────────┐
│                                                                                            │
│  FastAPI (main.py)                                                                        │
│   ├── /app                          (UI atual + nova aba "Conversas")                      │
│   ├── /api/enrich                   (inalterado)                                          │
│   ├── /api/decisores                (inalterado)                                          │
│   ├── /api/leads                    (+ PATCH para editar telefone)                        │
│   ├── /api/activities               (inalterado)                                          │
│   ├── /api/leads/{id}/push          (CRM via webhook — inalterado)                        │
│   ├── /api/wa/webhook       (NOVO)  (recebe mensagens da Meta — resposta curta ~2s)       │
│   ├── /api/wa/start         (NOVO)  (humano inicia, cria conversation, enfileira)         │
│   └── /api/internal/*       (cron)  (fila + novo: pacing/janela horário)                  │
│                                                                                            │
│  Postgres (Supabase)                                                                      │
│   ├── Tabelas atuais (inalteradas)                                                        │
│   ├── conversation          (NOVO)  (uma conversa de WhatsApp por lead)                    │
│   ├── wa_message            (NOVO)  (cada mensagem trocada)                               │
│   ├── Lead.relationship     (NOVO)  (LEAD/CUSTOMER/DO_NOT_CONTACT/BLOCKED)                │
│   └── OptOut                (existente, repropósito como DO_NOT_CONTACT)                  │
│                                                                                            │
└────────────────────────────────────────────────────────────────────────────────────────────┘
        │ webhook oficial                         │ 1 chamada LLM/turno
        ▼                                         ▼
  WhatsApp Cloud API (Meta)                  LLM (Haiku ou Gemini)
                                             └─ redige texto + classifica intenção
                                                (portão determinístico decide se sai)
```

**O coração de segurança:** um único portão `can_send(conversation) -> bool` por onde **toda** mensagem de saída passa.

---

## 4. Fluxo completo do usuário (seu pai)

1. Abre `/app`, vai num lead (ficha já existe).
2. Vê o telefone. **Se for de empresa, app avisa** ("este é o número da central; informe o celular do decisor") e deixa colar/confirmar o celular.
3. Clica **"Iniciar contato por WhatsApp"**. App mostra o **template** aprovado e pede confirmação (ação irreversível).
4. Sistema cria `conversation` com `ai_status = AI_ACTIVE` e envia template (humano inicia).
5. Na aba **"Conversas"**, cada card mostra: nome, empresa, **selo grande de status** (🟢 IA ativa / 🟡 aguardando você / 🔴 parada / 👤 assumida), última mensagem, botão "Assumir agora" sempre visível.
6. Lead responde → a IA responde **dentro das regras**. Se qualquer gatilho dispara (pediu humano, negociou, é cliente, pediu para não receber), card fica 🟡 e seu pai é avisado.
7. Ele pode **pausar a IA** (um clique), **assumir** (IA cala imediatamente) ou **encerrar**.
8. Tudo vira `Activity` na timeline — histórico único.

Cada botão diz o que faz. Status sempre visível. Função desligada aparece apagada explicando o que falta. (Memória: `leadenricher-ux-clareza-funcional`.)

---

## 5. Fluxo do agente + máquina de estados

### Regra de ouro

**A IA nunca envia nem decide parar.** Ela só:
- (a) Classifica a intenção da última mensagem
- (b) Redige um rascunho

Uma **máquina de estados determinística** valida antes de qualquer envio.

### Diagrama de estados

```
relationship = LEAD ── seu pai autoriza ──▶ conversation.ai_status = AI_ACTIVE
        │
        ▼
Envia TEMPLATE aprovado (1º contato, humano inicia)  ──▶  aguarda resposta
        │
   lead responde  ──▶ [PORTÃO can_send? relationship==LEAD? não opt-out? janela 24h? horário?]
        │                                   │ qualquer "não" ⇒ não envia
        ▼                                   ▼
   LLM classifica intenção            HANDOFF/PAUSA (avisa humano)
        │
   ┌────┴─────────────────────────────────────────────────────────────────────────┐
   │ intenção                           → ação                                      │
   │ "sou eu / é a pessoa certa"        → seguir conversando                        │
   │ "quero falar com humano"           → HUMAN_HANDOFF (pausar IA)                 │
   │ "tenho interesse / vamos negociar" → HUMAN_HANDOFF                             │
   │ "já sou cliente"                   → STOP + marca relationship=CUSTOMER        │
   │ "não quero mais mensagens"         → STOP + OptOut + DO_NOT_CONTACT            │
   │ pergunta fora da base              → HUMAN_HANDOFF                             │
   │ ambíguo / baixa confiança          → HUMAN_HANDOFF                             │
   │ abertura para conversa             → tentar agendar ligação                    │
   └─────────────────────────────────────────────────────────────────────────────┘
        │
   agendou ligação ──▶ cria Activity(type=meeting) + .ics  ──▶ HANDOFF p/ humano
```

### Fora do horário

O portão marca `after_hours=true`. O LLM recebe **modo curto**:
- Reconhece a mensagem
- É educado
- Tenta empurrar para agendar ligação
- **Não aprofunda**

Regra determinística: máximo N trocas fora do horário antes de silenciar até o próximo dia útil.

### Estados (dois eixos separados — **essa separação é o coração da proteção**)

**`Lead.relationship`** (no Lead, **estrutural**):
- `LEAD` — prospecto normal, pode receber IA
- `CUSTOMER` — cliente atual, IA nunca inicia
- `DO_NOT_CONTACT` — pediu para não receber
- `BLOCKED` — erro/suspeita de segurança

**`conversation.ai_status`** (na conversa, **controla automação**):
- `AI_ACTIVE` — IA respondendo
- `AI_PAUSED` — seu pai pausou manualmente
- `HUMAN_HANDOFF` — seu pai assumiu ou IA detectou gatilho
- `STOPPED` — encerrada

### O portão (determinístico, **default-deny**)

```python
def can_send(conversation, lead) -> bool:
    # Lê estado atual (a cada turno)
    if lead.relationship != "LEAD":
        return False  # não é lead
    if conversation.ai_status != "AI_ACTIVE":
        return False  # IA não está ativa
    if is_blocked_by_optout(db, conversation.phone):
        return False  # pediu para não receber
    if not in_service_window(conversation.last_message_at):
        return False  # fora do horário (modo curto depois)
    if conversation.window_expires_at < now():
        return False  # passou 24h
    return True
```

---

## 6. WhatsApp — a parte crítica

### Opção A — API Oficial (recomendada)

**Como funciona:**
- Você cria um WhatsApp Business Account (WABA), recebe um número
- Meta chama **seu webhook** a cada mensagem recebida
- Você responde via REST em < 5s
- **Encaixa perfeitamente no serverless Vercel** (sem servidor novo)

**1º contato (abertura fria):**
- **Obrigatoriamente via template pré-aprovado pela Meta**
- Template de prospecção = categoria **marketing**
- **Pago por mensagem** (~R$ 0,30–0,45/msg no BR, **confira tabela atual Meta**)
- Sujeito a **queda de qualidade/bloqueio** se muita gente marcar como spam

**Responder (seu valor):**
- Depois que o lead responde, abre **janela de 24h** de mensagens livres
- **Grátis** desde nov/2024 (conversa iniciada pelo usuário)
- É aqui que a IA trabalha barato

**Custo resumido:**
- Número/WABA: **R$ 0**
- Receber + responder 24h: **R$ 0**
- Enviar 1º template: **pago** (centavos por msg)

**Risco principal:** abertura fria em massa derruba o *quality rating* do número → restrição. **Mitigação:** humano inicia, volume baixo, templates aprovados, opt-out fácil.

### Opção B — Não-oficial (Baileys, Evolution, wppconnect)

❌ **Não recomendo para operação comercial do seu pai.**

**Problemas:**
- **Viola Termos da Meta** → risco real de **banir o número para sempre** (catastrófico)
- Exige **processo sempre-ligado com WebSocket** → **não roda na Vercel**
- Precisaria VPS/PC ligado 24h (contradiz "simples")
- Quebra a cada atualização do WhatsApp (manutenção infinita)
- Sem SLA, sem suporte, insegurança jurídica para operação comercial

**Máximo:** use para experimento com chip descartável. Nunca no número do seu pai.

### Recomendação (importante)

**Reenquadre o "primeiro contato":**

> **Seu pai (ou um clique automático) dispara o 1º template aprovado. A IA assume a partir da resposta do lead**, dentro da janela de 24h.

**Vantagens:**
- ✅ Derruba o custo (só a abertura é paga; resposta é grátis)
- ✅ Derruba risco de ban (é o humano que inicia, com base legal mais defensável)
- ✅ Mantém 100% do valor (a IA qualifica, confirma pessoa, tenta agendar)
- ✅ Mais seguro LGPD (consentimento/base legal mais clara)

**Isso é a arquitetura que recomendo:** API oficial + humano inicia + IA qualifica.

---

## 7. IA — opções sob restrição de custo

**Sua preferência (regras críticas determinísticas, IA só apoio) é correta e é o que proponho.**

| Opção | Custo | Qualidade PT-BR | Latência | Privacidade | Veredito |
|---|---|---|---|---|---|
| **Claude Haiku (atual)** | ~centavos/conversa | ótima p/ tarefa | baixa (2–3s) | sai p/ Anthropic (TLS) | **Recomendado agora** |
| **Google Gemini Flash (free tier)** | **R$ 0** até cota | boa | baixa | sai p/ Google | Bom "grátis de verdade"; depois se apertar |
| **Ollama local (Llama/Qwen 8B)** | **R$ 0** | razoável | média (8–15s) | fica na máquina | Só se worker no PC do seu pai (contradiz Vercel) |
| OpenAI/outros | $$ | ótima | baixa | terceiro | Sem motivo agora |

**É realista IA sem API paga?** Sim. Haiku custa quase nada (fração de centavo por mensagem curta de qualificação). Se apertar, Gemini free tier funciona. Depois da prova de conceito, decida.

**Separação correta:** LLM → Regras de negócio → Máquina de estados → WhatsApp. Regra crítica nunca depende do modelo.

---

## 8. Dynamics 365

### Hoje

Só o **webhook assinado** (`push_lead`), que **já chega no Dynamics via Power Automate** ("When a HTTP request is received").

**Já funciona sem código novo.**

### Caminho recomendado

#### 1. **Agora — webhook → Power Automate → Dynamics** (trivial)
- Zero código novo no LeadEnricher
- Você mapeia campos dentro do Power Automate
- Idempotência/dedup você resolve no fluxo (chave = domínio/CNPJ)
- ⚠️ **Confirme se o plano M365/Dynamics inclui Power Automate sem conector premium** (conector Dataverse pode ser pago conforme a licença)

#### 2. **Depois (opcional) — cliente nativo Dataverse Web API**
- OAuth2 *client-credentials* via app registration no Entra ID
- Você controla: **upsert** (`PATCH` com `If-Match`/*alternate key*) = idempotência real
- Retry automático, logs nativos
- Mapeamento `Lead→lead`, `activity→phonecall`, `meeting→appointment`
- Campos `access_token/account_id` em `CRMConnection` já existem para isso

**Regra prática:** só construa o nativo quando o webhook provar o fluxo e o volume justificar. Menor complexidade primeiro.

---

## 9. Banco de dados — modelagem

### Reaproveita quase tudo

Novas entidades **mínimas**:

#### `conversation` (tabela nova)

```
id                 INTEGER PK
lead_id            INTEGER FK → leads
decision_maker_id  INTEGER FK → decision_makers (nullable)
user_id            STRING FK → profiles
phone_e164         STRING     (número do destinatário, único)
channel            STRING     (varchar, "whatsapp")
ai_status          STRING     (AI_ACTIVE | AI_PAUSED | HUMAN_HANDOFF | STOPPED)
relationship       STRING FK  (aponta de volta a lead.relationship — redundância deliberada para cache)
window_expires_at  DATETIME   (as 24h)
last_inbound_at    DATETIME   (última mensagem recebida)
last_outbound_at   DATETIME   (última mensagem enviada)
last_message_body  TEXT       (preview da última msg, sem PII)
handoff_reason     TEXT       (por que foi transferida)
after_hours        BOOLEAN    (a última troca foi fora do horário?)
created_at         DATETIME   (quando iniciou)
updated_at         DATETIME   (última mudança de estado)
```

#### `wa_message` (tabela nova)

```
id                 INTEGER PK
conversation_id    INTEGER FK → conversations
direction          STRING     (in | out)
wa_message_id      STRING     (ID da Meta, para idempotência)
type               STRING     (text | template | image | button)
body               TEXT       (conteúdo, sem PII sensível)
template_name      STRING     (qual template foi enviado, se type=template)
status             STRING     (sent | delivered | read | failed)
sent_by            STRING     (ai | human)
intent_detected    STRING     (resultado da classificação LLM)
created_at         DATETIME
```

### Alterações pequenas no que já existe

**`Lead.relationship`** — nova coluna
- `LEAD` (padrão)
- `CUSTOMER` (cliente atual)
- `DO_NOT_CONTACT` (pediu para não receber)
- `BLOCKED` (erro/suspeita)
- **É a trava estrutural** contra falar com cliente

**`DecisionMaker.phone`** — permitir edição manual
- Hoje é sempre `None` ou telefone de empresa
- Seu pai informa o celular confirmado aqui

**`OptOut`** — **já existe** (hash de telefone)
- Integra como fonte de `DO_NOT_CONTACT` sem nada novo

**Não precisa de mais que isso agora.** Reusar `Activity` para histórico evita redundância.

---

## 10. Segurança — riscos e mitigação

| Risco | Mitigação | Teste |
|---|---|---|
| IA falar com **cliente atual** | `relationship` no banco + portão exige `LEAD`; cliente **nunca** pode ter `AI_ACTIVE` | Teste: marque um lead como CUSTOMER, confirme que portão nega |
| IA não parar quando mandam parar | "Parar" faz `UPDATE conversation.ai_status`; worker relê estado **antes de cada envio** | Teste: clique "Pausar", confirme que próxima mensagem entra em HANDOFF |
| Vazamento de telefones | Opt-out por **hash**; PII fora de logs; `wa_message.body` só no Postgres TLS | Auditoria: grep log em busca de `+55` |
| **Tokens da Meta / chaves** | Só em env vars Vercel; `access_token` e `webhook_secret` da `CRMConnection` cifrados em repouso com `SECRETS_KEY` (Fase 10) | Code review: nenhuma chave em `main.py` ou migrations; `test_segredos.py` lê a coluna crua |
| Webhook da Meta falsificado | Validar `X-Hub-Signature-256` (HMAC do App Secret) | Teste: mandar POST sem HMAC, confirmar 401 |
| SSRF no push CRM | Já mitigado (`is_public_url`, sem redirect) | Code review já feito em 2026-08 |
| Quem pode ligar/desligar IA | Só o dono do lead (`user_id`), via JWT validado | Teste: outro usuário tenta PATCH na conversa do seu lead |
| **Ban do número WhatsApp** | Humano inicia + templates aprovados + volume baixo + opt-out fácil | Monitoramento: quality score da Meta |

**Sem overengineering:** as peças de segurança mais importantes (opt-out, SSRF, idempotência, HMAC, rate-limit) **já existem**.

---

## 11. Performance

- **Não introduza processo sempre-ligado.** WhatsApp oficial é *event-driven*: cada turno é uma requisição curta (< 5s: ler estado → 1 chamada LLM → 1 envio). Cabe nos 60s de Vercel.
- **Sem WebSocket/polling pesado no front:** aba "Conversas" pode fazer *polling* leve (a cada 10–15s) ou usar **Supabase Realtime** (já tem Supabase) para empurrar novidades.
- **Pacing/horário/janela 24h:** cron curto (a cada poucos minutos) resolve reengajamento sem manter nada rodando. ⚠️ **Confirmar plano Vercel:** Hobby tem crons limitados.
- **LLM:** 1 chamada por mensagem recebida, resposta curta (`max_tokens` baixo) → custo e latência mínimos.
- **Banco:** volume de mensagens é pequeno (conversas de qualificação); índices em `conversation_id` + `wa_message_id` bastam.

---

## 12. Manutenção — o que dói e como reduzir

| Parte | Dificuldade | Como reduzir |
|---|---|---|
| Scraping decisores (LinkedIn/buscadores) | **Alta** (DOM muda) | Já é "melhor esforço"; isolar com fallback (Receita) — como já está |
| WhatsApp **não-oficial** | **Altíssima** (quebra a cada atualização) | **Não usar** |
| WhatsApp **oficial** | Média | API estável; menos manutenção |
| Prompt/IA | Baixa se as regras estão no código | Manter LLM burro; determinismo fora dele |
| Front em JS puro (2.000 linhas) | Média e crescendo | Aba "Conversas" segue padrão de views por hash; não framework agora |
| `_sync_schema` + Alembic coexistindo | Média | Gerar migração Alembic para tabelas novas; não confiar no auto-sync |

---

## 13. Complexidade por funcionalidade

| Funcionalidade | Complexidade | Por quê |
|---|---|---|
| `PATCH /api/leads/{id}` editar telefone | **Fácil** | CRUD simples; `phone_normalizer.py` já existe |
| `Lead.relationship` + portão `can_send()` | **Fácil/Média** | Poucas colunas; é o coração de segurança — **testar bem** |
| Aba "Conversas" (UI) | **Média** | Segue padrão; tela nova com estado ao vivo |
| WhatsApp oficial (webhook + envio + template) | **Média** | API bem documentada; burocracia WABA/template |
| Máquina de estados + intenção | **Média** | Lógica clara; precisa cobrir gatilhos de handoff |
| Fila server-side (fechar a aba) | **Média** | Refina item 0.2 do roadmap |
| Dynamics via webhook/Power Automate | **Fácil** | Já pronto no backend |
| Dynamics **nativo** (OAuth Entra ID) | **Difícil** | OAuth + mapeamento + upsert idempotente |
| Precisão alta decisores **de graça** | **Muito difícil** | Limite das fontes gratuitas |
| WhatsApp não-oficial estável | **Muito difícil** | Ban + quebra constante |

---

## 14. Custo (R$ — sem inventar preços)

| Componente | Solução recomendada | Custo | Observações |
|---|---|---|---|
| **IA** | Claude Haiku ou Gemini Flash (free tier) | ~R$ 0 a centavos/conversa | Haiku: fração de centavo. Gemini: tem cota mensal |
| **WhatsApp — receber/responder (24h)** | Cloud API oficial | **R$ 0** | Grátis desde nov/2024 (conversa do lead) + cota mensal |
| **WhatsApp — 1º contato (template)** | Cloud API oficial | pago/msg | **~R$ 0,30–0,45/msg no BR** (aproximado; **confira Meta agora**) |
| **WhatsApp — número/WABA** | Meta | **R$ 0** | — |
| **Banco** | Supabase | R$ 0 (free) ou ~US$ 25/mês (Pro) | Free pausa após inatividade; produção pede Pro |
| **Hospedagem** | Vercel | R$ 0 (Hobby) ou ~US$ 20/mês (Pro) | ⚠️ Hobby é não-comercial + crons limitados; seu pai precisa Pro |
| **Dynamics — integração** | Web API (nativo) | **R$ 0** | Incluso na licença dele |
| **Dynamics — Power Automate** | Fluxo | depende | Conector Dataverse **pode ser premium** — **confirmar plano M365** |
| **Domínio** | — | ~R$ 40–60/ano | já previsto |

**Resumo honesto:**
- O que realmente custa: (1) **templates de marketing WhatsApp** (inevitável na via oficial), (2) **Vercel/Supabase Pro** quando virar operação comercial real
- IA pode ficar **~R$ 0** (Gemini free + cota) ou **centavos** (Haiku)
- Dynamics integra sem custo (webhook) ou com custo de OAuth (você decide depois)

---

## 15. Roadmap — implementação por fases

Reordenado por **valor × risco**, começando por **desbloqueadores**:

### **FASE 1 — Fechar análise de leads** ✅ feito em 2026-08-11
- [x] `PATCH /api/leads/{id}` edição de telefone + validação (libphonenumber)
- [x] `PATCH /api/decisores/{id}` — celular do decisor, informado à mão
- [x] UI: célula "Telefone" na ficha com edição inline; card do decisor com
      "+ Celular". Sem modal: o campo se edita onde está
- [x] Aviso de central: `phone_is_mobile` (calculado, não é coluna nova) diz na
      tela quando o número atende numa recepção
- [x] Fila server-side: `jobs.reclaim_stale()` devolve à fila o job cuja rodada
      morreu no meio, e o cron passou de 1×/h para 1×/5min
- [x] Testes: `tests/test_edicao_manual.py` (13) + 4 novos em `test_batch.py`

**O que ficou de fora e por quê:** nenhuma migração Alembic — a fase não criou
coluna nenhuma. O aviso de central é derivado do próprio telefone a cada
resposta, então não há estado novo para migrar nem para ficar desatualizado.

⚠️ O cron a cada 5 min só vale no plano Pro da Vercel; no Hobby ele roda uma
vez por dia independentemente do `schedule`. O motor principal continua sendo o
navegador com a aba aberta.

**Valor:** desbloqueia WhatsApp (não existe telefone, não existe agente)  
**Risco:** baixíssimo — é CRUD simples  
**Status:** [Concluída]

---

### **FASE 2 — Dynamics via webhook/Power Automate** ◐ código feito em 2026-08-12
- [ ] **Com você:** confirmar se a licença M365/Dynamics inclui Power Automate
      sem premium
- [ ] **Com você:** ligar o webhook a um fluxo Power Automate de teste
- [x] Mapa de campos no payload: `lead→lead`, `call→phonecall`,
      `meeting→appointment`, `task→task`, `note→annotation`
- [x] Dedup por domínio (`dedup_key`), não pelo nosso id
- [x] `schema_version` no payload

**Decisões:**

1. **Deduplicar pelo domínio, não pelo nosso `lead.id`.** O id muda quando a
   mesma empresa volta por outra planilha — deduplicar por ele daria um
   registro duplicado no Dynamics a cada reimportação. O domínio é o
   identificador que o produto inteiro já usa.
2. **A entidade-alvo vai junto de cada atividade.** O fluxo do Power Automate é
   montado à mão por uma pessoa; com o campo `entity` pronto, ele é um `switch`
   num campo só em vez de uma escada de condições sobre o nosso vocabulário
   interno. Tipo desconhecido cai em `annotation`, que registra sem inventar
   semântica que o Dynamics cobraria em campos obrigatórios.
3. **O `relationship` do lead vai no payload.** Um CRM que recebe "cliente
   atual" na fila de prospecção gera abordagem repetida — o mesmo erro que o
   portão evita do lado de cá.
4. **As conversas de WhatsApp NÃO vão.** Mandar o que foi dito para um sistema
   de terceiros é decisão sobre dado pessoal, não detalhe de integração — e o
   que sai não volta. **Isto é uma escolha sua**, não uma limitação: quando
   fizer sentido, decida primeiro entre metadado (com quem, quando, quantas
   trocas) e conteúdo; o segundo precisa constar da política de privacidade.

**Valor:** seu pai consegue exportar leads para Dynamics agora  
**Risco:** muito baixo — é configuração, não código  
**Status:** [Payload pronto; falta a licença e o fluxo]

---

### **FASE 3 — Máquina de estados de contato** ✅ feito em 2026-08-11
- [x] `Lead.relationship` + `states.set_relationship()` como entrada validada
- [x] `conversations` + `wa_messages` (tabelas + migração `0003`)
- [x] Portão `can_send()` em `services/wa/gate.py`, **default-deny**
- [x] Cliente atual impossível de entrar (`test_wa_portao.py`, 38 testes)
- [x] "Pausar" e "Assumir" interrompem imediatamente — o portão relê do banco
- [x] OptOut integrado: parar bloqueia o **número** por hash, não só o lead
- [x] Validação HMAC do webhook da Meta (`services/wa/webhook.py`)

**Três decisões que fogem do texto original desta seção:**

1. **`conversation.relationship` não existe.** O plano previa a coluna como
   "redundância deliberada para cache". Um cache do campo que decide se a
   automação pode falar com um cliente é exatamente o campo que não pode ficar
   desatualizado. O portão lê `Lead.relationship` direto, sempre.
2. **Fora do horário virou três faixas, não duas.** Comercial → conversa
   normal. Silêncio noturno (21h–8h) → não sai nada, nem resposta curta:
   mensagem comercial de madrugada é o caminho curto para ser marcado como
   spam, e o risco nº 1 do projeto é o número ser restringido. No meio (início
   da manhã, fim da tarde, fim de semana) → responde em modo curto, porque
   sumir com quem acabou de escrever custa o lead. Configurável por env
   (`WA_QUIET_START`, `WA_SERVICE_START`…).
3. **Sem fuso horário disponível, o portão nega.** Horário local
   indeterminado não é motivo para enviar.

**O que ainda não existe:** nenhuma rota HTTP e nenhuma tela. O portão e os
estados são chamáveis e testados, mas quem os chama é a Fase 4 (webhook) e a
Fase 6 (aba "Conversas").

**Valor:** fundação de segurança, já testável sem WhatsApp  
**Risco:** médio — impacta toda a lógica crítica  
**Status:** [Concluída]

---

### **FASE 4 — WhatsApp oficial: recebimento + envio** ✅ código feito em 2026-08-11
- [ ] **Criar WABA + número na Meta** — só você pode fazer
- [ ] **Aprovar 1 template de abertura** — só você pode fazer
- [x] `GET /api/wa/webhook` (handshake de cadastro da Meta)
- [x] `POST /api/wa/webhook` (HMAC sobre o corpo cru, grava mensagem e
      confirmação de entrega, reabre a janela de 24 h)
- [x] `POST /api/wa/start` (humano inicia; passa por `gate.can_start`)
- [x] `GET /api/wa/status` (o que falta configurar, para a tela explicar)
- [x] Cliente da Cloud API em `services/wa/client.py` (`requests`, sem SDK)
- [x] Idempotência por `wa_message_id` — em três camadas: consulta antes de
      gravar, índice único no banco e `IntegrityError` tratado

**Decisões que fogem do texto original:**

1. **`can_start` é um portão separado de `can_send`.** A abertura é, por
   definição, fora da janela de 24 h; passá-la pelo mesmo caminho recusaria
   todo primeiro contato, e a saída natural seria alguém desligar a checagem de
   janela para os dois casos. Todas as outras travas continuam valendo.
2. **Duas recusas novas, que protegem o bolso:** não reabrir conversa cuja
   janela está aberta (responder ali é grátis) e não reenviar convite a quem
   ainda não respondeu (`WA_TEMPLATE_RETRY_HOURS`, padrão 72 h).
3. **Mensagem de número desconhecido é ignorada.** Criar lead para quem
   escreveu sem ter sido convidado seria fichar quem não pediu. Fica no log; a
   Fase 6 decide se mostra.
4. **Falha da Meta não deixa conversa fantasma:** a conversa só nasce depois do
   envio confirmado, para o estado no banco corresponder ao que de fato saiu.

**Para ligar de verdade, faltam só as credenciais** (`.env.example` documenta
todas). Enquanto não existirem, `/api/wa/start` responde 503 dizendo qual
variável falta, e nada é enviado.

**Valor:** agente consegue enviar e receber  
**Risco:** médio (burocracia da Meta, limite de testes)  
**Status:** [Código pronto e testado; aguarda WABA + template aprovado]

---

### **FASE 5 — Orquestrador + IA** ✅ feito em 2026-08-11
- [x] `services/wa/brain.py`: uma chamada ao Haiku devolve intenção, confiança
      e rascunho em JSON. Só isso — não decide, não grava, não envia
- [x] `services/wa/orchestrator.py`: a tabela intenção → ação, em código
- [x] Gatilhos de handoff cobertos: quer humano, negociação, pessoa errada,
      fora da base, ambíguo, confiança baixa, IA fora do ar, Meta recusando
- [x] Modo fora-do-horário (curto) + teto determinístico de trocas
      (`after_hours_turns`, migração `0004`)
- [x] Portão validando **antes e depois** da chamada da IA
- [x] Cron `/api/internal/wa/pending` a cada 10 min

**As decisões que sustentam a segurança desta fase:**

1. **O portão roda duas vezes por turno.** Entre a IA ler e a mensagem sair
   passam segundos de rede — exatamente onde cabe o clique em "Assumir agora".
   Sem a segunda leitura, a automação responderia por cima de quem acabou de
   tomar a conversa. Tem teste que reproduz essa corrida.
2. **Silêncio é falha.** IA fora do ar, JSON quebrado, exceção inesperada,
   janela fechada: tudo vira `HUMAN_HANDOFF` com o motivo escrito, que aparece
   como pendência com badge na tela. O pior resultado possível seria a mensagem
   do lead morrer sem ninguém saber.
3. **Na dúvida, não envia.** A assimetria é o argumento: errar para "chame o
   humano" custa o tempo dele; errar para "responda" custa uma mensagem
   indevida no celular de alguém e, repetido, o número restringido.
   Confiança mínima 0,7; intenção fora da lista vira ambíguo.
4. **A IA nunca responde sobre dinheiro.** `NEGOCIANDO`, `QUER_HUMANO`,
   `JA_E_CLIENTE`, `PEDIU_PARAR` e `FORA_DA_BASE` **nunca** geram resposta
   automática, por mais confiante que o modelo esteja.
5. **Quem pediu para parar não recebe nem confirmação.** "Ok, não mando mais"
   ainda é mais uma mensagem.
6. **A gravação é commitada antes do turno.** Se a resposta demorar e a função
   for interrompida, a mensagem do lead já está salva, a reentrega da Meta não
   duplica, e o cron responde depois.

**Valor:** agente responde de forma inteligente e segura  
**Risco:** médio (prompt pode precisar iteração)  
**Status:** [Concluída — falta ajustar o prompt contra conversas reais]

---

### **FASE 6 — Handoff humano completo** ✅ feito em 2026-08-11
**Feita antes da Fase 5, de propósito:** a tela de controle precisa existir
*antes* da automação que envia sozinha. Construir na ordem original deixaria
uma janela em que a IA responde e não há onde vê-la nem como pará-la.

- [x] Aba "Conversas" (`#conversas`), no padrão de views por hash
- [x] Selo de status calculado no servidor — ativa / aguardando / assumida /
      pausada / encerrada — sempre com uma frase explicando o que significa
- [x] "Assumir agora" visível em **toda** conversa, inclusive nas paradas:
      saída de emergência que só aparece em certos estados não serve para nada
- [x] Aviso na barra lateral com quantas conversas esperam resposta
- [x] Pausar, retomar e encerrar em um clique
- [x] Responder à mão pela tela — e responder **assume** a conversa sozinho
- [x] Botão "Iniciar contato por WhatsApp" na ficha do lead, com confirmação
      que mostra o número e avisa que a mensagem é cobrada

**Decisões:**

1. **O selo vem do servidor, não da tela.** Estado de conversa é regra de
   negócio; dois lugares calculando isso acabam discordando, e o usuário confia
   no que está vendo.
2. **A tela nunca escreve `ai_status`.** Ela manda verbos (`pausar`, `assumir`,
   `retomar`, `encerrar`) e o serviço traduz. Assim ela não consegue inventar
   um estado que o portão — que é default-deny — recusaria para sempre.
3. **Pendência é só "o lead falou e ninguém respondeu".** Conversa pausada por
   escolha do usuário não vira cobrança na barra lateral.
4. **Responder à mão não passa pelo portão da automação** — este é o humano.
   As duas travas que continuam valendo são as que não são nossas para
   negociar: a janela de 24 h (regra da Meta) e o opt-out (promessa de LGPD).

**Valor:** seu pai consegue assumir conversas e responder pessoalmente  
**Risco:** baixo — é UI  
**Status:** [Concluída]

---

### **FASE 7 — Logs, auditoria e monitoramento** ✅ feito em 2026-08-12
- [x] Tabela `audit_logs` + migração `0005`
- [x] Qualidade do número: aviso da Meta pelo webhook (log) e rating ao vivo
      em `/api/wa/metrics`
- [x] Métricas na aba Conversas + `GET /api/wa/conversations/{id}/audit`

**Decisões:**

1. **A trilha guarda decisões, não mensagens.** O conteúdo trocado já vive em
   `wa_messages`; copiá-lo criaria uma segunda cópia de dado pessoal para
   manter em dia — e para apagar quando o titular pedir. O que falta em
   qualquer outro lugar são as *decisões*, e é isso que a tabela guarda.
2. **A coluna `ator` é o ponto da tabela.** "Conversa encerrada" por clique e
   por gatilho da automação são o mesmo estado e histórias diferentes.
   `handoff_reason` guarda só o *último* motivo e não conta história nenhuma.
3. **A qualidade do número não entra na auditoria.** Ela é do WABA, não de um
   usuário; atribuí-la a alguém seria inventar um dono. Vai para o log do
   servidor (nível ERROR quando cai) e aparece ao vivo na tela.
4. **A taxa de handoff é sobre quem respondeu, não sobre o total.** Sobre o
   total ela cairia sozinha a cada convite não respondido e passaria a medir
   taxa de resposta, em vez do que se quer saber: quanto a automação toca
   sozinha.
5. **Nada de custo em reais na tela.** O preço do template muda por país e
   categoria; um número inventado ali vira decisão de negócio errada. Mostramos
   a contagem de convites, que é o que se multiplica pela tabela da Meta.

**Um bug que os testes pegaram:** o registro do convite nascia sem
`conversation_id` (a auditoria era gravada antes do `flush`, quando a conversa
ainda não tinha id). Ele existia na tabela e sumia do histórico da conversa —
justamente onde alguém iria procurá-lo.

**Valor:** rastreabilidade e diagnóstico  
**Risco:** baixo  
**Status:** [Concluída]

**Valor:** confiança e conformidade  
**Risco:** baixo  
**Status:** [Depois de Fase 6]

---

### **FASE 8 — Testes de ponta a ponta** ✅ feito em 2026-08-12
- [x] Suíte: **633 testes** (eram ~80 no início do plano)
- [x] Portão, conversa, handoff, máquina de estados — arquivos próprios
- [x] `test_wa_jornada.py`: a jornada inteira, com mock **só na fronteira de
      rede** (Graph API e Anthropic). Tudo entre uma e outra — assinatura,
      portão, orquestrador, banco, auditoria, métricas — roda de verdade
- [x] `test_wa_cliente.py`: o código que fala com a rede, que antes só
      executaria pela primeira vez em produção
- [x] Cobertura do código novo: **94%** (meta era 70%)

| módulo | cobertura |
|---|---|
| `services/wa/webhook.py` | 100% |
| `services/wa/client.py` | 98% |
| `services/wa/orchestrator.py` | 97% |
| `services/wa/states.py` | 96% |
| `services/wa/brain.py` | 93% |
| `services/wa/gate.py` | 92% |
| `routers/wa.py` | 92% |
| `services/crm/webhook.py` | 84% |

**Dois defeitos que só apareceram aqui:**

1. **O telefone do lead vazava no log.** O comentário em `client.py` afirmava
   que o número não ia para o log, mas a mensagem de erro da Meta ("recipient
   +55...") ia inteira. Agora só o **código** do erro é registrado; a frase
   completa volta na tela, para o dono da conta — que já conhece o número.
2. **Um teste que passava sozinho e falhava em conjunto.** O `fileConfig` do
   Alembic (disparado por `test_migracoes.py`) marca `disabled = True` em todos
   os loggers que já existiam, e isso vale para o resto do processo. Não afeta
   produção — o app carimba a revisão por SQL direto e nunca importa Alembic —
   mas envenena qualquer teste que confira log.

**Ainda depende de você:** o teste com tráfego real. O roteiro está no passo 6
de [PRODUCAO.md](PRODUCAO.md) — sete verificações no seu próprio número antes
de qualquer lead.

**Valor:** confiança antes de produção  
**Risco:** baixo  
**Status:** [Concluída, menos o tráfego real]

---

### **FASE 9 — Produção** ✅ ferramental feito em 2026-08-12
O que dava para escrever em código está pronto; o que sobra é operação, e está
documentado passo a passo em **[docs/PRODUCAO.md](PRODUCAO.md)**.

- [x] `services/preflight.py` — confere tudo que precisa estar no lugar e diz
      **a consequência** de cada pendência, em três níveis
      (`impede` / `perigoso` / `atencao`)
- [x] `GET /api/internal/preflight` (protegida pelo segredo do cron)
- [x] Conferência escrita no log a cada boot
- [x] Guarda de boot: em produção o app **recusa subir** com WhatsApp meio
      configurado
- [x] Cron `/api/internal/wa/pending` declarado no `vercel.json`
- [x] Roteiro de virada, roteiro de desligamento e o que monitorar
- [ ] **Operacional, com você:** Vercel Pro, `APP_ENV=production`,
      `DEMO_MODE=0`, chaves reais, template aprovado, `alembic upgrade head`

**Decisões:**

1. **Ligue o recebimento antes do envio.** É a regra que orienta o roteiro
   inteiro. Com envio ligado e `WHATSAPP_APP_SECRET` ausente, o convite sai e é
   cobrado, e a resposta do lead é recusada no webhook por falta de assinatura
   — recusa correta, efeito prático péssimo: ninguém fica sabendo. Por isso é
   `perigoso` no preflight e **falha dura** no boot em produção: o erro aparece
   no deploy da Vercel em vez de virar lead perdido três dias depois.
2. **Cada achado diz a consequência.** Uma checagem que só diz "faltando" faz a
   pessoa preencher a variável e seguir sem entender o risco que correu.
3. **`pronto` olha só para `impede`.** `perigoso` é decisão de quem liga, não
   do código — DEMO_MODE aberto em produção pode ser proposital numa
   demonstração.
4. **A rota é protegida pelo segredo do cron.** A resposta é um mapa do que
   está e do que não está configurado no servidor.

**Valor:** seu pai pode usar de verdade  
**Risco:** operacional  
**Status:** [Ferramental pronto; a virada é sua]

---

### **FASE 10 — Credenciais de CRM cifradas em repouso** ✅ feito em 2026-08-12
Último item de código da checklist do § 17. Fecha o único "pendente" que não
dependia de conta de terceiro.

- [x] `services/crypto.py` — `SegredoCriptografado`, um tipo de coluna que
      cifra ao gravar e abre ao ler (Fernet, chave derivada de `SECRETS_KEY`)
- [x] `webhook_secret`, `access_token` e `refresh_token` em `crm_connections`
- [x] `POST /leads/{id}/push` responde **409** quando o segredo não abre com a
      chave atual, em vez de enviar sem assinatura
- [x] Preflight com três achados novos (sem chave / em claro / ilegível), com
      severidade que depende do que existe gravado
- [x] `scripts/recriptografar_segredos.py`, idempotente
- [x] `tests/test_segredos.py` (24) — suíte de 633 → **657**

**Decisões:**

1. **`webhook_url` e `account_id` ficam em claro.** O primeiro é destino, não
   credencial, e precisa ser lido para passar pela checagem anti-SSRF antes de
   cada envio; o segundo identifica, não autentica. Cifrar o que não precisa
   só aumenta a superfície de "e se a chave sumir".
2. **Sem `SECRETS_KEY`, grava em claro e avisa.** Recusar a gravação quebraria
   desenvolvimento e teste por uma variável que ninguém tem motivo para
   definir na própria máquina. Quem cobra é o preflight — e ele sabe a
   diferença entre "nenhuma conexão configurada" (aviso sobre o futuro) e
   "segredo real legível numa cópia do banco" (exposição que já aconteceu).
3. **Chave trocada devolve um marcador, não `None`.** `None` faria o push sair
   **sem assinatura**: o receptor que valida o HMAC descartaria em silêncio e o
   que não valida passaria a aceitar payload de qualquer origem — a proteção
   desligada sem ninguém notar. Com o marcador, o push recusa e diz como
   consertar. Perder a chave custa regravar a conexão; assinar com o segredo
   errado custa a confiança no que o CRM recebe.
4. **A regra mora no tipo da coluna, não em `@property` no modelo.** Assim não
   existe caminho que escreva sem passar por ela — um `bulk_update` ou um
   `session.merge` continuariam valendo se ela fosse Python do modelo.
5. **A tela de Configurações não carrega os segredos.** Ela só devolve
   "configurado: sim/não". Carregar a linha inteira faria a tela morrer
   justamente quando alguém precisa dela para regravar o que não abriu.
6. **A recriptografia não reescreve o ilegível.** Cifrá-lo com a chave nova
   apagaria a chance de recuperá-lo voltando a chave antiga — que é o primeiro
   conserto a tentar.

**Valor:** o backup do banco deixa de valer como credencial
**Risco:** baixo, com um porém operacional — a chave não é recuperável a
partir do banco. Guarde-a junto das outras.
**Status:** [Concluída; falta definir `SECRETS_KEY` na virada]

---

### **Opcional (depois)**
- **Dynamics nativo OAuth:** Entra ID client-credentials + Web API (1 semana)
- **Multiusuário/times:** tabela `organizations` (1 semana)
- **Refresh automático de leads quentes:** item 1.5 do roadmap (3 dias)

---

## 16. O que **NÃO** devemos fazer agora

1. ❌ **WhatsApp não-oficial** (Baileys, Evolution) no número do seu pai
   - Risco de ban e manutenção infinita
   - Contradiz "sistema comercial confiável"

2. ❌ **LLM local em cluster / GPU dedicada**
   - Contradiz custo e simplicidade
   - Só faz sentido se já houver worker no PC dele

3. ❌ **Deixar a IA decidir regras críticas** (parar, identificar cliente, handoff)
   - Isso **tem** que ser determinístico
   - LLM só redige e classifica

4. ❌ **Cliente Dynamics nativo antes de webhook provar fluxo**
   - Trabalho difícil sem necessidade comprovada
   - Deixa para Fase opcional

5. ❌ **Microserviços / servidor novo sempre-ligado**
   - Via oficial cabe no serverless
   - Não fragmente a arquitetura

6. ❌ **Prometer celular do decisor "de graça e preciso"**
   - Base não entrega isso
   - Não construir UX que finge que entrega

7. ❌ **Multiusuário/times, API pública, PDF executivo**
   - Valor real, mas depois
   - Não bloqueiam operação do seu pai agora

8. ❌ **Abertura fria automática em massa**
   - Caminho mais rápido para queimar o número
   - Maior exposição de LGPD/anti-spam

---

## 17. Checklist final

Antes de considerar a versão pronta para uso real, tudo isso precisa estar **✅ PRONTO E TESTADO**:

### Base / análise
- [x] `PATCH /api/leads/{id}` + normalizar telefone do decisor
- [x] App avisa quando telefone é de empresa; pede celular do decisor
- [x] Fila server-side (usuário pode fechar aba)
- [x] Migração Alembic das tabelas novas (não auto-sync) — `0003` (conversas),
      `0004` (teto fora do horário) e `0005` (trilha de auditoria)

### Segurança / controle humano (bloqueadores)
- [x] `Lead.relationship` (coluna, enum, validação)
- [x] Portão `can_send()` central, **default-deny**
- [x] Cliente atual **impossível** de entrar (teste automatizado)
- [x] "Pausar" e "Assumir" interrompem antes do próximo turno
- [x] OptOut (hash) integrado como `DO_NOT_CONTACT`
- [x] Validação HMAC do webhook da Meta
- [x] Tokens só em env; `access_token` CRM criptografado — `SegredoCriptografado`
      (`services/crypto.py`) cifra `webhook_secret`/`access_token`/`refresh_token`
      no tipo da coluna. Falta **definir `SECRETS_KEY` em produção** (§ Operação)

### WhatsApp
- [ ] WABA + número aprovados — **com você**
- [ ] 1 template de abertura aprovado pela Meta — **com você**
- [x] Humano inicia (`/api/wa/start`); janela de 24 h controlada pelo portão
- [x] IA responde na janela 24h
- [x] Gatilhos de handoff cobertos (humano, negociação, cliente, ambíguo)
- [x] Modo fora-do-horário (curto, sem aprofundar) + teto de trocas

### Dynamics
- [ ] Push via webhook chega em Dynamics (Power Automate) com dedup
- [ ] Confirmado se licença exige conector premium

### IA
- [x] LLM só redige/classifica; nenhuma regra crítica no prompt
- [x] Modelo definido: Haiku (`WA_AI_MODEL`)
- [ ] Custo medido — só dá para medir com conversas reais
- [ ] **Primeiro teste real no seu próprio número, antes de qualquer lead**

### Operação
- [ ] `APP_ENV=production`, `DEMO_MODE=0`, `SECRETS_KEY`, chaves reais
- [ ] Vercel/Supabase plano adequado a uso comercial
- [x] Logs de auditoria (quem, quando, para quem) sem PII sensível — tabela
      `audit_logs`, guarda decisões e não mensagens (Fase 7)
- [x] Testes de ponta a ponta passando — **657 testes**, `test_wa_jornada.py`
      com mock só na fronteira de rede
- [ ] Alembic aplicado em produção

---

## 18. Decisões que você precisa fazer antes de começar

### 1. WhatsApp: confirma API oficial + "humano inicia / IA qualifica"?
- É a via **barata, segura e que cabe na Vercel**
- Ou quer avaliar abertura 100% automática (custo + risco de ban)?

### 2. Celular do decisor: aceitamos que seu pai **informa manualmente** no início?
- Grátis e imediato
- Provedor pago fica para depois (se volume justificar)

### 3. Dynamics: começamos pelo **webhook → Power Automate** (rápido)?
- Deixar nativo OAuth para Fase opcional?
- ⚠️ **Confirma se plano M365/Dynamics inclui Power Automate sem premium?**

### 4. Qual o plano atual Vercel/Supabase?
- Hobby é não-comercial (precisará upgrade pro)
- Confirma se pode fazer esse upgrade?

---

## Próximos passos

1. ✅ **Você leu este documento** — todo o planejamento está aqui
2. 🔲 **Responde as 4 decisões acima**
3. 🔲 **Sessão 1 — Fase 1:** `PATCH /api/leads`, fila server-side
4. 🔲 **Sessão 2 — Fase 2:** Dynamics + Power Automate
5. 🔲 **Sessão 3 — Fase 3:** Máquina de estados + portão
6. 🔲 **Sessão 4 — Fase 4:** WhatsApp oficial
7. 🔲 **... e assim vai**

**Cada fase tem tarefas concretas, testes e checkpoints.** Nada é ambíguo.

---

**Data:** 2026-08-11  
**Análise completa por:** Claude Opus (via análise de código + memórias do projeto)  
**Pronto para:** implementação iterativa por fases  
**Última atualização:** 2026-08-12 (Fase 10 — credenciais de CRM cifradas em
repouso). Este documento é a fonte única de verdade; atualize conforme aprenda

