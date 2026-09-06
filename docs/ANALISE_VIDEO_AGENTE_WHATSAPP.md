# Análise crítica: vídeo "Automação Linear vs Agente de IA" aplicado ao nosso agente SDR

> Baseado na transcrição enviada + prints do vídeo (clínica de estética, WhatsApp
> oficial + painel do dono). Contrastado contra o código real do LeadEnricher
> (`services/wa/*`, `routers/wa.py`, `models/database.py`) e contra o
> planejamento já existente em [`PLANO_WHATSAPP_E_DYNAMICS.md`](PLANO_WHATSAPP_E_DYNAMICS.md)
> e [`ROADMAP_FUNCIONALIDADES.md`](ROADMAP_FUNCIONALIDADES.md).
>
> **Achado principal, antes de entrar em cada conceito:** o vídeo descreve, para
> uma clínica, praticamente a mesma arquitetura que já projetamos e em boa parte
> já implementamos para o SDR — portão determinístico, máquina de estados,
> painel com pausar/assumir, auditoria, LGPD/opt-out. Em vários pontos o nosso
> desenho já é **mais rigoroso** que o do vídeo (ex.: o vídeo só cita "confiança
> ≥ algo" informalmente; nós temos dois eixos de estado separados —
> `Lead.relationship` e `conversation.ai_status` — exatamente para impedir a IA
> de falar com cliente atual). Por isso esta análise é mais sobre **lacunas
> específicas** do que sobre "construir do zero".

---

## 0. Baseline — o que já existe hoje

| Peça | Onde está | Status |
|---|---|---|
| Webhook + validação HMAC | `services/wa/webhook.py` | ✅ Feito |
| Portão determinístico (`can_send`) | `services/wa/gate.py` (387 linhas) | ✅ Feito — cobre opt-out, quiet hours, fim de semana, janela 24h, "já com humano" |
| Classificação de intenção + rascunho (LLM) | `services/wa/brain.py` | ✅ Feito — 9 intenções, `confianca`, Groq free tier |
| Orquestração (decide enviar ou chamar humano) | `services/wa/orchestrator.py` | ✅ Feito |
| Envio via Meta + templates | `services/wa/client.py` | ✅ Feito (1 template fixo via env var) |
| Auditoria | `services/wa/states.py` + tabela `wa_auditoria` | ✅ Feito |
| Painel: pausar / assumir / retomar / enviar manual | `routers/wa.py` (709 linhas) | ✅ Backend feito; UI a confirmar (ver §2.5) |
| Máquina de estados (`ai_status`) + `Lead.relationship` | `models/database.py` | ✅ Feito — dois eixos separados (estrutural × automação) |
| Insights de pré-venda (resumo do lead) | `services/ai_insights.py` | ✅ Feito — mas é sobre a empresa do **lead**, não uma base de conhecimento da **nossa** oferta |
| Pipeline/funil (kanban) | `Lead.stage` (roadmap 1.1b) | ✅ Já existe, fora do módulo WA |
| CRM externo | `services/crm/webhook.py` → Power Automate/Dynamics | ✅ Já existe |
| Memória de curto prazo | `brain.py` lê últimas 12 mensagens | ✅ Feito |
| Modelo de IA | Groq free tier (`openai/gpt-oss-120b`) | ✅ Feito, custo ~R$ 0 |
| Debounce de mensagens picotadas | — | ❌ Não existe |
| Áudio/imagem (multimodal) | — | ❌ Não existe |
| Múltiplos templates geridos pelo painel | — | ❌ Só 1 template fixo (`WHATSAPP_TEMPLATE_NAME`) |
| Ferramenta de agenda (Google Calendar) | — | ❌ Não existe — hoje vira `Activity(type=meeting)` + handoff humano |
| Base de conhecimento da nossa oferta (constituição) | — | ❌ Não existe |
| Teste automatizado com "cliente simulado" adversário | — | ⚠️ Parcial — já existe `sandbox.py`/`wa_sandbox.py` e 657 testes, mas não um gerador de conversas adversárias com relatório de acerto |

Isso muda o enquadramento do pedido: a pergunta não é "vale a pena construir um
agente assim", é "quais das ~16 ideias do vídeo preenchem uma lacuna real, sem
contradizer decisões de arquitetura que já tomamos (§16 do plano: *IA nunca
decide regra crítica*, *sem WhatsApp não-oficial*, *sem servidor sempre-ligado*)".

---

## 1. Conceito por conceito

### 1.1 Os 4 pilares do agente (Modelo de IA, Prompt, Ferramentas, Memória)

- **O que é:** todo agente de IA precisa de um LLM, um prompt que define objetivo/limites, ferramentas de ação e memória de curto prazo; tirar uma peça quebra o conjunto.
- **Por que é relevante:** é um checklist de sanidade arquitetural, não uma feature.
- **Classificação: Essencial** — mas já está implementado. `brain.py` = modelo + prompt; `gate.py`/`orchestrator.py` fazem o papel de "ferramentas de decisão"; histórico de 12 mensagens = memória.
- **Como aplicar:** nenhuma mudança de arquitetura. Vale só **formalizar o prompt** em seções nomeadas (objetivo / ferramentas disponíveis / como agir / nunca fazer) do jeito que já é, para facilitar manutenção — ver §1.16.
- **Impacto:** nenhum (documentação/organização apenas).

### 1.2 Ciclo Percebe → Decide → Age, com a IA escolhendo livremente as ferramentas

- **O que é:** no vídeo, o próprio modelo de IA decide, mensagem a mensagem, quais ferramentas usar (agenda, planilha, CRM, etc.) — é um agente "tool-calling" de verdade.
- **Por que é relevante:** é o que dá flexibilidade para lidar com entrada imprevisível (texto solto, áudio, foto).
- **Classificação: Não faz sentido copiar como está — decisão já tomada e correta em sentido oposto.** O próprio vídeo admite: *"sempre que puder usar automação linear em vez de agente, use automação linear, porque um agente tem margem de erro"*. Nosso `PLANO_WHATSAPP_E_DYNAMICS.md` (§16, item 3) já bane explicitamente "deixar a IA decidir regras críticas". Um SDR que qualifica e agenda tem um espaço de ações muito mais restrito (avançar a conversa, chamar humano, propor horário) do que uma recepção de clínica com preço/agenda/cancelamento/promoção — não precisamos de um agente livre para isso.
- **Como aplicar:** manter o desenho atual — LLM só classifica intenção + redige rascunho; `orchestrator.py` (código determinístico) decide a ação. Se algum dia surgir uma ferramenta nova (ex.: consultar disponibilidade de agenda), ela entra como **leitura opcional que o LLM pode citar na resposta**, não como ação que o LLM dispara sozinho.
- **Impacto:** nenhum na arquitetura atual (confirma o desenho já escolhido).

### 1.3 Critério "automação linear vs agente" (entrada previsível vs imprevisível; quem decide o caminho)

- **O que é:** o vídeo resume: automação linear para rotina/dados previsíveis (planilha, CRM, e-mail), agente de IA só para a parte conversacional imprevisível.
- **Por que é relevante:** é o critério que evita usar IA (custo, latência, erro) onde um `if` resolveria.
- **Classificação: Essencial — já é exatamente o padrão do projeto.** O portão (`gate.py`), a máquina de estados, o push para CRM via webhook e o funil (`Lead.stage`) são 100% automação linear. Só a leitura da mensagem do lead (`brain.ler`) usa IA.
- **Como aplicar:** ao adicionar qualquer feature nova no módulo WA, perguntar primeiro "isso é dado estruturado (linear) ou linguagem livre (IA)?" antes de tocar no prompt.
- **Impacto:** nenhum — é um princípio de design a manter, não uma tarefa.

### 1.4 Portão determinístico antes de qualquer envio ("constituição de segurança")

- **O que é:** uma camada de regras fixas (horário, opt-out, janela de 24h, quem está atendendo) que barra o envio independente do que a IA decidiu.
- **Por que é relevante:** é a peça que impede a IA de "inventar" ou falar fora de hora/com quem não deveria.
- **Classificação: Essencial — já feito, e mais completo que o do vídeo.** `gate.py` cobre: `not_a_lead`, `opted_out`, `quiet_hours`, `window_closed`, `already_open`, `awaiting_reply` — o vídeo nem menciona LGPD/opt-out nem "cliente atual nunca recebe IA" (`Lead.relationship`), que é uma proteção que já temos e o vídeo não tem.
- **Como aplicar:** nenhuma ação — apenas reforço de que este é o componente mais crítico e deve continuar sendo o único ponto por onde passa qualquer envio (já é assim).
- **Impacto:** nenhum.

### 1.5 Segunda checagem do portão imediatamente antes do envio (corrida entre IA e humano)

- **O que é:** no vídeo, a resposta da IA passa pelo portão de novo bem antes de sair, porque nos segundos entre a IA decidir e o envio o vendedor pode ter assumido a conversa.
- **Por que é relevante:** evita a IA responder por cima de um humano que acabou de assumir.
- **Classificação: Essencial — já coberto.** O `PLANO_WHATSAPP_E_DYNAMICS.md` (§10, "IA não parar quando mandam parar") já registra que o worker relê o estado antes de cada envio.
- **Como aplicar / Impacto:** nenhum — apenas vale um teste de regressão dedicado (`test_wa_jornada.py` já existe; confirmar que cobre esse cenário exato de corrida).

### 1.6 Painel do dono: pausar / assumir / responder manualmente como WhatsApp Web

- **O que é:** driblar a limitação da API oficial (perde-se o WhatsApp Web) com um painel que mostra a conversa e permite digitar manualmente.
- **Por que é relevante:** sem isso, o time de vendas fica sem forma de intervir numa conversa individual.
- **Classificação: Essencial — já implementado no backend.** `routers/wa.py` tem `pausar`/`assumir`/`retomar`, envio manual e status (`AI_ACTIVE`/`AI_PAUSED`/`HUMAN_HANDOFF`/`STOPPED`) exatamente como no vídeo.
- **Como aplicar:** confirmar que a **UI** (aba "Conversas", provavelmente dentro do app de hash `index.html`, no padrão das outras telas) está de fato construída e testada manualmente — o roadmap trata isso como item de UI ainda a validar. Se a UI não estiver pronta, é o item de maior prioridade desta lista, pois sem ela o backend não tem uso prático.
- **Impacto:** UI/frontend — nenhuma mudança de banco ou de IA necessária, já existe o suporte no backend.

### 1.7 Templates de mensagem geridos pelo painel (criar modelo, escolher categoria, reabrir conversa)

- **O que é:** no vídeo, o dono cria e escolhe entre vários templates HSM (lembrete, remarketing, etc.) direto do painel, com variáveis preenchidas automaticamente da conversa.
- **Por que é relevante:** depois que a janela de 24h fecha, só um template aprovado reabre a conversa — sem isso, um lead que sumiu por 1 dia fica perdido.
- **Classificação: Recomendado** (não essencial no dia 1, mas de baixo custo e alto retorno assim que houver follow-up de SDR). Hoje só existe **um** template fixo (`WHATSAPP_TEMPLATE_NAME`), usado para o primeiro contato.
- **Como aplicar:** criar um registro simples (`wa_templates`: nome, categoria Meta, corpo, variáveis esperadas) e, no endpoint de reabertura, permitir escolher entre os templates aprovados na conta Meta — sem preencher variável automaticamente na v1 (isso pode ficar para depois; o vídeo mesmo mostra que quando a variável não existe, o dono digita manualmente).
- **Impacto:** tabela nova pequena + endpoint no `routers/wa.py`; nenhuma mudança em `brain.py`/`gate.py`.

### 1.8 Janela de 24h da API oficial (mensagem livre grátis vs template pago)

- **O que é:** regra da Meta — responder dentro de 24h da última mensagem do lead é grátis; reabrir depois disso exige template pago.
- **Por que é relevante:** é o maior fator de custo real do canal.
- **Classificação: Essencial — já implementado e já documentado em detalhe** em `conversation.window_expires_at` e no §6/§14 do `PLANO_WHATSAPP_E_DYNAMICS.md` (que já tem a tabela de custo Meta). Nada a fazer aqui além do que já está feito.

### 1.9 Base de conhecimento / "constituição" para não inventar preço, prazo ou condição

- **O que é:** um documento único com todos os fatos do negócio (preços, horários, políticas) que o agente consulta antes de responder, para nunca alucinar.
- **Por que é relevante:** é a principal causa de erro/alucinação em agentes de atendimento.
- **Classificação: Recomendado, com ressalva importante.** Note que hoje `brain.py`, por desenho, já **não deixa a IA falar de preço/condição/proposta** — qualquer coisa desse tipo cai em `NEGOCIANDO` → handoff humano. Ou seja, o maior risco que a constituição resolve no vídeo (a IA inventar preço) **já está mitigado por regra determinística no nosso sistema**, não por base de conhecimento. O que falta é mais modesto: um bloco curto de contexto institucional (quem somos, o que vendemos, para quem, 2-3 diferenciais) para a IA responder perguntas informativas simples (`CONVERSANDO`) com mais qualidade, sem inventar.
- **Como aplicar:** adicionar um arquivo `constituicao_sdr.md` curto (não uma base de dados dinâmica) injetado no prompt de `brain.py`, cobrindo só "sobre a empresa" — mantendo a regra de que preço/prazo/condição continuam indo para humano.
- **Impacto:** mudança pequena e localizada em `brain.py` (mais contexto no prompt); zero mudança em `gate.py`/`orchestrator.py`.

### 1.10 Ferramentas de agenda (ver horários livres, criar evento no Google Calendar)

- **O que é:** o agente do vídeo consulta e cria eventos diretamente no Google Calendar da clínica.
- **Por que é relevante:** é literalmente um dos objetivos do nosso projeto ("realizar ou auxiliar no agendamento de reuniões").
- **Classificação: Essencial o objetivo, mas Opcional/Futuro a automação completa.** Hoje nosso plano já cobre isso, mas com um desenho mais conservador: quando o lead topa uma ligação, o sistema cria `Activity(type=meeting)` + `.ics` e faz **handoff para o humano confirmar o horário** — não deixa a IA criar o compromisso final sozinha. Isso é coerente com a regra de ouro do projeto (IA não decide/executa ação crítica sozinha) e evita o risco do vídeo de a IA marcar um horário errado sem supervisão.
- **Como aplicar (se quisermos avançar):** separar leitura de escrita — dar ao LLM uma ferramenta **só de leitura** ("`ver_horarios_livres`", consultando a agenda do vendedor via Google Calendar API, gratuita até cota generosa) para ele **sugerir** 2-3 horários reais na conversa, mas manter a **criação do evento como ação determinística**, disparada só depois que o vendedor confirmar no painel (ou, no máximo, com uma dupla confirmação lead→sistema). Isso dá a mesma conveniência do vídeo sem abrir mão do controle humano sobre a peça mais sensível (a agenda de verdade do vendedor).
- **Impacto:** integração nova (Google Calendar API, OAuth por vendedor) + 1 ferramenta de leitura em `brain.py`/`orchestrator.py`. Complexidade média — vale para uma fase 2, não para o MVP do agente conversacional.

### 1.11 Export automático para Google Sheets / CRM simples numa aba nova

- **O que é:** toda vez que o agente fala com um lead, ele grava nome/interesse/status numa planilha.
- **Por que é relevante:** no vídeo, é a única forma de a clínica ter visão dos leads, porque ela não tem CRM.
- **Classificação: Não faz sentido para nós.** Já temos banco relacional completo (`Lead`, `Conversation`, `WaMessage`, `Activity`), pipeline/kanban (`Lead.stage`) e push para CRM externo via webhook (Dynamics/HubSpot/Pipedrive, conforme roadmap). Reproduzir isso como planilha seria regressão, não ganho — o Sheets do vídeo é o substituto de CRM de quem não tem um; nós já superamos essa etapa.
- **Como aplicar:** nada — reafirma que o "funil de vendas" e o "CRM" do vídeo já existem aqui em forma melhor (banco + kanban + push CRM real).

### 1.12 Memória de curto prazo (só as últimas N mensagens)

- **O que é:** o agente lembra só as últimas 50 mensagens (vídeo) para não estourar custo/contexto.
- **Classificação: Essencial — já implementado** (12 mensagens em `brain.py`, mais conservador até que o vídeo). Nenhuma ação.

### 1.13 Agrupar mensagens picotadas (o lead manda 3-5 mensagens seguidas, o agente junta e responde uma vez)

- **O que é:** um pequeno atraso (debounce) antes de processar, para tratar rajadas de mensagens como uma única pergunta.
- **Por que é relevante:** sem isso, cada mensagem picotada gera uma resposta separada e fragmentada — comportamento visivelmente "de robô" e ruim de UX, além de gastar N chamadas de IA por 1 pensamento do lead.
- **Classificação: Recomendado, custo baixíssimo, ganho de qualidade alto.** Hoje o webhook (`services/wa/webhook.py`) não faz isso — é a lacuna mais barata de fechar desta lista.
- **Como aplicar:** ao receber mensagem, gravar e agendar o turno com um pequeno atraso (5-8s); se chegar mensagem nova da mesma conversa antes do atraso vencer, cancelar/reagendar o turno anterior e processar só uma vez com todo o lote. Como a via é serverless (Vercel, sem processo sempre-ligado), isso é melhor resolvido com um **cron curto de poucos segundos/minutos já usado no projeto** (idem ao pacing de janela 24h) do que com `sleep` dentro da função — ou seja, dá para reaproveitar o padrão de cron que já existe, não é arquitetura nova.
- **Impacto:** pequena mudança em `webhook.py`/`orchestrator.py` (campo "turno pendente" e cron leve). Não mexe em `gate.py`/`brain.py`.

### 1.14 Entender áudio e imagem (multimodal)

- **O que é:** transcrever áudio e descrever imagem antes de tratar como texto normal.
- **Por que é relevante:** no vídeo (clínica de estética, atendimento B2C), é comum o cliente mandar áudio.
- **Classificação: Opcional/Futuro.** Em prospecção B2B por SDR, a proporção de leads que respondem com áudio/foto tende a ser bem menor do que num atendimento B2C de clínica — e cada modalidade nova é mais uma fonte de custo, latência e erro (transcrição errada → intenção errada → resposta errada), contrariando o princípio "manter o LLM burro/determinismo fora dele" que já guia o projeto.
- **Como aplicar (quando fizer sentido):** medir primeiro — só justifica construir se os logs mostrarem volume real de áudio/imagem chegando. Se justificar, a Groq (que já usamos) tem Whisper-large-v3 gratuito no free tier, o que mantém custo ~R$0; descrição de imagem pode ficar para handoff humano direto (mensagem com imagem → sempre `AMBIGUO`/handoff) em vez de gastar em visão computacional.
- **Impacto:** se implementado, adiciona uma etapa de pré-processamento antes de `brain.ler`; nenhuma mudança estrutural.

### 1.15 Testar o agente com um "agente adversário" simulando clientes chatos, gerar relatório de acerto/erro

- **O que é:** rodar dezenas/centenas de conversas sintéticas contra o agente, com outro LLM fazendo o papel de lead difícil, medir taxa de erro e usar isso para refinar o prompt.
- **Por que é relevante:** dá um número objetivo de qualidade (ex.: "17 de 30 erraram" → "0 de 100 erraram") em vez de "parece que está funcionando".
- **Classificação: Recomendado.** É uma boa prática de QA e **encaixa diretamente** no que já existe: `services/wa/sandbox.py` e `routers/wa_sandbox.py` (simulador) e a suíte de 657 testes já mencionada no `PLANO_WHATSAPP_E_DYNAMICS.md`. Não é uma feature de produto, é uma ferramenta interna de desenvolvimento.
- **Como aplicar:** um script (não endpoint de produção) que gera N conversas sintéticas variando intenção esperada, roda cada uma contra `brain.ler`, compara `intencao`/`confianca` esperado vs obtido, e gera um relatório simples (markdown/CSV) com taxa de acerto por intenção. Rodar antes de qualquer mudança de prompt em `brain.py`, como um teste de regressão de qualidade (não de código).
- **Impacto:** ferramenta de desenvolvimento isolada; zero impacto em produção/arquitetura.

### 1.16 Prompt estruturado em seções (Objetivo, Ferramentas, Como agir, O que nunca fazer)

- **O que é:** organizar o prompt do agente em blocos nomeados e explícitos, incluindo uma lista do que ele nunca deve fazer.
- **Classificação: Recomendado** — puramente de manutenibilidade. O prompt atual de `brain.py` já tem, em prosa, a maior parte disso (objetivo, lista de intenções, regras de "nunca invente", instrução de confiança). Vale reorganizar em seções nomeadas / mover para um arquivo `.md` versionado à parte (como o vídeo faz com `prompt-agente.md` + `constituicao.md`), o que facilita revisão e histórico de mudanças de prompt sem tocar em código Python.
- **Como aplicar:** extrair o texto do prompt de dentro de `brain.py` para um arquivo `services/wa/prompt.md` carregado em runtime, com seções `## OBJETIVO`, `## INTENÇÕES`, `## COMO AGIR`, `## NUNCA FAZER`.
- **Impacto:** refactor pequeno, sem mudança de comportamento.

---

## 2. Análise de custos

O `PLANO_WHATSAPP_E_DYNAMICS.md` (§14) já tem uma tabela de custo completa e
correta para a arquitetura atual (WhatsApp oficial, Meta, Vercel, Supabase,
Groq/Haiku/Gemini). Aqui eu só estendo essa análise para as peças que vieram
do vídeo e ainda não estavam custeadas.

### 2.1 Opção gratuita / baixo custo (o que já temos e o que dá para manter em R$ 0)

| Item | Ferramenta | Cobre hoje | Limite / quando custa |
|---|---|---|---|
| LLM de classificação + rascunho | Groq free tier (`openai/gpt-oss-120b`) | ✅ Já em uso | 10.000 req/mês, 30 RPM — folgado para o volume atual de SDR (bem menor que os 500-2000/dia estimados para a clínica no vídeo) |
| WhatsApp — receber/responder (24h) | Cloud API oficial (Meta) | ✅ Já em uso | Grátis desde nov/2024; não muda com o volume de SDR |
| WhatsApp — WABA/número | Meta | ✅ Já em uso | Grátis |
| Debounce de mensagens picotadas (§1.13) | Código próprio + cron já existente | A construir | R$ 0 — é lógica, não infraestrutura nova |
| Base de conhecimento institucional (§1.9) | Arquivo `.md` versionado | A construir | R$ 0 — é conteúdo, não serviço |
| Múltiplos templates (§1.7) | Tabela nova no Postgres já existente (Supabase) | A construir | R$ 0 de infra; o **custo real é por mensagem enviada como template** (ver 2.2), não pela feature em si |
| Teste adversário / QA automatizado (§1.15) | Script usando o mesmo Groq | A construir | Poucas chamadas extras de LLM, só quando rodado manualmente antes de deploy — irrelevante no orçamento |
| Áudio (se decidirmos fazer) | Groq Whisper-large-v3 (free tier) | Opcional | Mesma cota do Groq acima — mas cada áudio consome parte da cota compartilhada com a classificação de texto |
| Agenda — leitura de horários livres (§1.10) | Google Calendar API | Opcional | Gratuita até 1.000.000 requisições/dia — não é fator de custo real neste volume |

**O que não dá para fazer de graça:** abrir conversa fria (1º template) —
é cobrado por mensagem pela Meta (~R$ 0,30–0,45/msg, conforme já registrado no
plano), independente de quantos templates diferentes o painel gerenciar.
Isso já era verdade antes do vídeo e continua sendo o único custo variável
real do canal.

### 2.2 Opção paga (quando e por quê)

| Alternativa | O que resolve | Vantagem | Desvantagem | Quando vale a pena |
|---|---|---|---|---|
| **Upgrade de LLM (Claude Haiku / Sonnet, já usado em `ai_insights.py`)** | Qualidade de classificação/redação em casos ambíguos | Melhor PT-BR, mais consistente em nuance | Custo por chamada (ainda assim, fração de centavo por mensagem curta) | Se o Groq começar a gerar falsos "AMBÍGUO" em excesso (medir antes de trocar) |
| **Google Calendar API com conta Workspace paga por vendedor** | Agenda real por vendedor, sem depender de conta pessoal gratuita | Integração corporativa, controle de permissão | Custo de licença M365/Workspace (já existente na empresa, não é custo novo do agente) | Só se decidirmos avançar para leitura de agenda (§1.10) em escala com vários vendedores |
| **BSP (Business Solution Provider) da Meta — Twilio, Zenvia, Take Blip, 360dialog** | Onboarding mais simples de WABA, dashboards prontos, suporte | Menos fricção inicial, suporte comercial | Taxa mensal + markup por mensagem em cima do preço da Meta | Só se o time achar o onboarding direto pela Meta (Business Manager) complicado demais — tecnicamente não é necessário, é conveniência |
| **Supabase Pro / Vercel Pro** | Banco e hospedagem para uso comercial real (Hobby não é comercial) | Sem pausa por inatividade, crons sem limite | ~US$ 20-25/mês cada | Assim que o agente sair de teste e for usado com leads reais — já previsto no plano existente, não é algo novo do vídeo |
| **Ferramenta de gestão de templates dedicada (ex.: painel de BSPs pagos)** | Interface pronta de criação/aprovação de templates | Evita construir UI de templates do zero | Custo mensal + vendor lock-in | Só se o volume de templates crescer muito (dezenas); para 2-4 templates (abertura, lembrete, reengajamento, promoção), vale mais construir o registro simples do §1.7 |

**Resumo:** nenhum item que veio do vídeo (debounce, constituição leve, múltiplos
templates, QA adversário) exige gasto novo de infraestrutura. O único custo
variável real do sistema continua sendo a abertura de conversa fria via
template pago da Meta — que já estava mapeado antes desta análise.

---

## 3. Conclusão

### A. O que devemos aproveitar do vídeo

1. **Debounce/agrupamento de mensagens picotadas** (§1.13) — lacuna real, barata, ganho de UX imediato.
2. **Base de conhecimento institucional enxuta** para perguntas informativas simples, sem tocar na regra de "preço/condição vai para humano" (§1.9).
3. **Registro simples de múltiplos templates** geridos pelo painel, para reengajamento após a janela de 24h fechar (§1.7).
4. **Ferramenta de leitura de agenda** (não de escrita) para o LLM sugerir horários reais na conversa, mantendo a criação do compromisso como ação humana/determinística (§1.10, versão restrita).
5. **Script de teste adversário** (conversas sintéticas + relatório de acerto), reaproveitando o simulador que já existe (§1.15).
6. **Reorganizar o prompt em seções nomeadas**, movendo para um arquivo versionado à parte (§1.16) — manutenção, não funcionalidade.

### B. O que devemos ignorar por enquanto

1. **Agente "tool-calling" livre** (LLM escolhendo ferramentas sozinho) — contraria decisão já tomada e documentada de manter toda regra crítica determinística (§1.2).
2. **Export para Google Sheets** — já temos banco relacional + kanban + push CRM, seria regressão (§1.11).
3. **Multimodal (áudio/imagem)** — custo/complexidade não justificados sem dado real de volume em contexto B2B (§1.14).
4. **Criação automática de evento de calendário sem confirmação humana** — o vídeo faz isso, mas contradiz a regra de ouro do próprio projeto (§1.10, versão completa).
5. **BSP pago / gestor de templates de terceiros** — onboarding direto pela Meta já é suficiente no volume atual (§2.2).

### C. O que considerar para uma versão futura

1. Ferramenta de escrita no calendário (criação do evento pela própria IA) — só depois que a leitura (§1.10) estiver validada em produção e o volume justificar automatizar também a escrita.
2. Multimodal, revisitado com dados reais de quantos leads mandam áudio/imagem.
3. Upgrade de modelo (Groq → Claude Haiku/Sonnet) se a taxa de "AMBÍGUO" do Groq se mostrar alta demais na prática.
4. Gestor de templates mais robusto (populando variáveis automaticamente a partir da conversa, como no vídeo) — a v1 do §1.7 pode ficar manual.

### D. Recomendação de arquitetura/implementação

Não mudar a arquitetura — ela já está correta e mais rigorosa que a do vídeo
nos pontos que importam (portão determinístico, dois eixos de estado, LGPD,
auditoria). O trabalho real está em **fechar lacunas pontuais dentro do
desenho existente**, nesta ordem sugerida (menor esforço → maior valor primeiro):

1. Debounce de mensagens picotadas em `webhook.py`/`orchestrator.py` (§1.13).
2. Bloco de "sobre a empresa" no prompt de `brain.py` (§1.9).
3. Script de QA adversário sobre o `sandbox.py` existente (§1.15), rodado antes
   de qualquer mudança de prompt — vira o critério objetivo para saber se uma
   mudança no agente melhorou ou piorou.
4. Registro de múltiplos templates em `routers/wa.py` (§1.7).
5. Confirmar/terminar a UI da aba "Conversas" no painel, se ainda não estiver
   pronta (§1.6) — sem isso, o backend de pausar/assumir não tem uso prático.
6. Só depois disso, avaliar a ferramenta de leitura de agenda (§1.10) como
   iniciativa de fase 2, com o Google Calendar API (gratuito) e sem dar à IA
   permissão de escrita na agenda de ninguém.

Nenhum destes itens exige nova infraestrutura paga, nova dependência de
servidor sempre-ligado, ou abrir mão de qualquer regra de segurança já
decidida em `PLANO_WHATSAPP_E_DYNAMICS.md`.
