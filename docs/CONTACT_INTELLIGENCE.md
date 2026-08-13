# Contact Intelligence — arquitetura implementada

> Implementado em 2026-07-31. Cobre a Fase A (fundação de dados) e a Fase B
> (extensão MVP) do plano "Lusha brasileiro". **Custo de operação: zero** —
> nenhum provedor pago é necessário.

## A ideia em uma frase

A extensão é a vitrine; o produto é o **banco de contatos que cresce sozinho**.
Cada busca do app e cada revelação alimentam `companies`, `persons` e
`email_patterns` — e a consulta seguinte sai do cache, de graça.

## Cascata de fontes (da mais barata para a mais cara)

| # | Fonte | O que entrega | Custo | Onde |
|---|---|---|---|---|
| 1 | Cache próprio | tudo que já foi revelado | 0 | `people/repository.py` |
| 2 | DOM do LinkedIn (extensão) | nome, cargo, empresa | 0 | `extension/content/linkedin.js` |
| 3 | **Padrão de e-mail do domínio** | e-mail corporativo | 0 | `people/email_patterns.py` |
| 4 | Site da empresa | e-mails nominais, telefone, CNPJ | 0 | `scraper.py` |
| 5 | **Receita Federal (CNPJ)** | sócios, telefone e e-mail oficiais | 0 | `providers/cnpj_receita.py` |
| 6 | Provedores pagos | celular pessoal | $ | `providers/__init__.py` (vazio) |

### 3 — Padrão de e-mail: o motor de custo zero

Empresas usam um formato só. Um e-mail confirmado ensina o domínio inteiro:

```
1º contato da Acme   → descobre '{first}.{last}'  (pode custar verificação)
2º ao 500º contato   → montado localmente, confiança 85+   (custo zero)
```

`learn_from_email()` é alimentado por: e-mails do site, e-mail declarado à
Receita e toda confirmação SMTP. Amostras conflitantes **derrubam** a confiança
— empresa com dois formatos é justamente onde o palpite cego erra.

### 5 — Receita Federal: a vantagem que o Lusha não tem

O CNPJ é extraído do rodapé do site (com validação de dígito verificador) e
consultado em **quatro fontes gratuitas em cascata** (BrasilAPI → CNPJá Open →
publica.cnpj.ws → ReceitaWS). Traz razão social, situação, porte, telefone,
e-mail e o **quadro societário** — os nomes reais dos decisores, com associação
oficial empresa→pessoa.

Validado em produção: RD Station, Nubank, Magazine Luiza e Petrobras devolvem
presidente e diretores corretos.

## Confiança (o selo que o vendedor vê)

| Nota | Significado | Cor no painel |
|---|---|---|
| 97 | SMTP confirmou a caixa | verde |
| 85-95 | padrão do domínio com ≥3 amostras concordantes | verde |
| 60-84 | padrão inferido, ou domínio catch-all | amarelo |
| < 60 | palpite pelo ranking de mercado | cinza |

**Catch-all limita a nota a 70**: o servidor aceita qualquer destinatário, então
"não deu erro" não prova que a caixa existe. Dizer o contrário seria mentir.

**Sem sondagem SMTP** (a Vercel bloqueia a porta 25), a nota do padrão vale
integralmente — ausência de verificação não é sinal negativo. O circuit breaker
em `email_verifier.py` desliga a sondagem após 3 falhas de conexão, em vez de
gastar 5 s por e-mail para sempre devolver "unknown".

## Revelação: livre

Não há crédito, cota nem plano. Qualquer conta autenticada revela quantos
contatos precisar; o que existe é o registro em `reveals` (quem revelou quem,
quando e por qual cascata), usado para a extensão mostrar "já revelado" e para
auditar a origem de cada dado — não para medir consumo.

O que continua limitando a operação é técnico, não comercial: o teto de tempo
do `/reveal` (12 s, para caber no limite da função) e o rate limit por minuto.

## LGPD

| Exigência | Implementação |
|---|---|
| Oposição/eliminação (art. 18) | `/remover-meus-dados`, público, sem cadastro |
| Sem nova base de dados pessoais | guardamos só o SHA-256 do valor |
| Bloqueio definitivo | trava na **entrega** e na **gravação** (`repository.add_email/add_phone`) |
| Anti-enumeração | resposta genérica idêntica para dado existente ou não |
| Origem de cada dado | `source` + `verified_at` em toda linha |
| Só dado profissional | CPF, faixa etária e data de entrada do QSA são descartados |

> A trava na gravação existe porque o palpite do padrão **regravava** um e-mail
> removido. Está coberta por teste de regressão.

## Desempenho

Medições reais, antes → depois:

| Etapa | Antes | Depois | O que mudou |
|---|---|---|---|
| DNS report | 80,9 s | 1,3 s | consultas em paralelo; WHOIS só quando muda a resposta |
| Busca do LinkedIn | 20-24 s | 0 s no caso comum | não busca de novo quando o site já deu o link |
| Contagem de funcionários | 42 s | ~9 s | fontes em paralelo; Google Cache (desativado em 2024) removido |
| **Enriquecimento completo** | **122 s** | **8-24 s** | soma do acima + teto global de 35 s |

O teto (`ENRICH_BUDGET_SECONDS`) devolve ficha parcial em vez de estourar os
60 s da Vercel e perder tudo.

## Precisão — bugs corrigidos

- **"2.629.565 funcionários"**: a busca no Bing ecoava o termo pesquisado e o
  regex lia o ID da página do LinkedIn como número de funcionários. Agora há
  teto de plausibilidade (2,5 mi) e slug numérico não vai para busca.
- **Nubank virava "Somos incansáveis pra você não precisar ser"**: o `<title>`
  era usado como razão social. Agora a ordem é JSON-LD → og:site_name →
  application-name → title, com detector de slogan e fallback para o domínio.

Ambos com teste de regressão em `tests/test_precisao.py`.

## Mapa de arquivos

```
services/people/
  identity.py         nome, cargo, senioridade, chave de deduplicação
  email_patterns.py   aprende e aplica o formato de e-mail por domínio
  company_lookup.py   nome da empresa → domínio (banco → web, com validação)
  optout.py           LGPD: hash, bloqueio, purga
  repository.py       upserts; nunca rebaixa dado verificado
  waterfall.py        orquestra a cascata com orçamento de tempo
services/providers/
  cnpj_receita.py     4 fontes gratuitas de CNPJ
  hunter.py           verificação premium (opcional)
  __init__.py         hook de provedores pagos — vazio hoje
routers/
  extension.py        pair · me · resolve · reveal · company · save · report
  privacy.py          opt-out público
extension/            MV3: manifest, service worker, content script, popup
```

## Endpoints da extensão

| Rota | Faz rede? | Para quê |
|---|---|---|
| `POST /api/extension/pair-code` | não | app gera o código |
| `POST /api/extension/pair` | não | troca código por token |
| `GET /api/extension/me` | não | confirma o pareamento e lista as fontes extras |
| `POST /api/extension/resolve` | não (só com `deep`) | prévia mascarada, ~2 s |
| `POST /api/extension/reveal` | sim (teto 12 s) | revela o contato |
| `POST /api/extension/company` | sim (teto 9 s) | ficha da empresa + decisores |
| `POST /api/extension/save` | não | joga na pipeline |
| `POST /api/extension/report` | não | remove e bloqueia |

## Quando houver orçamento para APIs pagas

Criar o módulo em `services/providers/` com `is_configured()` e
`find_contacts()`, e registrar em `CONTACT_FINDERS`. O `waterfall` já chama a
camada premium quando cache, padrão e Receita não bastaram — nenhuma outra
mudança é necessária. Ordem sugerida por custo-benefício no Brasil:
Dropcontact/Prospeo (e-mail) → Apollo/People Data Labs (celular).

## O que ainda não existe

- **Celular pessoal**: não está em fonte pública. Só com provedor pago.
- **Fila assíncrona**: `/reveal` é síncrono com teto de 12 s. Se a base de
  usuários crescer, virar job + polling (item 0.2 do roadmap).
- **Ícones da extensão** e submissão à Chrome Web Store.
