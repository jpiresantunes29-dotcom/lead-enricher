# Guia de copy do app

Como escrever qualquer texto que aparece na área logada (`templates/index.html`,
`static/js/app.js`, `static/js/sheet.js`). Vale também para mensagens de erro
que o servidor devolve para a tela.

## Princípio

A tela não explica a si mesma. Se o vendedor precisa ler um parágrafo para usar
um botão, o problema é o botão. Texto que sobrar vai para o `title` (tooltip) ou
para os Guias.

## Regras

| Onde | Regra | Exemplo |
|---|---|---|
| Título da tela | O que é, até 3 palavras | "Pipeline", "Agenda" |
| Subtítulo | Opcional. Uma frase, até ~60 caracteres, diz o que fazer ali | "Quem contatar hoje." |
| Botão | Verbo + objeto, até 3 palavras | "Analisar", "Salvar telefone" |
| Estado vazio | Uma linha do que falta + um botão | "Nenhum lead ainda." + "Analisar empresa" |
| Erro | O que não deu + o que fazer | "Não foi possível salvar. Tente de novo." |
| Confirmação | A consequência, sem repetir a pergunta | "Excluir Nubank? Não dá para desfazer." |
| Sucesso | Curto, no passado | "Nota salva." |
| Detalhe técnico | Tooltip, nunca o corpo da tela | HMAC, JWKS, nomes de variável de ambiente |

## Voz

- "Você", frases curtas, verbo no começo.
- Números com dígito ("3 leads"), nunca "(s)": escreva o singular e o plural.
- Sem jargão onde houver palavra comum: "servidor de e-mail" antes de "MX",
  "conectar" antes de "integrar". Termo técnico fica quando o vendedor
  precisa reconhecê-lo (MX, SPF, DMARC no relatório de DNS).
- Sem exclamação, sem "por favor", sem "com sucesso".

## Termos

| Use | Para | Evite |
|---|---|---|
| lead | empresa no seu pipeline | prospect, oportunidade (é um estágio) |
| contato | pessoa na empresa | decisor (só quando o cargo importa) |
| ficha | a página de um lead | perfil, card |
| analisar | buscar os dados de um domínio | enriquecer, coletar |
| tarefa | algo a fazer com prazo | follow-up (exceto no nome da tela) |
