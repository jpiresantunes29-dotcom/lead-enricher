# Fila de enriquecimento e análise em lote

## O problema

Uma análise leva 15-30 s (site, DNS, LinkedIn, Receita) e a função serverless
morre em 60 s. Cem domínios em uma requisição não é lento — é impossível.

## A solução, em uma frase

O pedido só **enfileira**; o processamento acontece em **rodadas curtas**, cada
uma com orçamento próprio, disparadas por quem estiver disponível.

```
POST /api/batches            cria o lote (1 job por domínio) e responde na hora
POST /api/batches/{id}/run   processa uma rodada e devolve quantos faltam
GET  /api/batches/{id}       progresso, para a barra na tela
POST /api/internal/jobs/run  mesma rodada, chamada pelo cron
```

## Quem empurra a fila

**A tela do usuário, enquanto está aberta.** `processarLote()` chama `/run` em
sequência: cada chamada devolve `remaining`, e o front decide se pede outra.
Isso funciona em qualquer plano de hospedagem, mostra progresso real em vez de
um spinner de dois minutos, e nenhuma requisição chega perto do limite de tempo.

**O cron, quando ela não está.** `vercel.json` agenda `/api/internal/jobs/run`
de hora em hora — é a rede de segurança para quem fechou a aba no meio.

> No plano Hobby da Vercel os crons rodam **uma vez por dia**, independentemente
> do que estiver no `schedule`. Como o motor principal é o navegador, isso não
> trava o produto; no plano Pro o agendamento configurado vale como está.

Os dois caminhos podem rodar ao mesmo tempo sem processar o domínio duas vezes:
cada job é reservado com `UPDATE ... WHERE status = 'queued'`, e quem perde a
corrida pega o próximo.

## Regras de cobrança

Iguais às da busca avulsa, porque são o mesmo código
(`services/enrichment_service.py`):

- domínio pesquisado nos últimos 7 dias sai do cache e **não cobra**;
- tentativa que já cobrou (inclusive a interrompida) **não cobra de novo**;
- sem cota, o job **volta para a fila** em vez de virar erro — o lote retoma
  quando o ciclo renovar, sem o usuário precisar montar a lista outra vez;
- falha de coleta é retentada até `MAX_ATTEMPTS` (3) antes de virar `failed`.

## Formatos aceitos na entrada

`services/domain_list.py` resolve sozinho: uma coluna por linha, CSV com
cabeçalho (acha a coluna do site pelo nome), CSV sem cabeçalho (acha a coluna
que mais parece domínio), URLs completas e e-mails (usa o domínio). Linha sem
domínio nenhum é ignorada e contabilizada como "ignorada" na resposta.

## Limpeza das sessões demo

`/api/internal/demo/cleanup` (mesmo segredo de cron) apaga perfis
`demo-*` parados há mais de `DEMO_TTL_DAYS` (padrão 7), com leads, atividades,
jobs, créditos e tokens. Quem voltou a usar nas últimas 24 h não é apagado.

O banco global de contatos (Company/Person/EmailPattern) **não** é tocado:
aquele conhecimento é do produto e foi pago em tempo de coleta.
