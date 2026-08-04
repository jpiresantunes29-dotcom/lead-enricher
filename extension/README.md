# Extensão LeadEnricher (Chrome / Edge)

Mostra decisores, e-mail corporativo verificado e telefone da empresa direto
nas páginas do LinkedIn — como o Lusha, mas com as fontes brasileiras
(Receita Federal) que os concorrentes de fora não têm.

## Instalar em modo desenvolvedor

1. Abra `chrome://extensions`
2. Ligue o **Modo do desenvolvedor** (canto superior direito)
3. Clique em **Carregar sem compactação** e escolha esta pasta (`extension/`)
4. Clique no ícone da extensão → cole o código de pareamento

## Conectar à sua conta

1. No LeadEnricher, vá em **Configurações → Extensão do navegador**
2. Clique em **Gerar código de pareamento** (vale 10 minutos)
3. Cole o código no popup da extensão e clique em **Conectar**

Rodando o backend local? Abra "Estou rodando o app localmente" no popup e
informe `http://localhost:8000` antes de conectar.

## O que aparece em cada página

| Página do LinkedIn | O que a extensão faz |
|---|---|
| `/in/{pessoa}` | Identifica nome, cargo e empresa; mostra e-mail/telefone mascarados com botão **Revelar** |
| `/company/{empresa}` | CNPJ, razão social, situação cadastral, porte, telefone, padrão de e-mail do domínio e lista de decisores |
| `/company/{empresa}/people` | Captura os perfis visíveis (grátis) ou revela em lote |
| `/search/results/people` | Mesmo comportamento do lote |

## Créditos

- 1 crédito = 1 pessoa revelada (e-mail + telefone).
- **Nada é cobrado quando não encontramos contato.**
- Revelar a mesma pessoa de novo dentro de 90 dias é grátis.

## Como a extensão se comporta (e por quê)

- **Só lê a página que você abriu.** Nunca navega, pagina ou clica sozinha.
- **Nenhuma chamada à API interna do LinkedIn** — é o que costuma gerar bloqueio de conta.
- **Lote com teto**: 25 perfis por vez, com intervalo variável entre um e outro.
- **O token nunca fica na página**: vive no service worker e no `chrome.storage`,
  fora do alcance de qualquer script do LinkedIn.

> Automatizar coleta viola os Termos de Uso do LinkedIn. A extensão opera
> dentro da sua sessão, como um leitor da tela que você já abriu, mas o risco
> sobre a conta é seu. Use com moderação.

## Extração resiliente

O LinkedIn muda o HTML com frequência. A extração tenta, nesta ordem:

1. **JSON-LD** (`script[type="application/ld+json"]`) — formato estável
2. **Meta tags** Open Graph
3. **DOM visível** (h1, headline, link da empresa)
4. **Título da aba + URL** — último recurso, nunca quebra

O método que funcionou é registrado no console (`le:extract`), então dá para
detectar uma quebra de layout antes do usuário reclamar. Abra o DevTools na
página do LinkedIn para ver.

## Estrutura

```
manifest.json          permissões mínimas (storage + os hosts usados)
background.js          service worker: token, chamadas à API
content/linkedin.js    extração + painel (shadow DOM, isolado do CSS do LinkedIn)
ui/popup.html|js       pareamento e saldo de créditos
```

## Antes de publicar na Chrome Web Store

- [ ] Ícones 16/48/128 px em `icons/` e referência no `manifest.json`
- [ ] Política de privacidade publicada e linkada na ficha da loja
- [ ] Descrição de propósito único ("single purpose") no formulário
- [ ] Justificativa de cada permissão (`storage`, host permissions)
- [ ] Revisão leva de 3 dias a 3 semanas — envie cedo
