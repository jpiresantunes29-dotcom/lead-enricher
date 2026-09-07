# Auditoria de agosto/2026 — o que estava quebrado e como foi fechado

> Varredura completa do projeto: segurança, bugs, arquivos mortos e estado do
> deploy. Cada item abaixo foi corrigido e tem teste que impede a regressão.
> A suíte saiu de 244 para 282 testes.

O modo demonstração **continua ligado por decisão do produto**: o projeto roda
local, sem assinantes, e a sessão demo é a forma de explorar o produto inteiro
sem cadastro. O que mudou é que ela não dá mais acesso a cobrança (ver B4).

---

## Segurança

### S1 — JWT aceito sem verificação real (crítico)
`SUPABASE_JWT_SECRET` ausente virava string vazia, e o HS256 valida qualquer
token assinado com `""`: bastava forjar um JWT com o `sub` da vítima para
assumir a conta dela. Reproduzido em teste antes da correção.

**Correção:** `middleware/auth.py` só decodifica com segredo de 32+ caracteres.
Sem ele, a rota responde 503 (erro de configuração) — nunca "token aceito". O
boot avisa no log e, em produção sem modo demo, falha de propósito.
**Testes:** `tests/test_seguranca.py`.

### S2 — Modo demo (mantido, com trava de cobrança)
Continua ligado por padrão. O risco de custo permanece consciente: cada token
`demo-session-<algo>` cria uma conta Pro efêmera. Documentado em
`.env.example` com o passo para desligar (`DEMO_MODE=0`) quando houver
clientes reais.

### S3 — SSRF pelo webhook de CRM (alto)
O usuário salvava qualquer URL e o servidor fazia POST nela. `http://169.254.169.254`
(metadados da nuvem) era aceito.

**Correção:** validação na gravação (`routers/crm_config.py`, exige `https://`
e endereço público) e de novo no envio (`services/crm/webhook.py`), porque um
domínio pode passar a apontar para IP interno depois. Redirect desligado no
POST, para 302 não contornar a checagem.
**Testes:** `tests/test_integrations.py`.

### S4 — Opt-out apagava dados de terceiros (médio)
O formulário público removia contatos sem provar titularidade: com 5 pedidos
por minuto, dava para esvaziar a base alheia.

**Correção:** o pedido nasce `pending` e só bloqueia depois que o titular abre
o link enviado por e-mail (token de uso único, 24 h). Remoção de e-mail
confirma no próprio e-mail; telefone e LinkedIn exigem e-mail de contato.
O valor em claro é guardado só até a confirmação — depois resta o hash.
Envio por `services/mailer.py` (Resend opcional; sem chave, o link vai para o
log). **Testes:** `tests/test_extension.py`.

### S5 — Injeção de fórmula no export (médio)
Nome e descrição vêm de sites de terceiros; um valor começando com `=` era
executado como fórmula ao abrir a planilha.
**Correção:** `services/exporter.py` prefixa `=`, `+`, `-`, `@` com apóstrofo,
em CSV e XLSX. **Testes:** `tests/test_exporter.py`.

### S6 — SDK do Supabase vindo de CDN de terceiro (médio)
O script que enxerga o token de sessão era carregado do jsDelivr.
**Correção:** self-hosted em `static/vendor/supabase.js` (v2.112.0), com
`static/vendor/README.md` explicando como atualizar.

### S7/S8 — Superfície exposta (baixo)
`/docs`, `/redoc` e `/openapi.json` agora fecham com `APP_ENV=production`. O
CORS da extensão passou a exigir o formato real de ID do Chrome (`[a-p]{32}`)
e aceita allowlist por `EXTENSION_IDS`.

### Cabeçalhos de segurança
`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
`Permissions-Policy` e HSTS (em HTTPS/produção) em toda resposta.

---

## Bugs

| # | Bug | Correção |
|---|---|---|
| B1 | `0800 887 0463` virava `+55 80 0887 0463` (DDD inventado) | Números sem DDD (0800/0300/4004) mantêm o formato nacional; telefone direto tem prioridade sobre central |
| B2 | `/api/decisores` sem teto de tempo estourava os 60 s da Vercel | Orçamento global de 30 s, com corte por etapa e por motor de busca |
| B3 | Landing e JSON-LD vendiam "score explicável", removido do código | Promessas retiradas; nenhuma menção sobrou fora de nomes de classe CSS |
| B4 | Sessão demo podia pagar assinatura amarrada ao localStorage | Checkout recusa usuário demo (400) e o paywall convida a criar conta |
| B5 | Webhook do Stripe quebrava em `session.get(...)` — 500 em todo pagamento | Dados lidos do JSON puro; o SDK só confere a assinatura. Perfil é criado se o webhook chegar antes do primeiro login |
| B6 | Schema dependia de lista manual de colunas e falhava calado | Sincronização automática contra os modelos + `/health` com `schema_ok` + falha dura em produção |
| B7 | Dashboard misturava datas com e sem fuso | Todas as colunas de data são `timezone=True` e nascem de `models.database.utcnow()` |
| B8 | Busca de domínio lia leads de outros usuários | `company_lookup` só consulta leads do próprio `user_id` |
| B9 | Busca interrompida cobrava cota e não deixava rastro | Ficha `pending` gravada antes da coleta serve de recibo: a retentativa do mesmo domínio não cobra |
| B10 | Nota com quebra de linha corrompia o `.ics` | Escape conforme RFC 5545 |

---

## Limpeza

- Worktree órfã `.claude/worktrees/lucid-lehmann-9ad413` e o branch
  correspondente: removidos.
- `services/lead_scorer.py`, `tests/test_lead_scorer.py`, `alembic/` e
  `scripts/extract_executives.py`: remoções que estavam pendentes de commit.
- `.vercelignore` passou a excluir `tests/`, `docs/`, `scripts/`,
  `extension/` e `.claude/` do bundle de produção.
- 61 arquivos de trabalho acumulado foram commitados em cinco commits
  temáticos — antes disso, tudo vivia apenas no disco.

---

## O que ficou de fora, de propósito

- **Publicação da extensão na Chrome Web Store** — em aberto por decisão do
  produto. O checklist continua em `extension/README.md`.
- **Alembic** — enquanto o banco de produção não existir, a sincronização
  automática de schema cobre o caso. Adotar quando houver Postgres com dados
  reais (item 0.3 do roadmap).
