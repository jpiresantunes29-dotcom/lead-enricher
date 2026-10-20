# Fluxo comercial automatizado — contrato de API

Automatiza os quatro fluxogramas de prospecção (Captura e Priorização,
Agendamento do 1º contato, Contatos e Qualificação, Aquecimento de lead frio)
a partir do fluxo principal do produto: **digitou o domínio → coletou → o
resto acontece sozinho**.

Todos os endpoints exigem o Bearer token (`authFetch`) e só enxergam leads do
próprio usuário. Datas em ISO 8601; dias de agenda (`dia`) em `YYYY-MM-DD` no
fuso America/Sao_Paulo.

## 1. Tier (Captura e Priorização)

Calculado automaticamente ao fim de toda coleta (`/api/enrich`, lote, re-coleta).

| Tier | Regra |
|---|---|
| 4 | Já é cliente Skymail/revenda (MX Skymail, base de clientes do time ou `relationship=CUSTOMER`), **ou** outro executivo do mesmo time já trata o domínio, **ou** < 50 funcionários |
| 3 | > 1.000 funcionários |
| 2 | 50–1.000 e e-mail Microsoft 365 / Google Workspace (MX ou, atrás de gateway, SPF) |
| 1 | 50–1.000 e outro provedor — alvo prioritário |
| suposto | porte desconhecido (ou faixa que cruza 50/1.000): assume porte médio e aplica a regra do MX (Microsoft/Google → 2, outro ou sem MX → 1), com `tier_details.needs_review = true`. Informar o porte na ficha refaz a classificação com o dado real |

Campos novos em `LeadOut` (e em `LeadListOut`, menos `tier_details`):

```
tier: 1|2|3|4|null          tier efetivo (manual vence o automático)
tier_auto: 1|2|3|4|null     o que a régua calculou
tier_manual: 1|2|3|4|null   escolha do vendedor
tier_reason: str            frase pronta ("Porte médio (51–200 funcionários) e usa Locaweb — alvo prioritário.")
tier_details: {path: [{pergunta, resposta, detalhe?}], email_service: {familia, rotulo, evidencia},
               employees: {min,max,exact,fonte}|null, needs_review: bool, version}
relationship: LEAD|CUSTOMER|DO_NOT_CONTACT|BLOCKED
cadence: null|agendado|tentativas|qualificacao|proposta|aquecimento|encerrado   (null = não executado)
cadence_attempt: int        tentativa atual (1..3)
first_contact_at: datetime|null
```

- `GET /api/leads?tier=1&cadence=nao_executado&sort=tier` — filtros novos. `sort` aceita `recent|tier`. `cadence` aceita os valores acima ou `nao_executado`.
- `PATCH /api/leads/{id}/tier` `{tier: 1..4 | null}` → `LeadOut`. `null` volta para o automático.
- `POST /api/leads/{id}/reclassificar` → `LeadOut`.
- `PATCH /api/leads/{id}` agora aceita também `{employees: int, relationship: "LEAD"|"CUSTOMER"|"DO_NOT_CONTACT"}` (além de `phone`). Reclassifica o tier na hora.
- `POST /api/tiers/reclassificar` → `{success, reclassificados, message}` — classifica todas as fichas existentes (base anterior à funcionalidade). Não vai à rede.
- `LeadListOut` traz `tier_needs_review: bool` (sem tier e não pendente) para o filtro "Revisar".
- `GET /api/tiers/resumo` → `{por_tier: {"1":n,"2":n,"3":n,"4":n,"sem_tier":n}, nao_executados: {"1":n,"2":n,"3":n}, revisar: n}`

### Base de clientes do time (Skymail / revenda)

O "time" é o domínio do e-mail do usuário (vendedores @empresa.com.br compartilham a base).

- `GET /api/clientes` → `{team, total, itens: [{domain, kind, created_at}]}`
- `POST /api/clientes` `{texto: "dom1.com.br\ndom2.com …", kind: "skymail"|"revenda"}` → `{success, message, adicionados, ignorados, reclassificados}`
- `DELETE /api/clientes/{domain}` → 204

## 2. Agendamento do 1º contato (lote 40/5/5)

- `POST /api/agenda/lote` `{dia: "2026-10-21", total: 50, mix: {"1":40,"2":5,"3":5}, preview: true|false}`
  - Seleciona leads **não executados** (`cadence=null`), `relationship=LEAD`, tier 1–3, ordenados por tier e depois pelo mais antigo. Faltou lead num tier → completa com o próximo tier disponível.
  - `preview: true` só mostra; `false` cria uma tarefa "1º contato" (`step=primeiro_contato`) às 09:00 do dia, marca `cadence=agendado` e documenta.
  - Resposta: `{success, message, dia, por_tier: {"1":n,"2":n,"3":n}, disponiveis: {"1":n,"2":n,"3":n}, selecionados: [{lead_id, domain, company_name, tier}], criadas: n}`

## 3. Agenda do dia (lembretes)

- `GET /api/agenda?dia=YYYY-MM-DD` (padrão: hoje) →
```
{dia, resumo: {atrasadas: n, hoje: n, por_tier: {"1":n,...}},
 atrasadas: [Item], hoje: [Item]}
Item = {activity: ActivityOut,
        lead: {id, domain, company_name, tier, cadence, cadence_attempt, phone},
        contato: {id, name, title, phone, email, linkedin_url} | null,
        acao: {titulo, dica, tel_url|null, whatsapp_url|null, mailto_url|null}}
```
`acao` traz os links prontos (tel:, https://wa.me/…?text=…, mailto:…?subject=…&body=…) com o texto da etapa — um clique e o vendedor está falando com o lead.

## 4. Cadência por lead (Contatos e Qualificação + Aquecimento)

- `GET /api/leads/{id}/cadencia` →
```
{cadence, cadence_attempt, tier, proximas: [ActivityOut abertas da cadência, mais cedo primeiro],
 acoes: [{resultado, label, estilo: "primary"|"ghost"|"danger", pede_data: bool}]}
```
  `acoes` já vem filtrada pelo estado: a tela só desenha os botões que fazem sentido agora.

- `POST /api/leads/{id}/cadencia` `{resultado, notas?, reuniao_em?: datetime, contato_id?}` →
  `{success, message, lead: LeadOut, criadas: [ActivityOut], cadencia: <mesmo formato do GET>}`

| resultado | O que acontece |
|---|---|
| `iniciar` | Cria a tarefa de 1º contato para hoje; `cadence=tentativas`, tentativa 1 |
| `sem_contato` | Tentativa 1 → tarefa 2ª tentativa D+7; 2 → 3ª tentativa D+7; 3 → entra no aquecimento |
| `telefone_errado` | Tarefa "Mapear contato alternativo" para hoje; a tentativa não avança |
| `reuniao_marcada` | Reunião de qualificação em `reuniao_em` (padrão: próximo dia útil 10:00); stage `reuniao_agendada` |
| `qualificado_com_demanda` | `cadence=proposta`, stage `oportunidade`, tarefa "Construir proposta" em 2 dias úteis |
| `qualificado_sem_demanda` / `nao_qualificado` | Entra no aquecimento |
| `aquecimento` | Entra no aquecimento manualmente |
| `ganho` / `perdido` | Encerra a cadência e move o stage |
| `encerrar` | Encerra a cadência e remove as tarefas abertas dela |

**Aquecimento (quinzenal):** e-mail 1 no próximo dia útil, e-mail 2 em +14 dias, e-mail 3 em +28, WhatsApp lista em +42. Concluir a etapa de WhatsApp (`PATCH /api/activities/{id}` `{completed:true}`) agenda o próximo ciclo — o fluxograma volta ao início.

`ActivityOut` ganha `step` (`primeiro_contato|tentativa_2|tentativa_3|contato_alternativo|reuniao_qualificacao|proposta|aquec_email_1|aquec_email_2|aquec_email_3|aquec_whatsapp`) e `decision_maker_id`.
`GET /api/followups/today` passa a incluir `type=email` (etapas de aquecimento).

## 5. Contatos do decisor cadastrados à mão

- `GET /api/leads/{id}/decisores` → `[DecisionMakerOut]` — todos os contatos salvos na ficha (manuais e revelados), com `source` (`manual`, `lusha`, `free`…), `email` (atalho para o primeiro e-mail) e `is_primary`.
- `POST /api/leads/{id}/decisores` `{name*, title?, phone?, email?, linkedin_url?, is_primary?}` → `DecisionMakerOut` (`source="manual"`).
- `PATCH /api/decisores/{id}` aceita `name, title, phone, email, linkedin_url, is_primary` (só os enviados mudam; `""` apaga).
- `DELETE /api/decisores/{id}` → 204.

O contato principal (`is_primary`) é quem a agenda usa para montar os links de telefone/WhatsApp/e-mail.
