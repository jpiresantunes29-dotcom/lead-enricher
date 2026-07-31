# Plano de Redesign — Identidade Corporativa

> Criado em 2026-07-05. Objetivo: tirar o produto do registro "startup/hacker
> genérico" e posicioná-lo como ferramenta B2B séria, vendável para gerente
> comercial e diretor de vendas — sem jogar fora o CSS existente.

## Diagnóstico do visual atual

O tema "Gold Signal" (dark-only, dourado `#F5B700`, Space Grotesk, confete ao
enriquecer, "link mágico", partículas na landing) comunica *ferramenta indie
para growth hacker*. O comprador corporativo — que aprova os R$ 97+/mês —
espera algo entre HubSpot, Pipedrive e RD Station: claro, denso em informação,
sóbrio. Problemas específicos:

1. **Dark-only.** SaaS B2B brasileiro é vendido em projetor de sala de reunião
   e em notebook corporativo com brilho baixo — dark mode como única opção
   parece "developer tool". (styles.css é dark-only por decisão de jul/2026;
   a proposta abaixo mantém o dark como opção, não como padrão.)
2. **Dourado como cor primária** lê-se "casa de apostas/cripto" no contexto BR,
   não "confiança".
3. **Microcopy informal**: "link mágico", "Bem-vindo de volta", confete,
   emojis em feedbacks. Simpático para PLG, ruim para screenshot em proposta
   comercial.
4. **Navegação por botões no topo** em vez de sidebar — padrão de app pessoal,
   não de ferramenta de trabalho com 6+ áreas.
5. **Zero sinais de confiança**: sem footer institucional, termos, política de
   privacidade, CNPJ, página de segurança — itens que o compras/jurídico do
   cliente procura antes de aprovar.

---

## Etapa 1 — Sistema visual (1–2 dias)

**Paleta** (substituir em `static/css/styles.css` — as cores vivem em
`:root`, a troca é concentrada):

| Papel | Hoje | Proposta |
|---|---|---|
| Primária/ação | Dourado `#F5B700` | Azul-profundo `#1D4ED8` (confiança, padrão B2B) |
| Fundo app | Preto `#0A0A0B` (aprox.) | Modo claro padrão: `#F8FAFC` fundo, `#FFFFFF` painéis |
| Texto | Branco/cinza | `#0F172A` títulos, `#475569` corpo |
| Sucesso/alerta | verdes/vermelhos atuais | manter, saturação -15 % |
| Dark mode | único | vira `prefers-color-scheme` / toggle (reaproveita o CSS atual) |

**Tipografia**: manter JetBrains Mono só para dados técnicos (domínios, MX,
IPs — é um diferencial visual legítimo). Trocar Space Grotesk por **Inter**
em UI/títulos: neutra, corporativa, ótima em tamanhos pequenos de tabela.

**Densidade**: reduzir border-radius (16px → 8–10px), sombras mais curtas,
espaçamentos menores nas tabelas. Corporativo = mais informação por tela.

## Etapa 2 — Layout do app (2–3 dias)

1. **Sidebar fixa à esquerda** (ícone + label): Buscar, Dashboard, Pipeline,
   Follow-ups, Histórico, Configurações. Colapsável em ícones. As views por
   hash já existem em `app.js` — muda só o container de navegação em
   `templates/index.html` + CSS.
2. **Histórico vira tabela** (colunas: empresa, domínio, score, estágio,
   funcionários, data, ações) com ordenação por coluna — hoje são cards de
   linha. Tabela é o idioma do comprador corporativo.
3. **Header do lead com ações agrupadas**: Exportar / Enviar ao CRM / Resumo IA
   em uma barra de ações consistente, em vez de espalhadas pelo card.
4. **Remover o confete** (`celebrate()` em app.js) ou trocar por um check
   sutil animado no botão. Confete em demo para diretor = risco.

## Etapa 3 — Tom de voz e microcopy (meio dia)

Passar `templates/*.html` e strings do `app.js`:

| Hoje | Proposta |
|---|---|
| "Entrar com link mágico" | "Receber link de acesso por e-mail" |
| "Bem-vindo de volta." | "Acesse sua conta" |
| "Experimente: nubank stripe…" | manter, mas com empresas B2B BR (totvs, embraer, gerdau) |
| "Você tem X buscas neste ciclo" | "X de Y análises disponíveis · renova em DD/MM" |
| "Inteligência comercial em segundos" | manter — é bom |

Regra geral: sem exclamações, sem diminutivos, verbos no infinitivo nos botões.

## Etapa 4 — Sinais de confiança (1–2 dias, alto impacto/venda)

1. **Footer institucional** (landing e app): razão social/CNPJ, Termos de Uso,
   Política de Privacidade, contato, LinkedIn da empresa.
2. **Página /privacidade e /termos** — templates estáticos simples; sem eles
   nenhum jurídico aprova a compra (e a LGPD exige a política).
3. **Página /seguranca**: onde os dados ficam, retenção de 90 dias
   (roadmap 0.4), HMAC no webhook, HTTPS. Uma página vende mais que 10 features.
4. **Favicon + OG image** profissionais (hoje não há favicon — aparece o globo
   padrão do navegador na aba).
5. **E-mail transacional com domínio próprio** (Supabase auth manda de
   `noreply@mail.app.supabase.io` — trocar SMTP para domínio leadenricher).

## Etapa 5 — Landing (2–3 dias, depois das etapas 1–4)

Manter a estrutura (é bem construída), mas recalibrar:
1. Hero: navy sólido, com a interface real do produto (modo claro) em moldura
   de browser, em vez de efeitos abstratos.
2. Seção "para quem": 3 personas (SDR, gerente comercial, agência) com o fluxo
   de cada uma — a landing falava só de tecnologia (DNS, MX), que impressiona
   dev e não o comprador.
3. Pricing com coluna Enterprise ("fale com vendas") + FAQ (cota, LGPD,
   cancelamento, nota fiscal).
4. Prova social assim que existir: logos de clientes, número de buscas feitas,
   depoimento. Até lá, badge "dados públicos · conformidade LGPD".

---

## Ordem de execução recomendada

| # | Entrega | Esforço | Status |
|---|---|---|---|
| 1 | Etapa 3 (microcopy) + favicon | 0,5 dia | ✅ feito em 2026-07-05 |
| 2 | Etapa 1 (paleta clara azul + Inter) | 1–2 dias | ✅ feito em 2026-07-05 (confete removido junto) |
| 3 | Etapa 4 (termos, privacidade, segurança, footer) | 1–2 dias | ✅ feito em 2026-07-05 (revisar textos com jurídico) |
| 4 | Etapa 2 (sidebar + tabela no histórico) | 2–3 dias | ✅ feito em 2026-07-05 |
| 5 | Etapa 5 (landing recalibrada) | 2–3 dias | ✅ feito em 2026-07-05 |

### O que a Etapa 2 entregou
- **Sidebar fixa** (`.app-sidebar` em index.html + styles.css) com as 6 áreas,
  incluindo Configurações. Colapsa para ícones entre 721–1100px e vira barra
  superior rolável abaixo de 720px. O header antigo deixou de existir.
- **Histórico virou tabela** (`.lead-tbl`) com colunas Empresa, Domínio, Score,
  Estágio, Funcionários, Data e Ações, **ordenável por coluna**
  (`sortHistory()` no app.js; clique alterna asc/desc).
- **Barra de ações do lead** (`.lead-actions`) logo abaixo do cabeçalho do card:
  CSV, Excel, Resumo IA e Enviar ao CRM (os dois últimos só quando configurados).
  Substituiu a `.integr-row` que ficava perdida no meio do card.

### O que a Etapa 5 entregou
- **Paleta da landing** migrada de dourado/preto quente para **navy + azul
  institucional** (mesmos tons do app). Tokens renomeados `--gold*` → `--brand*`.
  O âmbar sobrou apenas como cor semântica de "prioridade alta".
- **Hero**: o terminal animado e a rede de partículas saíram; entrou uma
  **prévia do produto em moldura de browser** — a interface real (sidebar +
  ficha de empresa + decisores) desenhada em markup, no tema claro. O campo de
  domínio continua real e leva para `/app?domain=…`.
- **Seção "Para quem é"** com as 3 personas (SDR, gerente comercial, agências),
  cada uma com a dor declarada e o que resolve.
- **FAQ** ganhou cota/renovação, cancelamento sem fidelidade e nota fiscal.
- **Removidos**: boot sequence ("resolving… 200 OK"), cursor custom
  (`cursor:none` incomoda usuário corporativo), `fx/terminal.js`,
  `fx/network.js` e `fx/cursor.js`. Ficaram os reveals, o spine e o journey.
- Badges de confiança no hero: "Dados públicos · conformidade LGPD" e
  "Exportação e webhook para CRM".

Pendências pontuais: e-mail transacional com domínio próprio (SMTP do Supabase),
OG image em PNG (hoje só há favicon SVG) e prova social real quando existir.
