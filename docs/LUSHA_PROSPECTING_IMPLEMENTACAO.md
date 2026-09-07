# Lusha Prospecting — especificação de implementação

> **Criado em**: 2026-09-06
> **Implementado em**: 2026-09-06
> **Para**: quem for continuar a integração da Lusha (humano ou LLM)
> **Atualizado em**: 2026-09-07
> **Estado**: **fases 1 a 5 implementadas**. Falta fechar a Fase 0 — capturar a
> resposta real da API numa fixture. Desde 2026-09-07 isso é **um comando**:
> `scripts/capturar_fixtures_lusha.py`. Falta só a chave. Ver §11.

---

## 0. Estado da implementação (leia primeiro)

| Fase | Estado |
|---|---|
| 0 — confirmar o contrato real | ⚠️ **parcial** — paths, auth, corpo e erros confirmados na documentação oficial; **nomes dos campos de cada contato continuam não verificados contra resposta real** |
| 1 — camada de provedor | ✅ `services/providers/lusha_prospecting.py` |
| 2 — persistência | ✅ migração `7d3c1a94ef60`, 9 colunas em `decision_makers` |
| 3 — endpoints | ✅ `/leads/{id}/contacts`, `/decision-makers/{id}/reveal`, `/lusha/filters`, `/me/lusha` estendido |
| 4 — frontend | ✅ duas colunas, sidebar de filtros, revelação sob clique, paginação |
| 5 — configuração | ✅ saldo, limites e tabela de custos |
| testes | ✅ 87 novos (60 de provedor + 27 de endpoint); suíte em **926 passando** (2026-09-07) |

**O que NÃO foi feito e por quê**: nenhuma chamada real à Lusha foi disparada.
Não havia chave no ambiente, e gastar crédito da conta de um usuário é decisão
dele, não de quem implementa. As fixtures em `tests/fixtures/` foram montadas a
partir da documentação oficial — ver §11 para fechar isso por 1 crédito.

Isso continua valendo em 2026-09-07: seguiu sem chave no ambiente (procurada em
`.env`, `.env.local` e `.env.producao`). O que mudou é que a captura deixou de
ser uma receita de quatro passos e virou um comando que, além de gravar as
fixtures, **compara o resultado com o que o parser consegue ler** e aponta os
campos divergentes — ver §11.

Este documento é auto-contido: dá para implementar tudo daqui sem ler o histórico
da conversa que o originou. Ele separa de propósito **o que foi verificado contra
a API real** do **que ainda é suposição** — porque a implementação atual quebrou
exatamente por não ter feito essa separação.

---

## 1. Contexto do produto

O LeadEnricher enriquece leads B2B a partir do domínio. O caminho gratuito
(`services/decision_finder.py`) busca decisores em fontes públicas: cache global
de pessoas, quadro societário da Receita, aba People do LinkedIn e motores de
busca.

Esse caminho tem um **teto estrutural**, não de engenharia: celular de executivo
não está em fonte pública, e site corporativo moderno não publica e-mail nominal
(0 em 5 dos testados). O palpite de e-mail fica em ~45/100 de confiança.

A Lusha entra para fechar esse buraco, no modelo **BYOA (bring your own API key)**:
cada usuário conecta a própria conta Lusha em Configurações e gasta os próprios
créditos. Sem chave conectada, nada da Lusha é chamado e o produto segue 100% no
caminho gratuito. A chave é gravada cifrada (`SegredoCriptografado`) na coluna
`profiles.lusha_api_key`.

### Objetivo desta fase

Replicar, dentro do LeadEnricher, a experiência da extensão da Lusha: ao abrir a
ficha de uma empresa, mostrar **vários contatos daquela empresa** (nome, cargo,
LinkedIn, localização) em cards, com filtros de cargo/senioridade numa barra
lateral, paginação, e revelação de e-mail/telefone sob clique.

---

## 2. ✅ O erro que foi corrigido

> Corrigido em 2026-09-06: `find_company_contacts()` foi **removida** de
> `services/providers/lusha.py` e substituída pelo par `search` + `enrich` em
> `services/providers/lusha_prospecting.py`. O texto abaixo fica como registro
> do que deu errado — é o modo de falha que a §11 ainda pode reintroduzir.

A implementação anterior de `find_company_contacts()` em
`services/providers/lusha.py` chamava:

```
GET https://api.lusha.com/v2/company?domain=...&limit=50
```

**Isso está errado para este caso de uso.** O endpoint `/v2/company` enriquece
**dados firmográficos de uma empresa** (setor, tamanho, receita) — ele não
devolve uma lista de pessoas. A função foi escrita assumindo um formato de
resposta que nunca foi verificado, e a extração tolerante mascara a falha: ela
devolve `None` silenciosamente em vez de erro, então parece "não achou contatos"
quando na verdade a chamada está conceitualmente errada.

**O que a tela da extensão da Lusha realmente usa é a Prospecting API**, que
funciona em duas etapas:

| Etapa | O que faz | Custo |
|---|---|---|
| **search** | Lista contatos por filtros. Devolve nome, cargo, empresa, LinkedIn, localização e quais campos podem ser revelados. **Não revela e-mail/telefone.** | 1 crédito por 25 resultados |
| **enrich** | Revela e-mail e/ou telefone dos IDs escolhidos. | 1 crédito/e-mail, 5 créditos/telefone |

Esse desenho em duas etapas explica exatamente a tela da extensão: os contatos
aparecem com nome e cargo já visíveis (baratos, vieram do search), e cada linha
tem um botão "Mostrar detalhes" que dispara o enrich (caro) só para aquele
contato. É também o desenho que **não queima crédito do usuário à toa** — o
requisito mais importante desta integração.

### Decisão

Substituir `find_company_contacts()` pelo par `search` + `enrich`. A função atual
pode ser removida: nada em produção depende dela além do endpoint
`/leads/{id}/popular-contacts`, que será reescrito.

---

## 3. Dados verificados contra a API real

Coletados em **2026-09-06** através do conector MCP da Lusha, numa conta de plano
**premium**. São valores de resposta real, não documentação.

### 3.1 Preços por ação (`pricing`)

| Ação | Créditos | Por quantidade | Observação |
|---|---|---|---|
| `contactSearch` | 1 | 25 contatos | **barato** — popular a tela inteira custa pouco |
| `revealEmail` | 1 | 1 contato | |
| `revealPhone` | **5** | 1 contato | o mais caro; nunca revelar em massa |
| `companySearch` | 1 | 25 empresas | |
| `revealCompany` | 1 | 1 empresa | |
| `showSignalsContact` | 1 | 1 contato | premium, opcional |

**Consequência de design**: mostrar 25 contatos custa 1 crédito. Revelar telefone
dos mesmos 25 custaria 125. A tela deve popular via search e revelar só sob clique
explícito, um contato por vez.

### 3.2 Limites de requisição (plano premium da conta testada)

| Janela | Limite | Reset |
|---|---|---|
| minuto | 300 | rolling |
| hora | 600 | rolling |
| dia | 6000 | diário |

Estourar devolve **HTTP 429**. Planos menores têm limites bem menores
(Free/Starter: 40/min, 100/dia). O código não pode assumir o limite premium.

### 3.3 Senioridade — IDs válidos

Confirmado via `prospecting_contact_filters(type="seniority")`. **Esta é
exatamente a lista que aparece na barra lateral da extensão, na mesma ordem:**

| ID | Nome |
|---|---|
| 10 | founder |
| 7 | partner |
| 9 | c-suite |
| 8 | vice president |
| 6 | director |
| 5 | manager |
| 4 | senior |
| 3 | entry |
| 2 | intern |
| 1 | other |

Os IDs **não** são sequenciais na ordem de exibição — não gerar essa lista por
loop, usar o mapa literal acima.

### 3.4 Departamentos — valores válidos

Confirmado via `prospecting_contact_filters(type="departments")`. São strings
exatas, não IDs:

```
Business Development, Consulting, Customer Service, Engineering & Technical,
Finance, General Management, Health Care & Medical, Human Resources,
Information Technology, Legal, Marketing, Operations, Other, Product,
Research & Analytics, Sales
```

### 3.5 Pontos de dados — valores válidos

Confirmado via `prospecting_contact_filters(type="existing_data_points")`. Usado
para filtrar "só quem tem celular", e é a origem dos badges de contagem na tela:

```
phone, direct_phone, mobile_phone, unknown_phone, no_dnc_phone,
email, work_email, private_email
```

### 3.6 Contrato de search e enrich

Verificado na assinatura das ferramentas MCP, que espelham a API:

**search**
- Paginação: `page` (**0-based**) e `page_size` (**mínimo 10, máximo 50, default 20**).
- Exige **pelo menos um filtro**. Para "contatos desta empresa", o filtro é
  `companyDomains: ["nubank.com.br"]`.
- Filtros de contato relevantes: `jobTitles`, `jobTitlesExactMatch`,
  `normalizedJobTitles`, `seniority` (IDs), `senioritiesLabels`, `departments`,
  `countries` (ISO-2), `locations`, `existing_data_points`,
  `existingDataPointsCondition` (`and`/`or`).
- `searchText` é **dica de relevância, não filtro exato** — não usar para
  precisão.
- Resposta traz, por contato, um array **`canReveal[]`** com `{field, credits}`,
  dizendo quais campos aquele contato permite revelar e quanto custa. É a fonte
  da verdade para habilitar/desabilitar o botão de revelar.

**enrich**
- Recebe `ids[]` — os IDs vindos do search. Máximo **50 por requisição**.
- IDs vêm em forma criptografada (formato `v1.AbCd...`), tratar como string opaca.
- `reveal[]` opcional escolhe os campos (`emails`, `phones`); omitir revela tudo
  que estiver em `canReveal`. **Passar um valor que não estava em `canReveal`
  daquele contato é rejeitado com 400.**
- `waterfallEnabled` (bool) controla o fallback para fornecedores externos quando
  a Lusha não tem o dado. `false` força só-Lusha e evita créditos de terceiros.
  Omitir usa o padrão da conta.
- Resposta traz `emails`/`phones` com **`dataSource`** (`lusha` ou nome do
  fornecedor externo), **`missingDataPoints[]`**, e `status`/`statusReason`/
  `statusDescription` no nível da requisição.

---

## 4. O que era suposição — e o que virou fato

> **Atualizado em 2026-09-06 contra docs.lusha.com.** As três dúvidas abaixo
> eram as que bloqueavam a implementação. Duas fecharam; a terceira só fecha
> com uma chamada real.

**Resolvido pela documentação oficial:**

| Dúvida | Resposta |
|---|---|
| Paths REST | `POST https://api.lusha.com/v3/contacts/prospecting` (search) e `POST .../v3/contacts/enrich`. **Não** são `/prospecting/contact/search` como este documento supunha. |
| Header de autenticação | `api_key` — o mesmo do `/v2/person`. Dúvida nº 3 resolvida. |
| Corpo do enrich | O campo é **`contactIds`**, não `ids`. E o teto documentado é **100**, não 50. O código usa 50 — o menor dos dois valores quando as fontes discordam. |
| Códigos de erro | 400, 401, 402, 429 e **451 (bloqueio GDPR)** — este último não estava previsto neste documento. |
| Limite de requisições | Vem nos headers `x-minute-requests-left`, `x-hourly-requests-left`, `x-daily-requests-left`. Dá para ler o limite real do plano do usuário em vez de assumir o premium. |

**Continua NÃO verificado — é o que sobra da Fase 0:**

- O **nome exato de cada campo** de um contato na resposta: se o nome vem em
  `name` ou em `firstName`/`lastName`, se o cargo é `jobTitle` ou
  `currentTitle`, onde exatamente vem a localização e o setor.

O parser aceita hoje mais de um nome por campo, justamente para não quebrar na
primeira divergência. **Mas tolerância não é verificação**: enquanto a fixture
não for real, um campo pode estar sendo lido do lugar errado sem ninguém notar.
É exatamente o modo de falha da §2. Ver §11.

## 4b. Texto original desta seção (mantido como registro)

**Leia isto antes de escrever qualquer código de rede.** O que segue é inferência
razoável, não fato confirmado. O erro descrito na seção 2 aconteceu por implementar
inferência como se fosse fato — não repetir.

1. **Os paths REST exatos.** O que foi testado foi o conector MCP, que é uma
   camada por cima da API. Os paths REST prováveis são
   `POST https://api.lusha.com/prospecting/contact/search` e
   `.../contact/enrich`, mas **isso precisa ser confirmado na documentação
   oficial (docs.lusha.com) antes de implementar**.

2. **O shape exato do JSON de resposta REST**: nomes dos campos de cada contato
   (`name` vs `firstName`/`lastName`, `jobTitle` vs `title`, onde vem a
   localização e o setor da empresa). O MCP normaliza a resposta; a API crua pode
   diferir.

3. **Se o header de autenticação da Prospecting API é o mesmo `api_key`** usado
   hoje em `/v2/person`.

**Como resolver antes de codar**: fazer uma chamada real com a chave de um
usuário conectado, com `page_size` mínimo (10), logar o JSON cru e escrever o
parser contra ele. Uma chamada de search custa 1 crédito — é barato o suficiente
para valer a certeza.

---

## 5. Estado do código (atualizado após a implementação)

### Implementado nesta fase

| Arquivo | O que tem |
|---|---|
| `services/providers/lusha_prospecting.py` | **novo** — `search_contacts()`, `enrich_contacts()`, `get_account_usage()`, `parse_contact()`, e as constantes verificadas da §3 (senioridade, departamentos, pontos de dados, preços) |
| `alembic/versions/0014_contatos_de_prospecting.py` | **novo** — 9 colunas em `decision_makers`; head passa a ser `7d3c1a94ef60` |
| `models/database.py` | campos de prospecting em `DecisionMaker`; `ALEMBIC_HEAD` atualizado |
| `models/schemas.py` | `DecisionMakerOut` estendido; `ContatosResponse`, `RevelarRequest`, `RevelarResponse` |
| `routers/enrichment.py` | `GET /leads/{id}/contacts`, `POST /decision-makers/{id}/reveal`, `GET /lusha/filters` |
| `routers/auth.py` | `GET /me/lusha` devolve saldo, limites e tabela de preços |
| `static/js/app.js` | seção de prospecting: sidebar, cards, revelação sob clique, paginação; `_lushaSaldo()` em Configurações |
| `static/css/styles.css` | classes `.pr-*` (substituíram as `.lusha-*` do card) e `.lu-*` (saldo) |
| `tests/test_lusha_prospecting.py` | **novo** — 60 testes de provedor |
| `tests/test_contatos_prospecting.py` | **novo** — 27 testes de endpoint |
| `tests/fixtures/` | **novo** — `lusha_search.json`, `lusha_enrich.json`, `LEIA-ME.md` |

### Removido

| O quê | Por quê |
|---|---|
| `lusha.find_company_contacts()` | chamava `/v2/company`, endpoint errado (§2) |
| `GET /leads/{id}/popular-contacts` | dependia da função acima; substituído por `/contacts` |
| `renderLushaContacts()` e classes `.lusha-*` do card | substituídos pela seção `.pr-*`, que tem filtros, paginação e estado de revelado |
| `tests/test_popular_contacts_integration.py` | mockava o formato antigo; coberto por `test_contatos_prospecting.py` |

### ⚠️ Correção a uma afirmação deste documento

A versão original dizia que `renderDecisoresV2()` e as classes `dec2-*` estavam
**sem nenhum chamador** e mandava removê-las. **Isso está errado**:
`searchDecisores()` chama `renderDecisoresV2()` em `static/js/app.js`. A função
é a que renderiza o resultado da busca por cargo (`POST /api/decisores`), que é
um fluxo diferente e continua vivo. Removê-la quebraria essa busca. **Não foi
removida.**

### Mantido intacto

`is_configured()`, `credencial_valida()`, `find_contacts()` (pessoa única, via
`/v2/person`), `_digits_to_e164()` e os extratores tolerantes de `lusha.py`
continuam válidos — a cascata de revelação (`services/people/waterfall.py`)
depende deles e não mudou. `lusha_prospecting.py` reaproveita
`_digits_to_e164()` e `_PHONE_TYPE_MAP` em vez de duplicá-los.

---

## 6. O que implementar

### Fase 0 — Confirmar o contrato real (bloqueia todo o resto)

1. Ler a documentação oficial da Prospecting API em docs.lusha.com.
2. Com uma chave real conectada, chamar search com `page_size: 10` e
   `companyDomains: ["<domínio de teste>"]`.
3. Logar o JSON cru completo.
4. **Colar o JSON real numa fixture de teste** (`tests/fixtures/lusha_search.json`)
   e escrever o parser contra ela. Isso trava o formato e faz qualquer mudança
   futura da Lusha quebrar um teste em vez de degradar em silêncio.
5. Repetir para enrich com **um** ID.

### Fase 1 — Camada de provedor (`services/providers/lusha_prospecting.py`)

Arquivo novo, separado de `lusha.py`, porque é outra API com outro modelo de
custo. Assinaturas sugeridas:

```python
def search_contacts(
    api_key: str,
    company_domains: list[str],
    *,
    page: int = 0,                      # 0-based
    page_size: int = 20,                # 10..50
    job_titles: list[str] | None = None,
    seniority_ids: list[int] | None = None,
    departments: list[str] | None = None,
    countries: list[str] | None = None,
    existing_data_points: list[str] | None = None,
) -> dict | None:
    """
    Devolve {"contacts": [...], "total": int, "page": int, "page_size": int}
    ou None em qualquer falha. Custa 1 crédito por 25 resultados.
    Nunca levanta exceção.
    """

def enrich_contacts(
    api_key: str,
    contact_ids: list[str],             # máx. 50
    *,
    reveal: list[str] | None = None,    # ["emails"] | ["phones"] | None = tudo
    waterfall_enabled: bool | None = None,
) -> dict | None:
    """
    Revela e-mails/telefones. Custa 1 crédito/e-mail e 5/telefone, por contato.
    Nunca levanta exceção.
    """
```

Requisitos:
- Reaproveitar `_digits_to_e164()` de `lusha.py` para normalizar telefone.
- Tratar os mesmos status já tratados lá: 401 (chave recusada), 402 (sem
  créditos), 429 (rate limit), 404, e qualquer outro → `None` + log.
- **Nunca levantar exceção**: a tela precisa degradar para o caminho gratuito.
- Validar `page_size` no intervalo 10..50 e `contact_ids` em no máximo 50 antes
  de chamar a rede — estourar isso é 400 garantido e crédito perdido.
- Distinguir no log "sem créditos" (402) de "rate limit" (429): a ação do usuário
  é diferente em cada caso.

### Fase 2 — Persistência

Migração nova (`0014_*.py`, `down_revision = "c5a71f0e3b92"`) para guardar o
estado de revelação. Atualizar `ALEMBIC_HEAD` em `models/database.py:764` — há um
teste que falha se esquecer.

Campos a acrescentar em `decision_makers` (ou tabela nova, se preferir isolar
contatos de prospecting):

| Campo | Tipo | Para quê |
|---|---|---|
| `lusha_contact_id` | String(64), index | ID criptografado do search; chave para o enrich |
| `revealed` | Boolean, default False | se já gastou crédito revelando |
| `can_reveal` | JSON | `canReveal[]` do search: quais campos e quanto custam |
| `data_points` | JSON | contagem por tipo, para os badges (ex.: 2 celulares) |
| `department` | String(100) | filtro e exibição |
| `seniority` | String(50) | filtro e exibição |
| `location` | String(255) | **substitui o "São Paulo, Brazil" chumbado no JS** |
| `company_industries` | JSON | tags "Finanças, Bancos +2" |
| `source` | String(20) | `lusha` ou `free` — saber a origem de cada contato |

Refletir os campos novos em `DecisionMakerOut` (`models/schemas.py:31`).

### Fase 3 — Endpoints

**`GET /api/leads/{lead_id}/contacts`** (substitui `popular-contacts`)

Query params: `page` (0-based), `page_size` (10..50), `job_titles`, `seniority`
(IDs, repetível), `departments`, `countries`, `data_points`.

Comportamento:
- Com chave Lusha → `search_contacts()`, grava os contatos com `revealed=False`.
- Sem chave, ou Lusha indisponível → cai para `find_decision_makers()` como hoje,
  mantendo o filtro de fidelidade que exige `linkedin.com/in/` na URL.
- Resposta precisa incluir `total`, `page`, `page_size` para a paginação, e um
  campo dizendo qual fonte respondeu (`lusha` ou `free`) para a tela poder
  explicar ao usuário.

**`POST /api/decision-makers/{id}/reveal`** (novo)

Body: `{"reveal": ["emails"] | ["phones"] | null}`.

- Chama `enrich_contacts()` com o `lusha_contact_id` daquele contato.
- Grava e-mail/telefone, marca `revealed=True`.
- **Se já estiver `revealed`, devolver o dado gravado sem chamar a Lusha** —
  revelar duas vezes cobra duas vezes.
- Devolver erro legível quando a conta estiver sem créditos (402) ou em rate
  limit (429); a tela precisa dizer qual dos dois foi.

**`GET /api/lusha/filters`** (novo, opcional mas recomendado)

Devolve as listas da seção 3 (senioridade, departamentos, pontos de dados) para a
sidebar. Não consome crédito. Pode ser servido de constantes no backend em vez de
chamar a Lusha — os valores estão na seção 3 deste documento.

**`GET /api/me/lusha`** (estender o existente)

Acrescentar saldo de créditos e limites de uso, para a tela avisar antes de o
usuário ficar sem. Requer um `get_account_usage()` no provedor (não consome
crédito).

### Fase 4 — Frontend

Layout de duas colunas, **em pé, sem ocupar a tela inteira** (requisito explícito
do usuário): sidebar estreita de filtros à esquerda, lista de cards à direita.

Sidebar:
- Checkboxes de senioridade, **na ordem da seção 3.3** (founder, partner, c-suite,
  vice president, director, manager, senior, entry, intern, other).
- Checkboxes de departamento.
- Filtro de pontos de dados ("só com celular", "só com e-mail").
- Campo de busca por cargo (`jobTitles`).
- Cada mudança de filtro refaz o search voltando para `page=0`.

Card de contato:
- Avatar circular com iniciais, nome, badge do LinkedIn.
- Cargo e **localização vinda da API** — remover o `"São Paulo, Brazil"` chumbado
  em `static/js/app.js:~2210`.
- Tags de setor da empresa ("Finanças, Bancos +2").
- Badges de contagem de pontos de dados (o "📱²" da tela da Lusha).
- **Dois estados**: não revelado → botão "Mostrar detalhes" com o custo em
  créditos; revelado → e-mail e telefone visíveis + ações (copiar, CRM, e-mail).

Paginação no rodapé, com `total` e `page_size` vindos do backend.

Estados a tratar explicitamente na tela: sem chave conectada (com link para
Configurações), sem créditos, rate limit, empresa sem contatos na base da Lusha.

### Fase 5 — Configuração da conexão

Em Configurações, além do que já existe:
- Saldo de créditos e uso do período.
- Aviso quando o saldo estiver baixo.
- Explicação clara do custo: listar contatos é barato (1 crédito/25), revelar
  telefone custa 5.
- Deixar explícito que os créditos são da conta Lusha do próprio usuário.

---

## 7. Testes

Mínimo para considerar pronto:

1. **Parser contra fixture real** — o JSON capturado na Fase 0, não um mock
   inventado. É o teste que impede o erro da seção 2 de se repetir.
2. `search_contacts()` devolve `None` (sem levantar) em 401, 402, 429, timeout e
   JSON malformado.
3. `enrich_contacts()` recusa lista com mais de 50 IDs **antes** de ir à rede.
4. `search_contacts()` recusa `page_size` fora de 10..50 antes da rede.
5. Endpoint cai para o caminho gratuito quando não há chave.
6. Endpoint cai para o caminho gratuito quando a Lusha devolve erro.
7. Revelar um contato já revelado **não** chama a Lusha de novo.
8. Filtro de fidelidade do caminho gratuito segue exigindo `linkedin.com/in/`.
9. Paginação: `page`/`page_size` chegam corretos ao provedor.
10. `ALEMBIC_HEAD` bate com a última migração (teste já existe, só não quebrar).

Atenção ao mockar: os testes existentes em
`tests/test_popular_contacts_integration.py` precisam mockar **duas** coisas —
`routers.enrichment._lusha_key_utilizavel` (senão o código nem chega a chamar a
Lusha, porque o perfil de teste não tem chave) e a função do provedor.

A suíte tinha **812 testes passando** antes desta fase. `tests/test_wa_jornada.py`
tem flakiness conhecida por estado compartilhado entre testes — se falhar, rodar
o arquivo isolado para confirmar que não é regressão desta mudança.

Rodar com:

```bash
python -m pytest tests/ -q
```

---

## 8. Decisões de produto já tomadas

**Dado da Lusha vai para a base global** (decidido em 2026-09-06, por João Pires).
Contatos revelados são gravados em `Person`/`PersonEmail`/`PersonPhone`, que não
são segmentadas por usuário — um dado pago pelo usuário A passa a ser servido ao
usuário B pelo cache. Isso é deliberado: a base compartilhada é o que faz o
produto melhorar a cada busca.

Contrapartida registrada: contratos de provedores B2B costumam restringir
redistribuição a terceiros. Revisar se os termos da Lusha mudarem. Isolar por dono
exigiria uma coluna de proprietário em `PersonEmail`/`PersonPhone` e filtro nos
dois pontos de leitura. Detalhes em `services/providers/lusha.py:34-47`.

---

## 9. Princípios que não podem ser quebridos

1. **Nunca gastar crédito sem ação explícita do usuário.** Revelar telefone custa
   5 créditos. Nada de revelar em lote, em background ou "por precaução".
2. **Nunca levantar exceção do provedor.** Lusha fora do ar, sem crédito ou em
   rate limit → devolve `None`, e a tela segue com o caminho gratuito.
3. **Sem chave conectada, zero requisições à Lusha.** O produto funciona inteiro
   sem ela.
4. **Não implementar contra formato suposto.** Capturar a resposta real, fixar em
   fixture, escrever o parser contra ela.
5. **Não assumir os limites do plano premium.** A conta do usuário pode ser
   Free/Starter (40/min, 100/dia).

---

## 10. Entrega

Commits pequenos e descritivos, um por fase. Mensagem em português, explicando o
**porquê** e não só o quê — é a convenção do repositório.

Rodapé obrigatório nos commits:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

Branch atual: `master`. Remoto:
`https://github.com/jpiresantunes29-dotcom/lead-enricher`.

```bash
python -m pytest tests/ -q
git add -A
git commit -m "..."
git push origin master
```

Rodar a suíte **antes** de cada push. Não fazer push com teste vermelho sem dizer
claramente, na mensagem para o usuário, qual teste está falhando e por quê.

---

## 11. ⚠️ O que ficou pendente: fechar a Fase 0

**Isto é o único item aberto, e é o mesmo tipo de risco que causou o erro da §2.**

As fixtures em `tests/fixtures/lusha_search.json` e `lusha_enrich.json` foram
montadas a partir da **documentação oficial**, não capturadas de uma resposta
real. O que está confirmado (paths, header `api_key`, `contactIds[]`, códigos de
erro, headers de limite) está na §4. O que **não** está: o nome exato de cada
campo de um contato.

O parser (`parse_contact()`) aceita mais de um nome por campo — `name` ou
`firstName`/`lastName`, `jobTitle` ou `currentTitle`, e assim por diante. Isso
evita quebrar na primeira divergência, mas **não substitui verificação**: um
campo pode estar sendo lido do lugar errado e ninguém perceber, que é
exatamente o que aconteceu com `/v2/company`.

### Como fechar (custa 1 crédito) — agora é um comando

> **Atualizado em 2026-09-07.** Os quatro passos manuais abaixo viraram um
> script: `scripts/capturar_fixtures_lusha.py`. Passo manual repetido é passo
> que sai errado, e este em particular custa crédito a cada tentativa.

```bash
LUSHA_API_KEY=... python -m scripts.capturar_fixtures_lusha --enrich
```

O script faz o search (1 crédito), grava `tests/fixtures/lusha_search.json`, e
— **a parte que o `curl` não fazia** — passa cada contato por `parse_contact()`
e imprime, campo a campo, quantos foram lidos:

```
Campo                  preenchidos
  ✓ name                 10/10
  ✗ title                 0/10     ← nome divergente, não dado ausente
```

Um campo em `0/N` é o parser lendo a chave errada: um contato sem cargo
acontece, dez sem cargo não. É exatamente o modo de falha silencioso que a §2
descreve, e que o parser tolerante a vários nomes de campo **esconde** em vez
de resolver.

Com `--enrich`, revela **um** contato (mais 1 crédito pelo e-mail) e grava
`tests/fixtures/lusha_enrich.json`. Sem a flag, só o search — o que já fecha
metade desta seção.

A chave só entra por variável de ambiente, nunca por argumento, para não parar
no histórico do shell. Não há chave no repositório: o modelo é BYOA e cada
usuário guarda a própria em `profiles.lusha_api_key`, criptografada.

Depois que o script disser que está tudo lido:

1. `python -m pytest tests/test_lusha_prospecting.py -q`
2. Ajustar `parse_contact()` onde os nomes divergirem, e rodar de novo.
3. Apagar o aviso no topo de `tests/test_lusha_prospecting.py` e a seção
   correspondente de `tests/fixtures/LEIA-ME.md`.
4. Atualizar a §0 e esta seção.

Feito isso, uma mudança futura de formato da Lusha quebra um teste em vez de
degradar em silêncio — que é o ponto inteiro da §9.4.

### Divergência entre fontes, ainda não resolvida

A documentação v3 diz que o enrich aceita até **100** IDs por requisição; a
ferramenta MCP expõe **50**. O código usa 50 (`ENRICH_MAX_IDS`) — o menor dos
dois é o único valor seguro enquanto as fontes discordam. Se a chamada real
confirmar 100, dá para subir a constante e ganhar metade das requisições.
