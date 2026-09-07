# Fixtures da Lusha Prospecting

## Estado destes arquivos

`lusha_search.json`, `lusha_enrich.json` e `lusha_account_usage.json` foram
capturados de **chamadas reais** contra a API, em 2026-09-07, com uma chave
Lusha paga de verdade (plano premium), buscando o domínio `nubank.com.br` —
não montados a partir de documentação nem de suposição.

Custo real da captura: 1 crédito (search de 10 contatos) + 1 crédito (enrich
de 1 contato, mas cobrou 0 — o contato já tinha sido revelado antes por essa
conta, o que os próprios dados confirmam: `canReveal` já vinha com
`credits: 0` no search).

## O que essa captura corrigiu

A implementação anterior a 2026-09-07 tinha **três erros de formato**, todos
escritos contra documentação/ferramenta MCP e nunca testados contra uma
resposta real — o mesmo modo de falha que já tinha acontecido uma vez antes
(`/v2/company` em vez da Prospecting API, ver `docs/LUSHA_PROSPECTING_IMPLEMENTACAO.md`
§2):

1. **Corpo do search**: `pages` não existe — o campo é `pagination`. E o
   domínio da empresa não fica em `filters.companies.domains` direto, mas em
   `filters.companies.include.domains` (mesma coisa para os filtros de
   contato: `filters.contacts.include.{...}`, não soltos em
   `filters.contacts`). As duas primeiras tentativas reais devolveram 400
   ("property X should not exist") antes de acertar.
2. **Corpo do enrich**: o campo é `ids`, não `contactIds` como a documentação
   via ferramenta MCP dizia.
3. **Formato do contato**: `jobTitle` é um objeto aninhado
   (`{title, departments, seniority}`), não uma string solta. O LinkedIn vem
   em `socialLinks.linkedin`, não `linkedinUrl` na raiz. `company.industry`
   só existe na resposta do **enrich**, não no search (no search, `company`
   só tem `id`/`name`/`domain`).

Todas as três divergências foram confirmadas contra o YAML OpenAPI oficial
(`docs.lusha.com/_bundle/apis/@v3/openapi.yaml`) e DEPOIS validadas com uma
chamada real — a doc oficial e a resposta real bateram.

## O que a chamada real revelou que nem a doc oficial previa

- `has` (no search) é uma lista de **nomes de campo presentes**
  (`"firstName"`, `"jobTitle"`, `"phones"`, ...), não uma contagem por
  quantidade. O badge "tem 2 celulares" que a implementação original supunha
  não tem suporte confirmado — o que dá para saber é "tem telefone", não
  quantos.
- `dataSource: "lusha"` aparece em cada e-mail/telefone revelado na prática,
  mesmo esse campo não estando no schema formal do OpenAPI
  (`V3EmailAddress`/`V3PhoneNumber`). O schema publicado está incompleto
  nesse ponto — a resposta real manda o campo mesmo assim.
- `seniority` no contato pode vir com um rótulo que não está na lista oficial
  de filtro (`SENIORITY` em `lusha_prospecting.py`): a fixture real trouxe
  `"non-manager"` para uma funcionária de RH. A lista de **filtro**
  (`seniorityIds`, os 10 valores 1-10) e o rótulo que volta **no contato**
  são vocabulários relacionados mas não idênticos — o parser lê o texto como
  vier, sem validar contra a lista de filtro.
- `phones[].type` pode vir `"phone"` — fora do enum documentado
  (`mobile`/`direct`/`work`/`unknown`) — e o parser trata isso como
  `"unknown"` (fallback correto, comportamento já esperado).

## Se a Lusha mudar o formato de novo

Rodar `LUSHA_API_KEY=... python -m scripts.capturar_fixtures_lusha --enrich`
recaptura as duas fixtures e aponta divergências automaticamente. Custa 1-2
créditos. Ver o docstring do script para detalhes.
