# Virada para produção

A ordem importa. Cada passo aqui existe porque, feito fora de ordem, ele deixa
o sistema num estado em que alguma coisa falha **em silêncio** — que é sempre
pior do que falhar alto.

A regra que orienta o roteiro: **ligue o recebimento antes do envio**. Um
convite sai e é cobrado; se o webhook ainda não estiver validando assinatura, a
resposta do lead é recusada e ninguém fica sabendo. Convite pago, lead perdido,
nenhum erro na tela.

---

## Antes de tudo: rode a conferência

```bash
curl -H "Authorization: Bearer $CRON_SECRET" https://SEU-DOMINIO/api/internal/preflight
```

Ela devolve tudo que está pendente, com a consequência de cada item, em três
níveis:

| nível | significa | o que fazer |
|---|---|---|
| `impede` | não funciona, ou funciona errado | resolva antes de ligar |
| `perigoso` | funciona, mas custa caro se der errado | decida conscientemente |
| `atencao` | funciona; alguma capacidade fica desligada | só saiba qual |

O boot também escreve isso no log do deploy. Em produção, o app **recusa
subir** com WhatsApp meio configurado — o erro aparece no deploy da Vercel em
vez de virar lead perdido três dias depois.

---

## 1. Banco

1. Crie o projeto no Supabase e copie a connection string.
2. `DATABASE_URL=postgresql://...` nas variáveis da Vercel.
   O esquema legado `postgres://` é convertido sozinho.
3. Rode as migrações **apontando para o banco de produção**:

```bash
DATABASE_URL="postgresql://..." alembic upgrade head
```

Confira em `/health`: `schema_ok` precisa ser `true`. Se vier `false`, a lista
`missing_tables` / `missing_columns` diz exatamente o que faltou.

> Migração é o único passo que não dá para desfazer com um clique. Faça antes
> de apontar tráfego para lá.

## 2. Ambiente e segredos

| variável | por quê |
|---|---|
| `APP_ENV=production` | fecha `/docs`, exige schema no boot, liga HSTS |
| `SUPABASE_JWT_SECRET` | só é necessário se o projeto **não** publicar chave pública. Ver "Como o login é verificado", abaixo |
| `CRON_SECRET` | sem ele a fila não anda e conversas da madrugada não são retomadas |
| `SECRETS_KEY` | sem ela o segredo que assina o push para o CRM fica **em claro no banco** |
| `SITE_URL` | URLs canônicas; sem ele, conteúdo duplicado com o `*.vercel.app` |
| `RESEND_API_KEY`, `MAIL_FROM` | sem eles o pedido de remoção (LGPD) nunca se confirma |

`CRON_SECRET` e `SECRETS_KEY` se geram com:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Qual projeto Supabase o app usa

Um só, e ele está no código — `PROJETO_REF` em `middleware/auth.py`, ao lado da
anon key e do JWKS do projeto; no navegador, `_SB_URL`/`_SB_ANON` em
`static/js/app.js`. Hoje:
[`sgfbplozrpjnsudoawpz`](https://supabase.com/dashboard/project/sgfbplozrpjnsudoawpz).
Um teste (`test_front_e_back_apontam_para_o_mesmo_projeto`) impede que os dois
lados divirjam.

`SUPABASE_ANON_KEY` e `SUPABASE_URL` do ambiente **só são aceitas se forem do
mesmo projeto** — servem para rotacionar a chave, não para trocar de projeto.
Uma variável de outro projeto é descartada, aparece no log de boot, na
conferência de prontidão e em `/api/auth/diagnostico`
(`projeto.variaveis_ignoradas`). Antes disso ela vencia o código em silêncio: o
navegador logava num projeto, o servidor verificava contra outro, e o login
terminava bem no Google para voltar 401 — com a tela acusando "projeto
diferente" sobre um código que estava certo.

Trocar de projeto de verdade é editar as quatro constantes (as três do backend e
as duas do app.js) e cadastrar as Redirect URLs do novo projeto.

### As URLs de retorno precisam estar autorizadas

Em **Authentication > URL Configuration**, `Redirect URLs` precisa conter cada
origem em que o app roda, com o caminho `/app`:

```
http://localhost:8000/app
https://<deploy>.vercel.app/app
```

Sem isso o Supabase descarta o `redirect_to` e manda a pessoa para a `Site URL`.
O login termina bem no provedor e o navegador volta **sem credencial nenhuma** —
nem sessão, nem erro. A tela detecta esse retorno vazio e diz qual URL cadastrar,
mas a correção é aqui.

### Como o login é verificado

O servidor confere a assinatura do token do Supabase por um de dois caminhos,
nesta ordem:

1. **Chave pública (JWKS)** — o projeto publica a parte pública da chave de
   assinatura em `/auth/v1/.well-known/jwks.json`, o token diz no `kid` qual
   usar, e nada precisa ser guardado no ambiente do deploy. É o caminho bom.
2. **`SUPABASE_JWT_SECRET`** (HS256) — só para projetos que ainda assinam com
   o JWT Secret legado.

A armadilha que já custou um dia de depuração: em **Supabase > JWT Keys**, se a
chave atual for do tipo *segredo compartilhado* e não a legada, o material dela
**não é extraível** — os próprios docs do Supabase dizem isso. O JWKS vem vazio
(chave simétrica não se publica) e o segredo legado não confere. Resultado: o
login pelo Google termina bem, toda rota autenticada responde 401 e nenhum
valor de variável resolve.

Se cair nisso, a saída é trocar a chave de assinatura do projeto por uma
**assimétrica (ECC P-256)**: ela é publicada no JWKS e o caminho 1 passa a
funcionar. A troca invalida as sessões abertas — todo mundo loga de novo.

### `SECRETS_KEY` — defina uma vez e guarde

Cifra em repouso o `webhook_secret` e o `access_token` de cada conexão de CRM.
Não é paranoia de checklist: o `webhook_secret` é a chave que assina o payload
que chega no Dynamics do usuário — quem o tiver forja um lead que o CRM dele
aceita como nosso. Uma cópia de banco tirada para depurar já basta.

Depois de definir pela primeira vez, converta o que já estava gravado:

```bash
python scripts/recriptografar_segredos.py
```

**Trocar a chave torna ilegível o que foi gravado com a anterior.** O push
dessas conexões passa a responder 409 (em vez de sair sem assinatura, que
seria a proteção desligada em silêncio), e o conserto é o usuário regravar o
segredo em Configurações. Guarde a chave junto das outras — ela não é
recuperável a partir do banco.

## 3. Crons (exige Vercel Pro)

Já declarados em `vercel.json`:

| rota | quando | para quê |
|---|---|---|
| `/api/internal/jobs/run` | a cada 5 min | fila de análise em lote |
| `/api/internal/wa/pending` | a cada 10 min | responde quem escreveu de madrugada ou ficou para trás |

Sem o plano Pro os crons não rodam, e a fila só anda enquanto alguém tem a aba
aberta.

## 4. WhatsApp — recebimento primeiro

1. Crie o WABA e o número no [Meta for Developers](https://developers.facebook.com/).
2. Defina **só** as variáveis de recebimento e faça o deploy:
   - `WHATSAPP_APP_SECRET` (App Secret do app da Meta)
   - `WHATSAPP_VERIFY_TOKEN` (qualquer segredo que você escolher)
3. No painel da Meta, cadastre o webhook:
   - URL: `https://SEU-DOMINIO/api/wa/webhook`
   - Token de verificação: o mesmo `WHATSAPP_VERIFY_TOKEN`
   - Assine o campo **messages**
4. O handshake tem que passar na hora. Se falhar, o token não bate.

## 5. WhatsApp — envio

1. Aprove **um** template de abertura na Meta (categoria *marketing*).
2. Só então defina:
   - `WHATSAPP_PHONE_NUMBER_ID` (o id do número no WABA, **não** o telefone)
   - `WHATSAPP_ACCESS_TOKEN`
   - `WHATSAPP_TEMPLATE_NAME` (o nome do template aprovado)
3. Deploy.

Confira em `/api/wa/status`: `configurado` precisa ser `true` e `faltando`
precisa estar vazio.

## 6. O primeiro teste é no seu próprio número

Não comece por um lead real. A ordem:

1. Crie um lead com **o seu celular** e clique em *Iniciar contato por WhatsApp*.
2. Confirme que o convite chega.
3. Responda alguma coisa simples ("oi, tudo bem?").
4. Veja o turno acontecer: a resposta automática deve chegar em segundos.
5. Abra a aba **Conversas** e confira: o selo, as mensagens e a auditoria.
6. Clique em **Assumir agora** e responda mais uma vez. A automação tem que calar.
7. Escreva "não quero mais receber mensagens" de outro número de teste e
   confirme que o lead vira `DO_NOT_CONTACT` e que o número entra no opt-out.

Só depois disso, um lead de verdade.

## 7. IA

`ANTHROPIC_API_KEY` liga a automação. Sem ela nada se perde: toda resposta de
lead vira pendência humana com o motivo escrito, e aparece com badge na barra
lateral. Ligue depois de o passo 6 ter funcionado sem IA.

Vale calibrar `WA_AI_MIN_CONFIDENCE` (padrão 0,7) contra conversas reais. Mais
alto = mais handoff e menos risco; mais baixo = mais automação e mais chance de
resposta errada.

---

## Como desligar, se der errado

Do mais cirúrgico para o mais bruto:

| situação | o que fazer |
|---|---|
| uma conversa saiu do controle | **Assumir agora** na aba Conversas — vale imediatamente |
| a IA está respondendo mal | apague `ANTHROPIC_API_KEY` e faça deploy: tudo vira pendência humana, nada se perde |
| o número está sendo denunciado | apague `WHATSAPP_ACCESS_TOKEN`: nada mais sai, o recebimento continua |
| precisa parar tudo | apague `WHATSAPP_APP_SECRET` **e** `WHATSAPP_ACCESS_TOKEN` |

Nenhuma dessas ações perde dados: as conversas, as mensagens e a trilha de
auditoria continuam no banco.

> Não desligue só o `WHATSAPP_APP_SECRET` deixando o envio ligado. Em produção
> o app se recusa a subir nesse estado — de propósito.

---

## O que monitorar depois de ligar

- **Qualidade do número.** Aparece no topo da aba Conversas quando cai.
  `YELLOW` é aviso; `RED` é o degrau antes da restrição — pare os convites
  frios na hora.
- **Taxa de handoff** (aba Conversas). Subindo muito, ou o prompt precisa de
  ajuste ou os leads não são os certos.
- **Conversas aguardando você** (badge na barra lateral). É o número que não
  pode acumular: cada uma é um lead que escreveu e não teve resposta.
- **Log do deploy.** A conferência de prontidão sai lá a cada boot.
