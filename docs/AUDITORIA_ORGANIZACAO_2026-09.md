# Auditoria de organização e limpeza — setembro/2026

> Auditoria de arquivos (não de código). Objetivo: identificar documentação
> obsoleta, duplicada ou desatualizada, e propor uma estrutura final simples.
> Nenhum arquivo foi movido, apagado ou editado nesta passada — este documento
> é o diagnóstico; a execução é um passo separado, sob confirmação.

---

## 1. Diagnóstico geral

O projeto real é `lead_enricher/` com `.git` — mas o Claude Code está sendo
aberto a partir de uma pasta **externa** com o mesmo nome
(`Downloads/lead_enricher/`), que por sua vez contém a pasta do projeto
(`Downloads/lead_enricher/lead_enricher/`). Ou seja, há dois níveis de pasta
chamados `lead_enricher`, e só o interno é versionado. Isso não é um problema
de "arquivo a limpar" — é a causa raiz de dois problemas reais:

1. **Dois arquivos importantes vivem fora do controle de versão.**
   `DOCUMENTACAO_IA_COMPLETA.md` e `PROMPT_ANALISE_PROJETO.md` estão na pasta
   externa, nunca foram commitados, não aparecem no índice `docs/README.md` e
   não sobrevivem a um `git clone` ou a uma reinstalação da máquina.
2. **Existe uma segunda cópia de `.claude/`** (`launch.json` e
   `settings.local.json`) na pasta externa, divergente da cópia real dentro do
   projeto — inclusive com um comando de start diferente (`os.chdir` extra).

Fora esse ponto estrutural, a pasta `docs/` do projeto está **bem cuidada**:
já existe um índice (`docs/README.md`) que classifica cada documento como
"Vigente" ou "Histórico (implementado)", e o próprio índice explica por que os
históricos não são movidos — comentários no código (`main.py`,
`services/activity_rules.py`, `services/ai_insights.py`,
`static/landing/landing.js`) citam esses arquivos **pelo caminho exato**.
Mover um doc histórico para uma subpasta "arquivo" quebraria essa referência
sem gerar nenhum ganho real — por isso esta auditoria **não recomenda** criar
`docs/historico/` ou similar.

O problema real não é falta de estrutura — é que **três documentos sobre o
agente de WhatsApp cresceram em paralelo** e um deles (o mais novo, mais
completo e mais lido) tem um erro factual e uma seção inteira desatualizada:

| Documento | Papel real | Está no índice? |
|---|---|---|
| `docs/PLANO_WHATSAPP_E_DYNAMICS.md` (989 linhas) | Log de decisões, fase a fase, de por que o agente foi construído do jeito que foi | ❌ Não |
| `DOCUMENTACAO_IA_COMPLETA.md` (fora do repo, 1084 linhas) | Manual de referência/operação do agente (setup, troubleshooting, exemplos) | ❌ Não (nem está no repo) |
| `docs/ANALISE_VIDEO_AGENTE_WHATSAPP.md` (267 linhas, não commitado) | Memorando comparando o agente contra ideias de um vídeo externo | ❌ Não |

Nenhum dos três está listado em `docs/README.md`, então quem abrir o índice
hoje não sabe que eles existem.

**O código já está mais atualizado que a documentação.** Dois commits do
próprio dia 2026-09-05 mudaram o comportamento do agente depois que a
documentação mais recente foi escrita:

- `9cbc836` (21:07) — trocou o motor de IA de Claude/Anthropic para **Groq**.
- `98331e1` (21:29) — **removeu completamente** as restrições de horário
  (silêncio noturno, horário comercial, fim de semana); `gate.py` hoje sempre
  libera envio a qualquer hora (`service_window()` retorna `(True, False)`
  incondicionalmente).

`DOCUMENTACAO_IA_COMPLETA.md` já reflete a troca para Groq, mas foi escrita
**antes** do segundo commit (mesma noite) e descreve em detalhe um sistema de
horários que não existe mais — inclusive num diagrama de fluxo completo, numa
tabela de variáveis de ambiente e num item de troubleshooting. `PLANO_WHATSAPP_E_DYNAMICS.md`
também descreve esse sistema de horários como decisão vigente e ainda recomenda
Claude Haiku como modelo de IA "atual". Nenhum dos dois está "errado por
completo" — a arquitetura de portão, estados e handoff que ambos descrevem
continua exatamente como está no código. É um recorte específico e localizado
que ficou para trás.

Também foi encontrado um **erro factual** (não é questão de estar
desatualizado, é uma confusão de conceitos): `DOCUMENTACAO_IA_COMPLETA.md` diz
em 5 lugares que a janela de conversa gratuita da Meta é de **72 horas**. O
código (`services/wa/states.py:31`, `WINDOW_HOURS = 24`, e o próprio
`gate.py:18`) confirma que é **24 horas** — as 72 horas existem, mas são outra
coisa: `WA_TEMPLATE_RETRY_HOURS`, o tempo de espera antes de reenviar um
template a quem não respondeu ao primeiro contato.

Por fim, há um item de higiene fora do escopo de documentação, mas que apareceu
na varredura de arquivos: `.claude/settings.local.json` (dentro do projeto,
não versionado) tem, no meio da lista de comandos permitidos, uma
**connection string completa do Postgres com senha em texto puro**
(`DATABASE_URL=postgresql://postgres.sgfbplozrpjnsudoawpz:...`). O arquivo não
está no Git e nunca foi commitado — mas é um arquivo local com uma senha de
produção em claro. Vale considerar trocar essa senha e evitar colar
`DATABASE_URL` completa em comandos que ficam salvos no histórico de
permissões.

---

## 2. Arquivos para remover

| Arquivo | Por quê |
|---|---|
| `Downloads/lead_enricher/PROMPT_ANALISE_PROJETO.md` (pasta externa, fora do Git) | É o prompt que encomendou a auditoria de agosto/2026. O entregável dele já existe e está commitado (`docs/AUDITORIA_2026-08.md`). Não descreve o projeto — descreve uma tarefa já concluída. Se quiser reusar esse roteiro de auditoria no futuro, o texto pode ser preservado fora do projeto (ex.: num gist pessoal) em vez de ocupar a raiz do diretório de trabalho. |
| `lead_enricher/.env.env.bak` | Nome sugere um backup acidental (dupla extensão `.env.env.bak`), gerado por alguma cópia manual do `.env`. Já existem `.env`, `.env.local` e `.env.producao` como as fontes reais. **Confirme o conteúdo antes de apagar** — não foi lido aqui por poder conter segredo antigo; se for só uma cópia velha do `.env` atual, remover. |
| Pasta externa `Downloads/lead_enricher/.claude/` (a de fora, não a do projeto) | Duplica `launch.json`/`settings.local.json` do projeto real, com um comando de start diferente (assume `cwd` = pasta externa). Só existe porque o Claude Code foi aberto um nível acima do projeto. Ver §6 sobre o que fazer com a pasta externa como um todo. |

Nada dentro de `docs/` entrou nesta lista — não há documento cujo conteúdo
tenha perdido todo o valor.

---

## 3. Arquivos para atualizar

| Arquivo | O que está errado | O que precisa mudar |
|---|---|---|
| `DOCUMENTACAO_IA_COMPLETA.md` | (a) Descreve silêncio noturno/horário comercial/fim de semana como regra ativa do portão — foi removido em `98331e1`. Afeta o diagrama de arquitetura, a tabela "Horários e Silêncio", o passo 4 do fluxo completo, a tabela de estados e o troubleshooting "WhatsApp não autoriza IA a responder". (b) Diz "janela de 72h" em 5 lugares onde o código usa 24h (`WINDOW_HOURS = 24` em `services/wa/states.py`) — confundiu com `WA_TEMPLATE_RETRY_HOURS` (esse sim é 72h, mas é o prazo de reenvio de template, não a janela de conversa). | Remover/ajustar a seção de horários (ou registrar explicitamente "restrição removida em 2026-09-05, IA responde 24/7") e corrigir 24h↔72h nos 5 pontos. Depois de corrigido, mover o arquivo para dentro de `docs/` (ver §4) e commitar. |
| `docs/PLANO_WHATSAPP_E_DYNAMICS.md` | §7 recomenda Claude Haiku como "modelo atual"; §17 marca "Modelo definido: Haiku" — hoje é Groq (`openai/gpt-oss-120b`, migração em `9cbc836`). Fase 3 descreve o silêncio noturno/horário comercial como decisão de arquitetura vigente — foi revertido depois. | Não precisa reescrever as ~1000 linhas — é um documento de decisões históricas e a maioria continua válida. Bastam duas notas curtas (ex.: um bloco "Atualização 2026-09-05" no topo ou ao lado de §7 e da Fase 3) registrando as duas mudanças e apontando para o commit/PR que as fez. É o mesmo padrão que o documento já usa em outros pontos ("decisões que fogem do texto original"). |
| `docs/README.md` | Índice não lista `PLANO_WHATSAPP_E_DYNAMICS.md`, `PRODUCAO.md` (só citado em prosa) nem `ANALISE_VIDEO_AGENTE_WHATSAPP.md`. Quem abre o índice hoje não sabe que esses três existem. | Adicionar as três linhas na tabela, com estado "Vigente" para os dois primeiros. Ver proposta de texto em §4. |

---

## 4. Arquivos para consolidar

**`docs/ANALISE_VIDEO_AGENTE_WHATSAPP.md`** é um memorando de decisão: avalia
~16 ideias de um vídeo externo e conclui com uma lista curta do que vale
aproveitar (§3.A, 6 itens: debounce de mensagens picotadas, base de
conhecimento institucional leve, registro de múltiplos templates, ferramenta
de leitura de agenda, script de QA adversário, prompt em seções nomeadas).
Essa lista é, na prática, backlog — e backlog já tem endereço certo no
projeto: `docs/ROADMAP_FUNCIONALIDADES.md`.

Recomendação: copiar os 6 itens do §3.A para `ROADMAP_FUNCIONALIDADES.md`
(provavelmente como uma sub-seção dentro da Fase 1, já que são todos de baixo
custo/alto retorno), cada um com uma linha de esforço, do jeito que o roadmap
já formata os outros itens. Depois disso, `ANALISE_VIDEO_AGENTE_WHATSAPP.md`
continua existindo — não precisa ser apagado — mas sua função vira a de
**registro histórico do porquê** (por que "IA escolhe ferramentas livremente"
foi descartado, por que multimodal ficou pra depois, etc.), o mesmo papel que
`PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md` já cumpre para decisões mais antigas.
Vale marcá-lo "Histórico (decisões arquivadas)" no índice depois que o roadmap
absorver os itens acionáveis.

Não há duplicação a resolver entre `PLANO_WHATSAPP_E_DYNAMICS.md` e
`DOCUMENTACAO_IA_COMPLETA.md` apesar da sobreposição de assunto — o primeiro é
o **porquê** (decisão, fase, data, trade-off) e o segundo é o **como usar
agora** (setup, variáveis, troubleshooting). São públicos diferentes (quem
decide vs. quem opera) e a sobreposição de conteúdo é só a descrição da
arquitetura, que precisa aparecer nos dois para cada um ser lido sozinho.
Forçar consolidação aqui reduziria a utilidade de ambos.

---

## 5. Arquivos para arquivar

Nenhum. Os três documentos que o próprio projeto já rotula como "Histórico
(implementado)" — `PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md`,
`PLANO_REDESIGN_CORPORATIVO.md`, `DESIGN_LANDING_V3.md` — **devem permanecer
onde estão**, em `docs/`, sem subpasta separada. Comentários no código citam
o caminho exato de cada um:

```
main.py:272                    → docs/DESIGN_LANDING_V3.md
services/activity_rules.py:3   → docs/PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md §5.2
services/ai_insights.py:3      → docs/PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md §9
static/landing/landing.js:3    → docs/DESIGN_LANDING_V3.md §7-§8
```

Movê-los quebraria essas referências sem nenhum ganho — a etiqueta "Histórico"
no índice já cumpre o papel de "não confundir com o estado atual" que a
palavra "arquivar" busca. Isso já está certo do jeito que está.

`PROMPT_ANALISE_PROJETO.md` poderia ir para cá em vez de ser removido, se
houver interesse em reusar o roteiro de auditoria periodicamente — mas como
está fora do Git hoje, "arquivar" precisaria primeiro trazê-lo para dentro do
projeto. Ver alternativa em §2.

---

## 6. Estrutura recomendada

Sem criar pastas novas dentro de `docs/` — a estrutura atual já é enxuta.
As únicas mudanças são: trazer `DOCUMENTACAO_IA_COMPLETA.md` para dentro do
projeto e decidir o destino da pasta externa.

```
lead_enricher/                        ← pasta de trabalho (fora do Git)
└── lead_enricher/                    ← o projeto de verdade (Git aqui)
    ├── docs/
    │   ├── README.md                 ← índice (atualizar: 3 linhas novas)
    │   ├── ROADMAP_FUNCIONALIDADES.md
    │   ├── AUDITORIA_2026-08.md
    │   ├── AUDITORIA_ORGANIZACAO_2026-09.md   ← este documento
    │   ├── CONTACT_INTELLIGENCE.md
    │   ├── MIGRACOES.md
    │   ├── FILA_E_LOTE.md
    │   ├── PRODUCAO.md
    │   ├── PLANO_WHATSAPP_E_DYNAMICS.md       ← com nota de atualização
    │   ├── DOCUMENTACAO_IA_COMPLETA.md        ← trazido de fora + corrigido
    │   ├── ANALISE_VIDEO_AGENTE_WHATSAPP.md
    │   ├── PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md   (histórico, no lugar)
    │   ├── PLANO_REDESIGN_CORPORATIVO.md           (histórico, no lugar)
    │   └── DESIGN_LANDING_V3.md                    (histórico, no lugar)
    └── ... (código, sem mudanças)
```

Sobre a pasta externa `Downloads/lead_enricher/` (o nível que **não** tem
`.git`): depois de mover `DOCUMENTACAO_IA_COMPLETA.md` para dentro do projeto
e decidir o destino de `PROMPT_ANALISE_PROJETO.md`, ela fica vazia de
conteúdo próprio (só resta a subpasta do projeto e o `.claude` duplicado).
A forma mais simples de nunca mais ter esse problema é **abrir o Claude Code
diretamente em `Downloads/lead_enricher/lead_enricher/`** dali em diante, em
vez do nível de cima — não é um arquivo para apagar, é um hábito de onde
abrir a pasta.

---

## 7. Estado final desejado

Depois da limpeza:

- **Todo arquivo de documentação vive dentro do projeto versionado** — nada
  importante fica numa pasta que o `git clone` não traz de volta.
- **`docs/README.md` lista os 14 documentos reais**, cada um com uma frase do
  que trata e o estado (Vigente/Histórico) — quem abrir o projeto sabe em
  30 segundos quais 2-3 arquivos ler primeiro (o próprio índice já diz:
  `CONTACT_INTELLIGENCE.md` para o produto, `ROADMAP_FUNCIONALIDADES.md` para
  o que vem a seguir, `AUDITORIA_2026-08.md` para decisões de segurança —
  bastaria adicionar `PLANO_WHATSAPP_E_DYNAMICS.md` como a quarta porta de
  entrada, para quem for mexer no agente).
- **Nenhum documento afirma um comportamento que o código não tem mais.** As
  duas seções desatualizadas (horários do WhatsApp, modelo de IA) e o erro de
  24h/72h estão corrigidos ou explicitamente marcados como superados.
- **O backlog vive num lugar só** (`ROADMAP_FUNCIONALIDADES.md`) — a lista de
  "o que aproveitar do vídeo" não fica perdida dentro de um memorando de
  análise que ninguém vai reabrir depois de decidido.
- **Documentos históricos continuam onde os comentários do código apontam**
  — arquivar não significa mover, significa rotular, e isso já funciona.

O que este documento **não** cobriu, de propósito, por estar fora do escopo
pedido (organização de arquivos, não do código): o repositório tem mudanças
não commitadas em 13 arquivos e 3 arquivos novos não versionados
(`routers/wa_connection.py`, `services/wa/credenciais.py`,
`alembic/versions/0009_whatsapp_por_usuario.py` — aparenta ser uma feature de
"WhatsApp por usuário" em andamento). Isso não é lixo documental, é trabalho
em progresso; só fica registrado aqui para não ser confundido com um dos
itens acima.
