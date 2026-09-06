# 🤖 Documentação Completa: IA do LeadEnricher

**Data**: 2026-09-05  
**Versão**: 2.1.0  
**Motor IA**: Groq API (free tier) — `openai/gpt-oss-120b`

> ⚠️ **Nota de atualização (2026-09-06):** este documento foi escrito antes do
> commit `98331e1` (2026-09-05, 21:29), que **removeu completamente** as
> restrições de horário do agente (silêncio noturno, horário comercial, fim de
> semana). `services/wa/gate.py::service_window()` hoje sempre libera envio,
> a qualquer hora. Todo trecho abaixo que menciona `quiet_hours`, "horário
> comercial" ou as variáveis `WA_QUIET_START`/`WA_SERVICE_START`/`WA_WEEKEND_QUIET`
> descreve um comportamento que **não está mais ativo** — as variáveis ainda
> existem no código, mas não têm efeito. Também foi corrigida abaixo a janela
> de conversa, que é de **24 horas** (não 72h — 72h é `WA_TEMPLATE_RETRY_HOURS`,
> o prazo para reenviar o template a quem não respondeu, uma coisa diferente).

---

## 📋 Índice

1. [Visão Geral](#visão-geral)
2. [Arquitetura da IA](#arquitetura-da-ia)
3. [Agente de WhatsApp](#agente-de-whatsapp)
4. [Insights de Leads](#insights-de-leads)
5. [Configuração](#configuração)
6. [Variáveis de Ambiente](#variáveis-de-ambiente)
7. [Fluxo Completo de Uma Conversa](#fluxo-completo-de-uma-conversa)
8. [Estados e Transições](#estados-e-transições)
9. [Intenções do Lead](#intenções-do-lead)
10. [Tratamento de Erros](#tratamento-de-erros)
11. [Limitações e Timeouts](#limitações-e-timeouts)
12. [Exemplos de Uso](#exemplos-de-uso)
13. [Troubleshooting](#troubleshooting)

---

## 🎯 Visão Geral

O LeadEnricher usa **duas IAs complementares**:

### 1. **Agente de WhatsApp** (`services/wa/brain.py`)
- **O quê**: Classifica a intenção do lead e propõe uma resposta automática
- **Quando**: A cada mensagem recebida no WhatsApp
- **Quem usa**: O orquestrador (`orchestrator.py`) — a IA não toma decisões sozinha
- **Segurança**: Tudo que dá errado vira "pendência humana" (não envia automático)

### 2. **Insights de Leads** (`services/ai_insights.py`)
- **O quê**: Gera resumo executivo + 3 ganchos de venda baseados em dados da empresa
- **Quando**: Sob demanda, via endpoint `/api/leads/{id}/ai-summary`
- **Quem usa**: Vendedores, para preparar ligação
- **Segurança**: Só usa dados já enriquecidos (não inventa fatos)

---

## 🏗️ Arquitetura da IA

### Componentes Principais

```
┌─────────────────────────────────────────────────────────┐
│              Webhook WhatsApp (entrada)                 │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────▼────────────┐
        │   gate.py (Portão #1)   │
        │ - Valida assinatura     │
        │ - Checa horário         │
        │ - Verifica opt-out      │
        └────────────┬────────────┘
                     │
        ┌────────────▼────────────┐
        │   brain.py (IA)         │
        │ - Lê última mensagem    │
        │ - Classifica intenção   │
        │ - Redige rascunho       │
        └────────────┬────────────┘
                     │
        ┌────────────▼──────────────────┐
        │ orchestrator.py (Orquestrador) │
        │ - Converte intenção em ação   │
        │ - Checa portão #2             │
        │ - Marca conversa como         │
        │   "respondida por IA"         │
        └────────────┬──────────────────┘
                     │
        ┌────────────▼────────────┐
        │  client.py (Cliente)    │
        │ - Envia via Meta API    │
        │ - Grava no banco        │
        │ - Atualiza timestamp    │
        └────────────┬────────────┘
                     │
              ┌──────▼──────┐
              │  Resposta   │
              │  enviada ✓  │
              └─────────────┘
```

### Separação de Responsabilidades

| Arquivo | Responsabilidade | Pode errar? |
|---------|------------------|------------|
| `brain.py` | Entender + redigir | ❌ Erros → "AMBÍGUO" → humano |
| `orchestrator.py` | Decidir se envia | ❌ Em dúvida → humano |
| `gate.py` | Autorizar por regras | ✅ Blocos por segurança |
| `client.py` | Entregar à Meta | ❌ Falha → pendência no painel |
| `states.py` | Gravar estado | ✅ Auditoria por webhook |

**Regra de Ouro**: A IA **nunca** envia sozinha se tiver dúvida. Qualquer erro vira "chame o humano".

---

## 🤖 Agente de WhatsApp

### Como Funciona (Passo a Passo)

#### 1️⃣ **Recebimento da Mensagem** (`webhook.py`)
```python
# Webhook POST de /api/wa/webhook
# Meta envia:
{
  "entry": [{
    "changes": [{
      "value": {
        "messages": [{
          "from": "5511999999999",
          "text": {"body": "oi, quanto custa?"},
          "timestamp": "1693000000"
        }]
      }
    }]
  }]
}
```

- **Validação**: Assina com `WHATSAPP_APP_SECRET` (HMAC-SHA256)
- **Rejeição**: Se assinatura não bater → 401

#### 2️⃣ **Primeira Checagem (Portão #1)** — `gate.can_send()`
```python
# Verifica:
decisao = gate.can_send(db, conversa, agora=utcnow())

# Motivos de recusa:
- "not_a_lead"           → empresa não marcada como lead
- "ai_not_active"        → automação não está ligada nesta conversa
- "opted_out"            → número na lista de exclusão (LGPD)
- "window_closed"        → janela de 24h desde a última mensagem do lead expirou
- "already_open"         → conversa já está com vendedor
- "awaiting_reply"       → aguardando resposta do vendedor

# "quiet_hours" existe como código de motivo, mas desde 2026-09-05 nunca é
# devolvido — a checagem de horário foi removida (ver nota no topo do doc).
```

Se bloqueado: **para aqui, não processa mais**.

#### 3️⃣ **Leitura pela IA** — `brain.ler()`
```python
from services.wa import brain

leitura = brain.ler(
    mensagens=[
        {"direction": "in", "body": "oi"},
        {"direction": "out", "body": "Oi! Em que posso ajudar?"},
        {"direction": "in", "body": "quanto custa o plano pro?"}
    ],
    empresa="Empresa do Lead"
)

# Retorna:
Leitura(
    intencao="NEGOCIANDO",      # qual é a intenção
    confianca=0.92,             # 0-1, quão certo
    rascunho=None,              # se intenção → humano, rascunho é None
    erro=None                   # se IA falhou, aqui está o motivo
)
```

**O que a IA faz internamente:**

1. Extrai as **últimas 12 mensagens** (histórico curto)
2. Monta um prompt com:
   - Instrução de classificação (9 intenções predefinidas)
   - Histórico completo da conversa
   - Empresa do lead (para contexto)
3. Chama a Groq API com timeout de 20s
4. Parseia JSON da resposta
5. **Valida**: Se intenção não está na lista conhecida → `AMBÍGUO`

#### 4️⃣ **Decisão do Orquestrador** — `orchestrator.responder()`
```python
turno = orchestrator.responder(db, conversa)

# Basado na intenção, decide:
if leitura.intencao == "CONFIRMOU_PESSOA":
    # Responde com rascunho proposto
    acao = "ENVIOU"
    
elif leitura.intencao == "CONVERSANDO" and leitura.confianca >= 0.7:
    # Responde com rascunho
    acao = "ENVIOU"
    
elif leitura.intencao in ("NEGOCIANDO", "QUER_HUMANO", "PEDIU_PARAR"):
    # Não responde: humano trata
    acao = "CHAMOU_HUMANO"
    
elif leitura.intencao == "AMBIGUO" or leitura.confianca < 0.7:
    # Confiança baixa: humano trata
    acao = "CHAMOU_HUMANO"
```

#### 5️⃣ **Segunda Checagem (Portão #2)** — `gate.can_send()` novamente
```python
# Entre a leitura da IA (passo 3) e o envio passaram alguns segundos.
# Nesse intervalo, o vendedor pode ter clicado em "Assumir agora".
# Então checamos de novo ANTES de enviar.

decisao = gate.can_send(db, conversa, agora=utcnow())
if not decisao.allowed:
    # Não envia (vendedor assumiu enquanto isso)
    acao = "NAO_FEZ_NADA"
```

#### 6️⃣ **Envio via Meta** — `client.send_text()`
```python
# Se passou em tudo:
client.send_text(
    phone_number=conversa.phone_e164,
    text=leitura.rascunho,
    wa_template_name="primeiro_contato"  # se for template
)

# Grava na conversa:
# - timestamp
# - quem enviou (IA vs vendedor)
# - motivo (se foi handoff)
```

#### 7️⃣ **Auditoria** — `states.registrar_auditoria()`
```python
# Grava na tabela `wa_auditoria`:
WaAuditAction(
    conversation_id=conversa_id,
    acao="respondida",          # respondida / pausada / assumida / etc
    ator="ia",                  # quem fez (IA ou vendedor)
    motivo="CONVERSANDO",       # intenção ou razão
    timestamp=utcnow()
)
```

---

### Intenções do Lead (9 tipos)

| Intenção | Exemplo | Resposta IA | Ação Orquestrador |
|----------|---------|------------|-------------------|
| **CONFIRMOU_PESSOA** | "Sou eu mesmo, pode falar" | "Ótimo! Posso te ligar amanhã?" | **Envia** rascunho |
| **CONVERSANDO** | "Oi, tudo bem?" ou "Que legal" | "Podemos conversar em uma ligação?" | **Envia** se confiança alta |
| **QUER_HUMANO** | "Quero falar com uma pessoa" | *(nenhum)* | **Humano trata** |
| **NEGOCIANDO** | "Qual é o preço?" ou "Envie proposta" | *(nenhum)* | **Humano trata** |
| **JA_E_CLIENTE** | "Já somos cliente de vocês" | *(nenhum)* | **Humano trata** |
| **PEDIU_PARAR** | "Não quero mais receber" | *(nenhum)* | **Humano + opt-out** |
| **PESSOA_ERRADA** | "Não sou eu, fale com João" | *(nenhum)* | **Humano trata** |
| **FORA_DA_BASE** | "Como faço para...?" (pergunta técnica) | *(nenhum)* | **Humano trata** |
| **AMBÍGUO** | *(qualquer coisa ambígua ou erro)* | *(nenhum)* | **Humano trata** |

**Regra importante**: Só 2 intenções mandam resposta automática:
- `CONFIRMOU_PESSOA` (confirmação)
- `CONVERSANDO` + confiança ≥ 0.7 (conversa leve)

Tudo mais vai pro humano.

---

### Prompt Enviado à IA

```
Você lê mensagens de WhatsApp que chegam para um vendedor brasileiro 
e faz DUAS coisas: classifica a intenção da última mensagem do lead e 
escreve um rascunho curto de resposta em português do Brasil.

Você NÃO decide se a resposta será enviada. Um sistema separado decide isso.

Classifique a ÚLTIMA mensagem do lead em exatamente uma destas intenções:
- CONFIRMOU_PESSOA: confirma que é a pessoa certa ou que pode falar agora
- CONVERSANDO: responde ou pergunta algo sobre o assunto, sem compromisso
- QUER_HUMANO: pede para falar com uma pessoa, ou desconfia de robô
- NEGOCIANDO: demonstra interesse concreto — preço, proposta, prazo, contrato
- JA_E_CLIENTE: diz que já é cliente ou já trabalha com a empresa
- PEDIU_PARAR: pede para não receber mais mensagens, reclama do contato
- PESSOA_ERRADA: diz que não é com ela, indica outra pessoa ou setor
- FORA_DA_BASE: pergunta algo que só quem conhece o negócio poderia responder
- AMBIGUO: não deu para entender com segurança

Na dúvida entre duas, escolha AMBIGUO. Errar para AMBIGUO é barato.

"confianca" é de 0 a 1 e deve refletir dúvida real. Use abaixo de 0.7 sempre 
que a mensagem for curta demais, irônica, ou aceitar mais de uma leitura.

O rascunho:
- no máximo 2 frases curtas, tom profissional e simples, sem emoji
- nunca invente preço, prazo, produto, condição ou qualquer fato do negócio
- nunca afirme ser humano nem finja ser uma pessoa específica
- se a intenção for QUER_HUMANO, NEGOCIANDO, JA_E_CLIENTE, PEDIU_PARAR, 
  FORA_DA_BASE ou AMBIGUO, devolva "rascunho": null — nesses casos quem 
  responde é o vendedor
- o objetivo, quando faz sentido, é propor uma ligação rápida

Empresa do lead: {empresa}

Conversa até aqui:
{histórico_das_últimas_12_mensagens}

Responda SOMENTE com um objeto JSON, sem cercas de código e sem comentários:
{"intencao": "...", "confianca": 0.0, "rascunho": "..." }
```

---

## 💼 Insights de Leads

### O que é

Resumo executivo pré-ligação gerado pela IA, que o vendedor vê **antes** de ligar.

### Fluxo

```
1. Vendedor clica em "Resumo por IA" no painel de leads
2. Backend chama POST /api/leads/{id}/ai-summary
3. IA lê dados já enriquecidos do lead (não faz busca nova)
4. Gera: 1 parágrafo + 3 bullets
5. Cacheia no banco (não gera 2x seguidas)
6. Vendedor vê no painel
```

### Dados Usados

```python
# Contexto montado de:
lead.company_name            # "Acme Corp"
lead.domain                  # "acme.com"
lead.sector                  # "Tecnologia"
lead.location                # "São Paulo, SP"
lead.description             # Resumo do que a empresa faz
lead.mx_provider             # "Google Workspace"
lead.hosting_provider        # "AWS"
lead.employee_count          # {"exact": 150} ou {"band": "50-200"}
lead.dns_report              # {"spf": true, "dmarc": true}
lead.decision_makers         # [{"name": "João Silva", "title": "CTO"}]
```

### Prompt Enviado à IA

```
Você é um analista de pré-vendas B2B brasileiro. Com base nos dados 
coletados automaticamente abaixo, escreva em português:

1. Um parágrafo curto (máx. 60 palavras) resumindo quem é a empresa e 
   seu provável momento/maturidade tecnológica.

2. Três bullets objetivos de ganchos de abordagem comercial baseados 
   APENAS nas evidências dos dados (ex.: provedor de e-mail, porte, decisores).

Não invente fatos que não estejam nos dados.

DADOS:
{dados_da_empresa}
```

### Exemplo de Saída

```
Empresa Acme Corp é uma startup de 50-150 pessoas no setor de fintech, 
com infraestrutura AWS e Google Workspace. Usa SPF e DMARC, sinalizando 
maturidade de segurança de e-mail. O CTO João Silva lidera a área técnica.

Ganchos de abordagem:
• Infraestrutura modern (AWS) — já investe em cloud, pronto para escalar
• Segurança de e-mail rigorosa (SPF+DMARC) — empresa pensa em compliance
• CTO na liderança — decisão técnica tem peso, não é só RH
```

---

## ⚙️ Configuração

### Setup Mínimo (Local)

1. **Obter chave Groq** (gratuita):
   ```bash
   # Acesse https://console.groq.com/keys
   # Crie conta com GitHub ou Google
   # Gere uma API key → gsk_...
   ```

2. **Adicionar ao `.env`**:
   ```bash
   GROQ_API_KEY=gsk_seu_api_key_aqui
   ```

3. **Rodar servidor**:
   ```bash
   cd lead_enricher
   python -m uvicorn main:app --reload
   ```

4. **Testar no simulador**:
   ```
   http://localhost:8000/app/wa/simulador
   ```

### Setup Produção (Vercel)

1. Ir em **vercel.com → lead-enricher → Settings → Environment Variables**
2. Adicionar novo:
   - **Key**: `GROQ_API_KEY`
   - **Value**: sua chave (gsk_...)
   - **Environments**: Production (e Preview se quiser)
3. Redeploy

---

## 🔧 Variáveis de Ambiente

### IA — WhatsApp

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `GROQ_API_KEY` | *(obrigatório)* | Chave de autenticação na API Groq |
| `WA_AI_MODEL` | `openai/gpt-oss-120b` | Modelo a usar (pode sobrescrever) |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Fallback se WA_AI_MODEL não existir |
| `WA_AI_TIMEOUT` | `20` | Segundos para IA responder (antes de timeout) |
| `WA_AI_MIN_CONFIDENCE` | `0.7` | Confiança mínima para confiar na classificação |

### IA — Insights

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `GROQ_API_KEY` | *(obrigatório)* | Compartilhada com WhatsApp |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Modelo para insights |

### WhatsApp — Geral

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `WHATSAPP_PHONE_NUMBER_ID` | *(obrigatório)* | ID do número no WABA |
| `WHATSAPP_ACCESS_TOKEN` | *(obrigatório)* | Token de acesso da Meta |
| `WHATSAPP_APP_SECRET` | *(obrigatório)* | App Secret para validar webhook |
| `WHATSAPP_VERIFY_TOKEN` | *(obrigatório)* | Token customizado para handshake |
| `WHATSAPP_TEMPLATE_NAME` | `primeiro_contato` | Template aprovado para abrir conversa |
| `WHATSAPP_TEMPLATE_LANG` | `pt_BR` | Idioma do template |
| `WHATSAPP_GRAPH_VERSION` | `v21.0` | Versão da API da Meta |

### Horários e Silêncio — ⚠️ desativado desde 2026-09-05

As variáveis abaixo ainda existem em `services/wa/gate.py`, mas
`service_window()` foi alterada para sempre devolver "pode enviar" — nenhuma
delas tem efeito hoje. A IA responde 24 horas por dia, todos os dias. Mantidas
na tabela só para quem for reativar a checagem de horário no futuro.

| Variável | Padrão | Descrição (sem efeito atualmente) |
|----------|--------|-----------|
| `WA_TIMEZONE` | `America/Sao_Paulo` | Fuso do comercial |
| `WA_SERVICE_START` | `9` | Hora de início (9h) |
| `WA_SERVICE_END` | `18` | Hora de término (18h) |
| `WA_QUIET_START` | `21` | Silêncio noturno começa às 21h |
| `WA_QUIET_END` | `8` | Silêncio noturno termina às 8h |
| `WA_WEEKEND_QUIET` | `0` | Se `1`, fim de semana também é silencioso |

Esta variável continua ativa e sem relação com horário — é o prazo para
reenviar o template a quem ainda não respondeu ao primeiro contato:

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `WA_TEMPLATE_RETRY_HOURS` | `72` | Horas de espera antes de reenviar o template de abertura |

### Exemplo `.env.example`

```bash
# ── IA ────────────────────────────────────────────────
GROQ_API_KEY=gsk_...
GROQ_MODEL=openai/gpt-oss-120b
WA_AI_MODEL=openai/gpt-oss-120b
WA_AI_TIMEOUT=20
WA_AI_MIN_CONFIDENCE=0.7

# ── WhatsApp ───────────────────────────────────────────
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_APP_SECRET=...
WHATSAPP_VERIFY_TOKEN=...
WHATSAPP_TEMPLATE_NAME=primeiro_contato
WHATSAPP_TEMPLATE_LANG=pt_BR

# ── Horários ───────────────────────────────────────────
WA_TIMEZONE=America/Sao_Paulo
WA_SERVICE_START=9
WA_SERVICE_END=18
WA_QUIET_START=21
WA_QUIET_END=8
WA_WEEKEND_QUIET=0
```

---

## 🔄 Fluxo Completo de Uma Conversa

### Cenário: Lead Responde "Quanto Custa?"

```
TIMESTEP 1: Meta envia webhook
┌──────────────────────────────────────────┐
│ POST /api/wa/webhook                     │
│ {from: "5511999999999",                  │
│  text: {"body": "quanto custa?"}}        │
└──────────────────────────────────────────┘
         ▼
TIMESTEP 2: Validação de assinatura
┌──────────────────────────────────────────┐
│ HMAC-SHA256(body, WHATSAPP_APP_SECRET)   │
│ matches X-Hub-Signature-256 header?      │
│ YES → continua                           │
│ NO  → 401, rejeita                       │
└──────────────────────────────────────────┘
         ▼
TIMESTEP 3: Carrega conversa do banco
┌──────────────────────────────────────────┐
│ SELECT * FROM conversations              │
│ WHERE phone_e164 = "+5511999999999"      │
│ and lead_id = ?                          │
│ Retorna: Conversation(state=ABERTA)      │
└──────────────────────────────────────────┘
         ▼
TIMESTEP 4: Portão #1
┌──────────────────────────────────────────┐
│ gate.can_send(db, conversa)              │
│ Checks (horário não é mais um deles):    │
│ - Lead está com relationship=LEAD? ✓    │
│ - Automação (ai_status) está ativa? ✓   │
│ - Não está opt-out? ✓ ativo             │
│ - Janela de 24h não expirou? ✓ 12h atrás│
│ ► Resultado: ALLOWED                    │
└──────────────────────────────────────────┘
         ▼
TIMESTEP 5: IA lê e classifica
┌──────────────────────────────────────────┐
│ brain.ler([histórico], empresa)          │
│ Prompt → Groq API                        │
│ Resposta JSON:                           │
│ {"intencao": "NEGOCIANDO",               │
│  "confianca": 0.95,                      │
│  "rascunho": null}                       │
│                                          │
│ Decisão: NEGOCIANDO → humano trata      │
└──────────────────────────────────────────┘
         ▼
TIMESTEP 6: Orquestrador avalia
┌──────────────────────────────────────────┐
│ if intencao in [NEGOCIANDO,              │
│                  QUER_HUMANO,            │
│                  PEDIU_PARAR]:           │
│   ação = CHAMOU_HUMANO                   │
│   ator = IA                              │
│   motivo = NEGOCIANDO                    │
│ else:                                    │
│   (não aplicável neste caso)             │
└──────────────────────────────────────────┘
         ▼
TIMESTEP 7: Registra handoff
┌──────────────────────────────────────────┐
│ INSERT wa_auditoria(                     │
│   conversation_id,                       │
│   acao='respondida',                     │
│   ator='ia',                             │
│   motivo='NEGOCIANDO',                   │
│   timestamp=now()                        │
│ )                                        │
│                                          │
│ UPDATE conversations                     │
│   SET state = 'HUMAN_HANDOFF'            │
│ WHERE id = ?                             │
└──────────────────────────────────────────┘
         ▼
TIMESTEP 8: Vendedor vê no painel
┌──────────────────────────────────────────┐
│ Aba "Conversas" mostra:                  │
│ [⚠️ Awaiting your reply]                 │
│  Lead: Acme Corp                         │
│  Message: "quanto custa?"                │
│  Sent by: IA (classificada)              │
│  Intent: NEGOCIANDO                      │
│  Badge: "Vendedor você tem 1 pendência"  │
└──────────────────────────────────────────┘
         ▼
TIMESTEP 9: Vendedor responde
┌──────────────────────────────────────────┐
│ POST /api/wa/conversations/{id}/send     │
│ {"text": "Enviamos nossa tabela..."}     │
│                                          │
│ client.send_text() envia via Meta        │
│ Grava: WaMessage(                        │
│   sent_by='human',                       │
│   direction='out'                        │
│ )                                        │
│ UPDATE conversations state = ABERTA      │
└──────────────────────────────────────────┘

FIM DO TURNO ✓
```

---

## 📊 Estados e Transições

### Estados Possíveis de uma Conversa

```
ABERTA (inicial)
    ├─ IA responde
    │  └─> ABERTA (continua conversando)
    │  └─> HUMAN_HANDOFF (confiança baixa ou intenção → humano)
    │
    ├─ Vendedor responde
    │  └─> ABERTA (continua)
    │
    ├─ Lead responde "não quero mais"
    │  └─> DO_NOT_CONTACT (opt-out)
    │
    └─> Pausa / Fechamento manual
       └─> PAUSED / CLOSED
```

### Mudanças de Estado

| Evento | De → Para | Motivo |
|--------|-----------|--------|
| Mensagem recebida | PAUSED | ABERTA | Retoma conversa pausada |
| IA classifica como PEDIU_PARAR | ABERTA | DO_NOT_CONTACT | Lead quer sair |
| Vendedor clica "Assumir agora" | ABERTA | ABERTA | Vendedor toma controle |
| Vendedor responde | HUMAN_HANDOFF | ABERTA | Vendedor ativado |
| Janela de 24h expira | ABERTA | CLOSED | Timeout da conversa |

---

## ⚠️ Tratamento de Erros

### Filosofia

**"Silêncio é Falha" + "Na Dúvida, Não Envia"**

- Tudo que der errado → `HUMAN_HANDOFF` com motivo
- Nunca falha silenciosamente
- Vendedor sempre vê o que aconteceu

### Cenários de Erro

#### 1. IA fora do ar ou timeout
```python
# Se timeout > 20s ou HTTP 5xx:
leitura = Leitura(
    intencao="AMBIGUO",
    confianca=0.0,
    erro="A IA não respondeu."
)
# Resultado: HUMAN_HANDOFF
# Painel mostra: "Falha na IA — responda você"
```

#### 2. JSON malformado
```python
# Se resposta não é JSON válido:
leitura = Leitura(
    intencao="AMBIGUO",
    confianca=0.0,
    erro="Resposta da IA fora do formato."
)
# Resultado: HUMAN_HANDOFF
```

#### 3. Intenção desconhecida
```python
# Se IA inventar uma intenção nova:
if intencao not in INTENCOES:
    leitura = Leitura(
        intencao="AMBIGUO",
        confianca=0.0,
        erro=f"Intenção desconhecida: {intencao}"
    )
# Resultado: HUMAN_HANDOFF
```

#### 4. Confiança baixa
```python
# Se confianca < 0.7:
if leitura.confianca < CONFIANCA_MINIMA:
    # Trata como AMBIGUO mesmo que intenção seja válida
    leitura = Leitura(
        intencao="AMBIGUO",
        confianca=leitura.confianca,
        erro=f"Confiança baixa: {leitura.confianca:.2f}"
    )
# Resultado: HUMAN_HANDOFF
```

#### 5. Meta API recusa envio
```python
# Se client.send_text() falha:
try:
    client.send_text(phone, text)
except HTTPError as e:
    # Registra na auditoria
    states.registrar_erro(
        conversation_id,
        motivo=f"Meta recusou: {e.status_code}"
    )
    # Conversa fica em HUMAN_HANDOFF pendente
# Painel mostra: "Falha ao enviar — Meta API indisponível"
```

### Log de Erro Típico

```json
{
  "time": "2026-09-05T15:30:00",
  "level": "WARNING",
  "logger": "services.wa.brain",
  "msg": "Falha na chamada de classificação: ConnectTimeout",
  "conversation_id": "conv_12345",
  "phone": "5511999999999",
  "motivo": "Timeout na IA"
}
```

---

## ⏱️ Limitações e Timeouts

### Timeouts

| Componente | Timeout | Ação se exceder |
|------------|---------|-----------------|
| IA (brain) | 20s | AMBÍGUO → humano |
| Insights | 30s | Retorna `null` → 503 |
| Meta webhook | 60s | Meta retenta 5x |
| HTTP requests | 15s | Reconecta ou falha |

### Limites de Uso

| Limite | Valor | Razão |
|--------|-------|-------|
| Histórico de mensagens lido | 12 mensagens | Reduz tokens/custo |
| Max tokens IA resposta | 400 tokens | Cabe em celular |
| Max tokens insights | 600 tokens | Não é parágrafo demais |
| Tentativas de envio Meta | 5 | Padrão do Groq |
| Conversa "janela aberta" | 24 horas | Regra da Meta — grátis dentro desse prazo |
| Reenvio de template (`WA_TEMPLATE_RETRY_HOURS`) | 72 horas | Prazo antes de convidar de novo quem não respondeu |
| Turnos fora do horário | 3 por noite | Evita spam noturno |

### Rate Limits Groq API

```
Free Tier:
- 10,000 requisições/mês
- 30 RPM (requisições por minuto)
- Modelo: openai/gpt-oss-120b

Lead-Enricher estima:
- ~5-10 mensagens por lead por dia
- ~100-200 leads ativos
- ~500-2000 chamadas de IA/dia
- Dentro do limite gratuito ✓
```

---

## 💡 Exemplos de Uso

### Exemplo 1: Setup Local Simples

```bash
# 1. Clone repo
git clone https://github.com/jpiresantunes29-dotcom/lead-enricher.git
cd lead_enricher

# 2. Crie .env
echo "GROQ_API_KEY=gsk_..." >> .env
echo "WHATSAPP_PHONE_NUMBER_ID=..." >> .env
echo "WHATSAPP_ACCESS_TOKEN=..." >> .env
# ... resto das variáveis

# 3. Rodar
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 4. Testar IA direto
python -c "
from services.wa import brain
leitura = brain.ler([{'direction': 'in', 'body': 'oi, tudo bem?'}])
print(f'Intenção: {leitura.intencao}')
print(f'Confiança: {leitura.confianca}')
print(f'Rascunho: {leitura.rascunho}')
"
```

### Exemplo 2: Testar Classificação Manual

```python
from services.wa.brain import ler, INTENCOES

conversas = [
    [{"direction": "in", "body": "Oi! Somos a Acme Corp"}],
    [{"direction": "in", "body": "Quanto custa o plano pro?"}],
    [{"direction": "in", "body": "Já somos clientes de vocês"}],
    [{"direction": "in", "body": "Não quero mais receber mensagens"}],
]

for msgs in conversas:
    leitura = ler(msgs)
    assert leitura.intencao in INTENCOES
    print(f"✓ {leitura.intencao:20} (confiança {leitura.confianca:.2f})")
```

### Exemplo 3: Gerar Insight de Lead

```python
from services.ai_insights import generate_summary
from models.database import Lead, DecisionMaker

lead = Lead(
    company_name="Acme Corp",
    domain="acme.com",
    sector="Fintech",
    employee_count={"band": "50-200"},
    location="São Paulo, SP"
)

dm = DecisionMaker(name="João Silva", title_found="CTO")

summary = generate_summary(lead, [dm])
print(summary)
# Retorna:
# "Acme Corp é uma fintech de 50-200 pessoas em São Paulo...
#  • Infraestrutura cloud moderna (AWS)
#  • CTO de decisão técnica
#  • ..."
```

### Exemplo 4: Simular Turno Completo

```python
from services.wa import orchestrator, brain
from models.database import Conversation, Lead

# Setup
db = Session()
lead = db.query(Lead).first()
conversa = db.query(Conversation)\
    .filter(Conversation.lead_id == lead.id)\
    .first()

# Simula resposta de lead
nova_msg = WaMessage(
    conversation_id=conversa.id,
    direction="in",
    body="Sim, sou eu! Posso te ligar amanhã?",
    type="text"
)
db.add(nova_msg)
db.commit()

# Roda turno completo
turno = orchestrator.responder(db, conversa)

# Resultado
print(f"Ação: {turno.acao}")  # "enviou" ou "chamou_humano"
print(f"Motivo: {turno.motivo}")  # "CONFIRMOU_PESSOA"
print(f"Horário: {turno.fora_do_horario}")
```

---

## 🔍 Troubleshooting

### Problema: "IA não está configurada"

**Sintoma**: Ao abrir o simulador, aparece "GROQ_API_KEY ausente"

**Checklist**:
- [ ] `GROQ_API_KEY` está definida em `.env`?
- [ ] `.env` está sendo carregado? (check: `from dotenv import load_dotenv; load_dotenv()`)
- [ ] Valor começa com `gsk_`?
- [ ] Sem espaços extras antes/depois?

**Solução**:
```bash
# Verifique
cat .env | grep GROQ_API_KEY

# Se vazio, obtenha chave em:
# https://console.groq.com/keys

# Adicione e teste
python -c "
import os
from dotenv import load_dotenv
load_dotenv()
print('GROQ_API_KEY:', 'PRESENTE' if os.getenv('GROQ_API_KEY') else 'AUSENTE')
"
```

---

### Problema: Timeout na IA (mensagem demora 20+s)

**Sintoma**: "A IA não respondeu" ou delay de 20s+ antes de handoff

**Checklist**:
- [ ] Groq API está up? (test: `curl https://api.groq.com/openai/v1/models` com header `Authorization: Bearer gsk_...`)
- [ ] Internet está ok?
- [ ] `WA_AI_TIMEOUT` é realista? (padrão 20s, máximo 60s)

**Solução**:
```bash
# Teste latência Groq
time curl -X POST https://api.groq.com/openai/v1/chat/completions \
  -H "Authorization: Bearer $GROQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-oss-120b", "max_tokens":10, 
       "messages":[{"role":"user","content":"OK"}]}'

# Se > 10s, Groq pode estar lento. Tente mais tarde.
```

---

### Problema: IA classifica tudo como "AMBÍGUO"

**Sintoma**: Qualquer mensagem vira AMBÍGUO, nada é respondido

**Possíveis causas**:
1. Confiança mínima muito alta → aumentar threshold
2. IA recebendo entrada ruim → checar prompt
3. Timeout → aumentar `WA_AI_TIMEOUT`

**Solução**:
```bash
# Abaixe confiança mínima de teste
export WA_AI_MIN_CONFIDENCE=0.5  # era 0.7

# Teste novamente
python -c "
from services.wa import brain
leitura = brain.ler([
    {'direction': 'in', 'body': 'Oi, quanto custa?'}
])
print(f'Intenção: {leitura.intencao}')
print(f'Confiança: {leitura.confianca}')
"

# Se melhorou: ajuste em .env permanentemente
```

---

### Problema: "A IA cometeu um erro, respondeu errado"

**Sintoma**: IA respondeu com informação incorreta ou tom inadequado

**Checklist**:
- [ ] Qual foi a intenção classificada?
- [ ] Qual era a confiança?
- [ ] Mensagem era ambígua?

**Ação**:
1. Classifique como "QUER_HUMANO" para bloqueá-la
2. Levante `WA_AI_MIN_CONFIDENCE` (ex: 0.75 → 0.80)
3. Peça handoff mais fácil: abaixe em intenções "automáticas"

**Ajuste permanente no `.env`**:
```bash
# Menos autoresposta, mais humano
WA_AI_MIN_CONFIDENCE=0.85  # era 0.7
```

---

### Problema: WhatsApp não autoriza IA a responder

**Sintoma**: Conversa recebe "Portão bloqueou" em vez de resposta IA

**Checklist** (horário não é mais um motivo possível — ver nota no topo do doc):
- [ ] Lead está marcado como `relationship=LEAD`?
- [ ] `ai_status` da conversa é `AI_ACTIVE`?
- [ ] Janela de 24h não expirou?
- [ ] Lead não está com vendedor?
- [ ] Número não é opt-out?

**Diagnóstico**:
```python
from services.wa import gate
from models.database import Conversation

conversa = db.query(Conversation).first()
decisao = gate.can_send(db, conversa)

print(f"Allowed: {decisao.allowed}")
print(f"Reason: {decisao.reason}")
print(f"Detail: {decisao.detail}")
# Outputs: allowed=False, reason='quiet_hours', detail='...'
```

---

### Problema: Rate limit — "Muitas requisições"

**Sintoma**: Código HTTP 429 de Groq

**Análise**:
```python
# Free tier Groq: 30 RPM (requisições/minuto)
# Se você tem 1000 leads recebendo mensagens simultaneamente:
# 1000 / 60s = 16.67 RPS < 30 RPM ✓

# Mas se houver picos:
# 30 + 20 = 50 RPS > 30 RPM ✗ → 429
```

**Solução**:
1. **Fila com retry**: Já implementado em `orchestrator.py`
   - 1º retry: 1s depois
   - 2º retry: 5s depois
   - 3º retry: abandona, handoff

2. **Upgrade Groq**: Se taxa não couber em free tier
   - Planos pagos: sem limite de RPM
   - Caro mas escalável

---

## 📚 Referências Rápidas

### Arquivos Principais

```
lead_enricher/
├── services/wa/
│   ├── brain.py          ← Classificação + rascunho
│   ├── orchestrator.py   ← Orquestração
│   ├── gate.py           ← Validações
│   ├── client.py         ← Envio via Meta
│   ├── states.py         ← Auditoria
│   ├── webhook.py        ← Recebimento
│   └── sandbox.py        ← Simulador
│
├── services/
│   ├── ai_insights.py    ← Resumos executivos
│   └── preflight.py      ← Validação de setup
│
├── routers/
│   ├── wa.py             ← Rotas de WhatsApp
│   ├── wa_sandbox.py     ← Rotas do simulador
│   └── integrations.py   ← Endpoints de IA
│
└── docs/
    └── PRODUCAO.md       ← Setup para produção
```

### Endpoints Públicos

```
GET  /health                              Status geral
POST /api/wa/webhook                      Recepção de mensagens WhatsApp
GET  /api/wa/simulador                    Interface do simulador

POST /api/leads/{id}/ai-summary           Gera resumo executivo
GET  /api/integrations/status             Status da IA (req. autenticação)
```

### Endpoints Internos (CRON_SECRET)

```
GET  /api/internal/preflight              Diagnóstico completo
POST /api/internal/jobs/run               Executa fila de análise
POST /api/internal/wa/pending             Responde conversas pendentes
```

---

## 📞 Suporte

Dúvidas? Consulte:
1. [`docs/PRODUCAO.md`](docs/PRODUCAO.md) — Setup e troubleshooting
2. [`services/wa/brain.py`](services/wa/brain.py) — Código fonte comentado
3. [`services/wa/orchestrator.py`](services/wa/orchestrator.py) — Fluxo completo
4. Logs do servidor: `docker logs` ou `vercel logs lead-enricher`

---

**Última atualização**: 2026-09-05 | **Versão**: 2.1.0 (Groq)
