# Vendor — libs self-hosted do app (/app)

| Arquivo | Lib | Versão | Fonte |
|---|---|---|---|
| `supabase.js` | @supabase/supabase-js (build UMD) | 2.112.0 | https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.112.0 |

Por que não usar o CDN direto: o script do Supabase roda dentro da área
logada e enxerga o token de sessão do usuário. Carregá-lo de um domínio de
terceiro significa que qualquer comprometimento daquele domínio — ou um
sequestro de DNS no caminho — vira roubo de sessão de todos os usuários do
app. Servindo do próprio domínio, a superfície volta a ser só a nossa.

`?v=` na tag `<script>` é o cache-buster: **atualize a query string junto com
o arquivo**, senão o navegador continua servindo a versão antiga do cache.

Para atualizar:

```bash
curl -sL "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@<versão>" -o static/vendor/supabase.js
```

Depois: trocar a versão em `templates/index.html`, nesta tabela, e conferir
o login (magic link + OAuth) antes de publicar.
