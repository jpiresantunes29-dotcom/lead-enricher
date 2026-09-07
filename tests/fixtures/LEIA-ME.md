# Fixtures da Lusha Prospecting

## Estado destes arquivos

`lusha_search.json` e `lusha_enrich.json` foram montados a partir da
**documentação oficial** da API v3 (docs.lusha.com), **não** capturados de uma
resposta real. O que está confirmado contra a documentação:

- caminho e método: `POST /v3/contacts/prospecting` e `POST /v3/contacts/enrich`
- header de autenticação: `api_key` (o mesmo do `/v2/person`)
- corpo do enrich: `contactIds[]`, `reveal[]`, `waterfallEnabled`
- presença dos campos `id`, `has` e `canReveal` em cada contato do search
- códigos de erro 400 / 401 / 402 / 429 / 451
- headers de limite: `x-minute-requests-left`, `x-hourly-requests-left`,
  `x-daily-requests-left`

O que **continua suposição**: o nome exato de cada campo do contato — se o nome
vem em `name` ou em `firstName`/`lastName`, se o cargo é `jobTitle` ou
`currentTitle`, e onde exatamente vem a localização.

## Por que isso importa

A implementação anterior quebrou exatamente por escrever o parser contra um
formato suposto: ela chamava `/v2/company` (dados firmográficos) esperando
receber uma lista de pessoas, e a extração tolerante devolvia `None` em
silêncio em vez de erro. Parecia "não achou contatos" quando a chamada estava
conceitualmente errada.

O parser em `services/providers/lusha_prospecting.py` aceita hoje mais de um
nome por campo, justamente para não quebrar na primeira divergência. Mas
tolerância não é verificação: enquanto a fixture não for real, um campo pode
estar sendo lido do lugar errado sem ninguém notar.

## Como fechar isso (custa 1 crédito)

Com uma chave conectada:

```bash
curl -s -X POST https://api.lusha.com/v3/contacts/prospecting \
  -H "api_key: $LUSHA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"filters":{"companies":{"domains":["nubank.com.br"]}},"pages":{"page":0,"size":10}}' \
  > tests/fixtures/lusha_search.json
```

Depois rodar `python -m pytest tests/test_lusha_prospecting.py -q` e ajustar o
parser onde os nomes divergirem. Repetir para o enrich com **um** ID.

Feito isso, apagar esta seção e trocar o cabeçalho do teste
`test_parser_contra_fixture` que hoje avisa que a fixture não é real.
