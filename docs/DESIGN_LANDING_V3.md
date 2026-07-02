# LeadEnricher — Conceito de Landing Page v3: "GOLD SIGNAL"

> Direção criativa, UX, motion design e arquitetura front-end para a nova landing.
> Identidade: amarelo / dourado / preto. Benchmark: Stripe, Linear, Vercel, Framer, Cloudflare.
> Data: 2026-06-12 · Complementa `PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md`
>
> **STATUS (2026-06-12): preparação concluída.** Rotas divididas (`/app` = produto canônico;
> `/landing` = preview de dev da nova landing, noindex; `/` segue com a página híbrida até o
> fim da L1). Esqueleto criado em `templates/landing.html` + `static/landing/` (tokens,
> orquestrador, fx/terminal funcional, fx/network básico, fx/spine stub). Vendor self-hosted:
> GSAP 3.13.0 + ScrollTrigger + Lenis 1.3.4. Redirects de auth/billing já apontam para `/app`.
> ⚠️ Pendência externa: adicionar `…/app` à allowlist de Redirect URLs no Supabase.

---

## 1. Conceito criativo — o manifesto

**"Todo domínio esconde uma empresa inteira. Nós acendemos as luzes."**

A metáfora central da página é **eletricidade atravessando infraestrutura no escuro**.
O preto não é "fundo escuro de SaaS" — é a internet apagada, cabos e servidores adormecidos.
O dourado é o **sinal**: o dado vivo que percorre essa infraestrutura quando o LeadEnricher
toca um domínio. A página inteira dramatiza o que o produto faz: *um domínio entra,
inteligência acesa sai*.

Três princípios inegociáveis (o que separa Linear/Stripe de template de Dribbble):

1. **O ouro é escasso.** 90% da tela é preto-carvão e cinza-quente. O dourado aparece
   apenas onde há *dado em movimento* ou *ação*. Se tudo brilha, nada brilha.
2. **Toda animação conta a história do produto.** Nenhuma partícula decorativa: cada pulso,
   cabo e brilho representa DNS sendo resolvido, decisor sendo encontrado, score sendo
   calculado. Motion = demonstração, não enfeite.
3. **A página é o demo.** O visitante não lê sobre o produto — ele o vê funcionar no
   scroll. (Inspiração: a homepage da Stripe que processa um pagamento de mentira; a da
   Vercel que faz deploy de mentira.)

---

## 2. Identidade visual

### 2.1 Paleta — "Carvão & Ouro"

```css
:root{
  /* Pretos quentes (nunca #000 puro — preto frio mata o dourado) */
  --void:      #060503;   /* fundo absoluto da página */
  --carbon:    #0D0B07;   /* fundo de seção alternada */
  --surface:   #15110A;   /* cards, painéis */
  --surface-2: #1D1810;   /* hover de card, inputs */
  --line:      rgba(245,183,0,.10);  /* bordas hairline douradas */
  --line-2:    rgba(245,240,225,.07);/* bordas neutras */

  /* Ouro — escala de energia */
  --gold:        #F5B700; /* primário — ação, links, dados ativos */
  --gold-bright: #FFD84D; /* hot spot de pulsos, hover, foco */
  --gold-white:  #FFF3C4; /* núcleo "white-hot" de partículas */
  --gold-deep:   #8A6400; /* dourado apagado — trilhas inativas */
  --ember:       #FF9E00; /* âmbar — alertas de prioridade alta, gradientes */

  /* Texto */
  --text:   #F5F2E9;      /* branco-quente (papel à luz de vela) */
  --text-2: #A89F8D;      /* secundário */
  --text-3: #6B6353;      /* terciário, labels mono */

  /* Semânticos (uso pontual) */
  --ok: #4ADE80;  --warn: var(--ember);  --err: #F87171;
}
```

**Gradientes de assinatura:**
- `--beam`: `linear-gradient(90deg, transparent, #F5B700, #FFD84D, #F5B700, transparent)` — o "feixe" que percorre bordas e cabos.
- `--heat`: `radial-gradient(closest-side, #FFF3C4, #F5B700 35%, rgba(245,183,0,0) 70%)` — núcleo de partícula/pulso.
- Texto-herói: `linear-gradient(110deg, #F5F2E9 60%, #F5B700 75%, #FFD84D 80%, #F5F2E9 95%)` com `background-position` animado — um brilho varre a palavra-chave a cada ~6s (efeito "shimmer" da Apple).

### 2.2 Tipografia

| Papel | Fonte | Uso |
|---|---|---|
| Display | **Space Grotesk** 500–700 (já em uso — manter) | H1 clamp(44px→88px), tracking −0.04em, line-height 0.98 |
| Corpo | **Inter** 400–500 | 16–18px, `--text-2` |
| Dados | **JetBrains Mono** 400–500 | registros DNS, scores, métricas, labels de seção `[01 — RESOLVE]` |

A mono é a alma da estética infra: tudo que é *dado* (MX, SPF, score, contadores) aparece
em mono dourado sobre painel escuro, como um terminal vivo. Labels de seção numerados em
mono (`01 / RESOLVE`, `02 / DISCOVER`…) criam ritmo editorial à la Linear.

### 2.3 Texturas e profundidade

- **Grid de circuito**: o atual dot-grid do hero evolui para um *grid de PCB* — linhas
  hairline `--line-2` a 45° em áreas específicas + vias (pontos) nos cruzamentos. Em CSS
  (`background-image` em camadas), custo zero.
- **Film grain**: overlay de ruído (PNG 128px tile, `opacity:.03`, `mix-blend-mode:overlay`)
  na página toda — mata o "flat de template" e dá textura cinematográfica.
- **Vinheta**: `radial-gradient` escurecendo cantos do viewport — foco central, clima de cinema.
- **Profundidade**: 3 planos de parallax em toda a página — fundo (grid/partículas, 0.2×),
  meio (conteúdo, 1×), frente (glows e fragmentos de UI flutuantes, 1.15×).

### 2.4 Iconografia

Linha fina 1.5px, cantos levemente arredondados, sempre monocromática (`--text-2`,
dourada apenas em hover/ativo). Ícones técnicos custom para o domínio do produto:
registro MX (envelope+nó), SPF (escudo+raio), decisor (nó com coroa de conexões),
score (hexágono com núcleo). Nada de emoji na landing.

---

## 3. O motivo de assinatura: **a Espinha de Dados (Data Spine)**

O elemento que ninguém mais tem: uma **linha de circuito SVG contínua que percorre a
página inteira**, do input do hero até o CTA final — como uma trilha de PCB conectando
todas as seções, ramificando-se para "alimentar" cada bloco de conteúdo.

Comportamento:
- A trilha existe apagada (`--gold-deep`, opacity .25) desde o load.
- Conforme o scroll avança, um **pulso dourado** (gradiente `--beam` animado via
  `stroke-dashoffset` com ScrollTrigger `scrub: 0.8`) percorre a trilha — o scroll do
  usuário *é* a energia que liga o sistema.
- Quando o pulso alcança a ramificação de uma seção, a seção "liga": borda do card
  acende, conteúdo faz reveal, mini-demo interna inicia. **Causalidade visível** — o
  usuário sente que ele ativou aquilo.
- No desktop a spine corre levemente off-center (esquerda); no mobile vira uma linha
  vertical fina na margem esquerda (versão simplificada, sem ramificações).

Implementação: 1 SVG `position:absolute` por seção (não um único SVG gigante — evita
reflow e permite lazy), paths desenhados à mão com `vector-effect: non-scaling-stroke`.
GSAP ScrollTrigger sincroniza `stroke-dashoffset` + classe `.is-live` nas seções.

---

## 4. Arquitetura narrativa — a jornada em 3 atos

```
ATO I — A PROMESSA          ATO II — A PROVA                    ATO III — A DECISÃO
┌──────────────┐  ┌──────────────────────────────────┐  ┌─────────────────────┐
│ 0 Boot/Nav   │  │ 3 Scrollytelling "Jornada do     │  │ 7 Comparação        │
│ 1 Hero       │→ │   Domínio" (pinned, 4 estações)  │→ │ 8 Prova social      │
│ 2 Logos/fé   │  │ 4 Bento de features              │  │ 9 Pricing           │
└──────────────┘  │ 5 Execução (pipeline vivo)       │  │ 10 CTA final        │
                  │ 6 Dashboard/números              │  │ 11 Footer           │
                  └──────────────────────────────────┘  └─────────────────────┘
```

Arco emocional: **curiosidade → fascínio → confiança → urgência**. O pico de motion fica
no Ato II (scrollytelling); o Ato III desacelera deliberadamente — menos animação, mais
clareza — porque ali o visitante está decidindo, não se encantando.

---

## 5. Seção a seção — layout, scroll e motion

### 5.0 · Boot sequence (preloader) — máx. 1.2s, só na primeira visita

Tela `--void` com uma única linha mono digitando:
`resolving leadenricher.app … 200 OK` → cursor pisca → a linha se transforma (morph) na
spine do hero e a página "liga". Skippable por clique; pulado via `sessionStorage` em
visitas seguintes; ausente se `prefers-reduced-motion`. (Referência: boot da Teenage
Engineering / terminal da Vercel.)

### 5.1 · Navbar

Pill glass (já existente) refinada: fundo `rgba(6,5,3,.7)` + blur 16px. Ao rolar >80px,
um **border-beam** dourado percorre o contorno da pill uma vez (1.6s) e para — anuncia a
mudança de estado sem poluir. Link ativo com underline que *desenha* da esquerda (scaleX).
CTA "Entrar" em dourado sólido com **efeito magnético** (o botão desliza ≤6px em direção
ao cursor — GSAP quickTo).

### 5.2 · Hero — *"Digite um domínio. Conheça a empresa inteira."*

**Layout:** headline à esquerda (60%), à direita um **terminal de demonstração vivo**.
Abaixo da dobra, a spine nasce do terminal e mergulha para a próxima seção.

**Fundo (o grande momento WebGL/canvas):** uma **malha de rede tridimensional** — ~800
nós (pontos) conectados por arestas finas, levemente rotacionando, em `--gold-deep`
quase apagado. Pulsos `--heat` viajam aleatoriamente entre nós (2–3 simultâneos). O
mouse cria um **campo de revelação**: nós num raio de ~180px do cursor acendem para
`--gold` e exibem micro-labels mono (`MX`, `A`, `SPF`, `NS`, `ASN`) que somem em 1s.
A internet está lá, apagada — o cursor do usuário a revela. *Essa é a tese do produto
em um efeito.*

**Headline:** reveal por linha com máscara (`clip-path` + stagger 80ms, ease
`power3.out`). A palavra **"inteira"** recebe o gradiente shimmer (§2.1).
Sub: "DNS, e-mail corporativo, decisores e score de prioridade em 5 segundos. Sem Lusha.
Sem planilha." Em mono, `--text-2`.

**O terminal-demo (signature component #1):** um card `--surface` com chrome de janela
(3 dots), onde roda um **loop de enriquecimento simulado** com dados reais de exemplo:

```
$ enrich nubank.com.br
├─ MX     → Google Workspace          ✓ 0.8s
├─ SPF    → v=spf1 include:_spf...    ✓
├─ DMARC  → p=reject                  ✓
├─ PORTE  → 5.000+ funcionários       ✓ 1.9s
├─ DECISOR→ CTO encontrado · e-mail válido
└─ SCORE  → 87/100  ████████▌  ALTA PRIORIDADE
```

Cada linha "chega" com som visual: flash dourado de 120ms na borda esquerda + contador
de tempo real. Ao completar, pausa 3s, limpa com efeito CRT e roda outro domínio
(rotação de 4 exemplos). **O input é real**: o visitante pode digitar um domínio — isso
o leva ao signup com o domínio pré-preenchido (`/app?domain=…`). A barra de score que
preenche é dourada com núcleo `--gold-white`.

**Scroll-out:** ao sair do hero, a malha de fundo *converge* — os nós se alinham e
escoam para dentro da spine (partículas seguem o path), literalmente "a rede inteira
entra no fio que alimenta o resto da página".

### 5.3 · Faixa de confiança (logos)

Marquee duplo infinito (duas linhas, direções opostas, velocidades 0.8×/1×), logos em
`--text-3` que acendem para `--text` no hover, máscara de fade nas bordas. Pausa no
hover. Custo: CSS puro (`animation` + `mask-image`). Acima, uma linha mono:
`> confiado por equipes comerciais que odeiam trabalho manual`.

### 5.4 · Scrollytelling pinned — **"A Jornada de um Domínio"** (signature moment #2)

A seção mais ambiciosa: **400vh de altura, viewport pinned**, o domínio `empresa.com.br`
viaja como um **pacote de luz** por 4 estações ao longo da spine, que aqui se expande
num diagrama de rede em tela cheia. Progresso 0→1 mapeado por ScrollTrigger `scrub`.

| Progresso | Estação | O que acontece na tela |
|---|---|---|
| 0–25% | **01 / RESOLVE** | O pacote entra num nó "DNS". Dele explodem, em órbita, cards mono com os registros reais: `MX 10 aspmx.l.google.com`, `SPF`, `DMARC p=reject`, `NS`, `SOA`. Cada card materializa com efeito **decode** (caracteres embaralhados resolvem no texto final, 400ms). Label lateral: *"Lemos a infraestrutura: provedor de e-mail, segurança, hosting."* |
| 25–50% | **02 / PROFILE** | Os cards DNS colapsam num **hexágono-empresa**. Ao redor, sobem chips: porte (barra de funcionários preenchendo), setor, localização (micro-mapa pontilhado do Brasil com ping dourado), LinkedIn ✓. Parallax: chips no plano frontal (1.15×). |
| 50–75% | **03 / DISCOVER** | Do hexágono partem cabos para **nós-pessoa**. Três acendem em sequência: avatar genérico + cargo em mono (`CTO`, `Diretor Comercial`, `Founder`). No nó do CTO, um zoom sutil: e-mail provável aparece caractere a caractere e o badge muda `verificando…` → `✓ SMTP válido` (verde, único uso de cor fria). Label: *"Decisores reais, e-mails verificados — sem APIs pagas."* |
| 75–100% | **04 / SCORE** | Tudo converge para um **medidor hexagonal**: fragmentos dourados (cada critério do scoring, com nome mono: `+10 Google Workspace`, `+15 e-mail válido`, `+20 telefone direto`) voam das estações anteriores e se encaixam como peças. O contador rola 0→87 (com easing de roleta), o anel preenche, o selo **ALTA PRIORIDADE** carimba com squash & stretch + flash `--ember`. |

**Saída do pin:** o medidor encolhe e *vira um card de lead real* que desliza para a
seção seguinte — costura perfeita entre o "filme" e a UI do produto.

**Mobile:** sem pin. As 4 estações viram blocos verticais com as mesmas micro-animações
disparadas por IntersectionObserver (uma vez, não scrubbed). Mesma história, custo baixo.

### 5.5 · Bento grid de features (estilo Linear/Apple)

Grid 12 col → 6 cards assimétricos (2 grandes, 4 menores), `--surface`, raio 20px,
borda `--line`. Cada card é uma **mini-demo viva**, não um ícone com texto:

| Card | Demo interna |
|---|---|
| **Relatório DNS completo** (grande) | Terminal em loop streaming registros; hover acelera o stream |
| **Decisores com e-mail verificado** (grande) | Card de pessoa onde 4 padrões de e-mail testam em sequência; o válido ganha ✓ |
| Score explicável | Mini-gauge; hover abre o breakdown linha a linha |
| Follow-up automático | Mini-calendário; um slot pulsa e um `.ics` "voa" para fora |
| Pipeline kanban | 3 colunas; um mini-card desliza de coluna a cada 4s |
| Export & CRM | Logos Dynamics/HubSpot/Pipedrive; pulso viaja do lead ao logo |

**Microinterações dos cards (padrão premium):**
- **Spotlight cursor**: `radial-gradient` dourado de 300px segue o mouse *dentro* do card
  (`background` posicionado via CSS vars atualizadas em `pointermove`) — o efeito Cursor da
  Linear/Vercel.
- **Tilt 3D contido**: `rotateX/Y` máx. 3° + `translateZ` no conteúdo (camadas) — perceptível,
  nunca caricato. Spring no leave (GSAP elastic.out suave).
- **Border-beam no hover**: o feixe `--beam` percorre o contorno uma vez.
- Entrada: stagger 90ms, `y:24→0` + `opacity` + `scale .97→1`, `power3.out`.

### 5.6 · Execução comercial — **"Do dado à reunião"** (pipeline vivo)

Fundo `--carbon` (alternância de ritmo). Um kanban estilizado em perspectiva isométrica
sutil (8°) ocupa a tela. Conforme o usuário rola (scrub leve), **cards de lead avançam
pelos estágios**: `novo → contatado → reunião` — cada movimento dispara um pulso na
spine e um toast minimalista (`☎ não atendeu → follow-up em 2 dias criado`) que sobe e
some. O card que chega em "reunião" ganha o selo dourado e um `.ics` materializa.
Copy lateral fixa (3 parágrafos que trocam por fade conforme o progresso — pattern de
scrollytelling lateral da Stripe).

### 5.7 · Números & dashboard — **"O que sua operação enxerga"**

Screenshot estilizado do dashboard (mock do v3) dentro de um frame de browser flutuante
com parallax e reflexo especular sutil. Ao entrar no viewport:
- KPIs contam com **odometer/scramble** (mono): `142 leads`, `43% contato`, `12% reunião`.
- O funil desenha-se (SVG `stroke-dashoffset`), barras crescem com stagger.
- Um gráfico de linha se traça da esquerda à direita com um cometa dourado na ponta.

### 5.8 · Comparação — **"Por que não Lusha + planilha + CRM?"** (Ato III começa: menos motion)

Tabela de 3 colunas (Stack manual / Ferramentas pagas / **LeadEnricher**), linhas
revelando em cascata. A coluna LeadEnricher tem fundo `--surface-2` e borda dourada
estática (sem beam — aqui é credibilidade, não show). Checkmarks desenham (SVG draw 200ms).

### 5.9 · Depoimentos

Cards glass com aspas gigantes em `--gold-deep`, carrossel horizontal com momentum
(drag + Lenis sync), avatares com anel dourado. Auto-play lento, pausa em hover.

### 5.10 · Pricing

3 cards. O **Pro** é maior (scale 1.04), com **border-beam contínuo lento** (8s/volta) —
o único elemento da página com animação dourada permanente nesse ato, puxando o olho ao
plano-alvo. Toggle mensal/anual com thumb deslizante spring e preços que rolam
verticalmente (slot machine, 300ms). Hover nos cards: lift 4px + sombra dourada difusa.

### 5.11 · CTA final — **"Sua próxima reunião começa com um domínio."** (signature moment #3)

Tela cheia, `--void`. Todas as partículas e a spine da página **convergem para um único
input gigante centralizado** (replica do hero — fechamento circular da narrativa).
O input tem caret dourado pulsante e placeholder que digita sozinho exemplos de domínio.
Em volta, anéis concêntricos de pulso emanam lentamente (sonar invertido — tudo é
atraído, não emitido: *os dados vêm até você*). Botão "Começar grátis →" magnético,
com 5 buscas grátis explicitadas embaixo em mono. Zero distração: nav some (fade) nesta
seção.

### 5.12 · Footer

Mini-rede de nós apagada como textura de fundo. Colunas padrão + uma linha de status
real: `● all systems operational` (consome o `/health` da API — detalhe técnico que
audiência técnica nota). Easter egg: clicar 5× no logo dispara uma chuva de pulsos na
rede do footer.

---

## 6. Catálogo de microinterações globais

| Interação | Especificação |
|---|---|
| Botões primários | Magnéticos (≤6px), shimmer interno no hover, `scale .97` no press com spring de volta |
| Links | Underline que desenha (scaleX origin-left, 250ms) |
| Inputs | Borda inferior dourada que expande do centro no focus; caret `--gold` |
| Cursor (desktop) | Dot dourado 6px com anel que atrasa 80ms (lerp); anel expande sobre elementos clicáveis; **desligado** em `pointer: coarse` |
| Seleção de texto | `::selection{background:var(--gold);color:var(--void)}` |
| Scrollbar | Custom fina, thumb `--gold-deep` → `--gold` no hover |
| Números/stats | Efeito scramble-decode (estilo terminal) ao entrar no viewport, uma vez |
| Imagens/frames | Reveal com máscara dourada que varre (clip-path inset, 600ms) |
| Toasts | Slide+spring, barra de progresso dourada, mono |
| Transição landing→app | O input do CTA expande até preencher a tela (`view-transition` API com fallback fade) |

**Física padrão:** nada de `ease` linear/genérico. Entradas: `power3.out` ou
`cubic-bezier(0.22,1,0.36,1)`. Springs (GSAP `elastic`/`back.out(1.4)`) só em elementos
pequenos. Durações: micro 150–250ms · entradas 500–700ms · cinematográficas 900–1200ms.
Stagger universal: 60–90ms.

---

## 7. Stack técnica — decisão de arquitetura

**Contexto:** o front atual é vanilla JS + CSS servido por FastAPI/Jinja2, sem build step.
A landing não justifica adotar React.

### Recomendado (vanilla, sem build obrigatório)

| Necessidade | Lib | Peso (gz) | Por quê |
|---|---|---|---|
| Smooth scroll | **Lenis** | ~4 KB | Padrão da indústria (usado por Framer, Locomotive); integra com ScrollTrigger em 5 linhas |
| Timelines, pin, scrub | **GSAP 3 + ScrollTrigger** | ~30 KB | 100% free desde 2024 (inclui SplitText, MorphSVG); insubstituível para o scrollytelling §5.4 |
| Text reveal | **GSAP SplitText** | incluso | Reveals por linha/palavra com máscara |
| Rede de partículas do hero | **Canvas 2D custom** (fase 1) → **OGL** (fase 2, ~35 KB) | 0→35 KB | Já temos expertise canvas (`animations.js`); 800 nós a 60fps é tranquilo em 2D. OGL entra se quisermos profundidade real + shader fog. **Three.js (~150 KB) é rejeitado**: peso não se paga para um efeito |
| Reveals simples | **CSS scroll-driven animations** + IntersectionObserver | 0 | Progressive enhancement; menos JS |
| Transição landing→app | **View Transitions API** | 0 | Nativa, com fallback |

**Rejeitados e por quê:** React Three Fiber e Framer Motion exigem React — adotar um
framework inteiro para uma página de marketing quebraria a coerência da stack (Jinja2 +
static/) e dobraria o tempo de build/manutenção. Locomotive Scroll: substituído por Lenis
(mais leve, mantido). Lottie: desnecessário — nossos efeitos são código, não vídeo.

**Estrutura de arquivos:**
```
templates/landing.html        ← nova rota "/" (marketing)
templates/index.html          ← app movido para "/app"
static/landing/
  landing.css                 ← tokens §2 + seções
  landing.js                  ← orquestração GSAP/Lenis (ESM)
  fx/network.js               ← rede de partículas do hero (canvas)
  fx/spine.js                 ← Data Spine (SVG + ScrollTrigger)
  fx/terminal.js              ← terminal-demo do hero
  vendor/                     ← gsap.min.js, ScrollTrigger.min.js, lenis.min.js (self-hosted)
```
Libs self-hosted (não CDN de terceiros): performance previsível + sem dependência externa.

---

## 8. Performance, acessibilidade e responsividade — orçamentos rígidos

O "uau" morre se a página engasgar. Limites:

- **LCP < 2.0s**: headline e CTA são HTML/CSS puros — renderizam antes de qualquer JS.
  Canvas da rede carrega via `import()` dinâmico após first paint (`requestIdleCallback`).
  Fontes: `font-display: swap` + preload do woff2 da Space Grotesk.
- **JS total < 90 KB gz** na fase 1 (GSAP+ScrollTrigger ~30, Lenis ~4, nosso código ~25).
- **60 fps sempre**: animar somente `transform`/`opacity`/`clip-path`; `will-change`
  aplicado e removido por JS; canvas pausa fora do viewport (IntersectionObserver) e em
  `document.hidden`.
- **Degradação por capacidade**: `navigator.hardwareConcurrency < 4` ou
  `deviceMemory < 4` → contagem de partículas cai 70%, tilt/parallax desligam.
- **`prefers-reduced-motion: reduce`**: TODA animação vira fade estático de 200ms; rede
  do hero vira imagem estática do grid; pin do scrollytelling desliga (versão vertical).
  Não é checkbox de a11y — é um modo de apresentação de primeira classe.
- **Mobile (`pointer: coarse`)**: sem cursor custom, sem tilt, sem spotlight; scrollytelling
  na versão vertical (§5.4); partículas ~200. A página deve ser *excelente* no celular,
  não uma versão tolerada.
- **SEO/semântica**: conteúdo todo em HTML semântico (h1–h3, sections com aria-label);
  animações apenas revelam — nunca injetam — conteúdo. OG image dourada custom.
- **Contraste**: `--gold` sobre `--void` = ~9:1 ✓; nunca usar `--gold-deep` para texto.

---

## 9. Os cinco diferenciais "esse produto é mais avançado que os concorrentes"

1. **A página é o produto** — o terminal do hero enriquece um domínio de verdade na frente
   do visitante (e o input é funcional, levando ao signup pré-preenchido).
2. **Data Spine** — a trilha de circuito contínua que o scroll energiza; identidade
   estrutural que nenhum template tem e que costura a narrativa inteira.
3. **A Jornada do Domínio** — scrollytelling pinned de 4 estações que substitui qualquer
   "features list" por cinema funcional.
4. **Cursor-revelação no hero** — a internet apagada que acende sob o mouse: a tese do
   produto num gesto.
5. **Fechamento circular** — a página inteira converge fisicamente (partículas, spine)
   para o input final. Começa com um domínio, termina com o *seu* domínio.

---

## 10. Fases de implementação

| Fase | Entrega | Conteúdo | Esforço |
|---|---|---|---|
| **L1 — Fundação** | Rebrand + página estática premium | Tokens §2, tipografia, grain/vinheta, nav, hero (sem WebGL, com terminal-demo), bento §5.5, pricing, CTA, footer; reveals com CSS scroll-driven + IO; Lenis | 1–2 sessões |
| **L2 — Energia** | GSAP + assinatura | ScrollTrigger, Data Spine, rede canvas do hero com cursor-revelação, magnetismo, border-beams, marquee, contadores | 1–2 sessões |
| **L3 — Cinema** | Scrollytelling | "Jornada do Domínio" pinned completa + versão mobile; pipeline vivo §5.6; dashboard §5.7 | 2 sessões |
| **L4 — Polimento** | Detalhes que viram screenshot | Boot sequence, cursor custom, view transitions, easter eggs, OG images, auditoria Lighthouse (meta ≥95 perf) | 1 sessão |

Cada fase entrega uma página completa e publicável — nunca um estado quebrado.

---

## 11. Riscos e antídotos

| Risco | Antídoto |
|---|---|
| Motion demais → enjoo/ruído | Princípio §1.1 (ouro escasso) + Ato III deliberadamente calmo + reduced-motion de primeira classe |
| Scrollytelling pesado no mobile | Versão vertical separada desde o design (não adaptação tardia) |
| Página linda, conversão baixa | CTA visível em 3 pontos (nav, hero, final); copy orientada a dor ("sem Lusha, sem planilha"); demo funcional encurta o caminho ao signup |
| Dourado virar "cassino" | Nunca usar dourado em fundos grandes; saturação controlada (`#F5B700`, não `#FFFF00`); pretos quentes |
| Rebrand conflitar com o app atual | Fase L1 inclui migrar os tokens do app (`--accent #F04E00` → sistema ouro) para consistência landing↔app |
