# WhatsApp Business no LeadEnricher (Coexistência)

Como conectar o WhatsApp Business que a empresa **já usa no celular** ao
LeadEnricher, sem perder o app e trazendo o histórico.

O caminho é o oficial da Meta: **Embedded Signup** em modo **Coexistência**
(na documentação: *Onboarding WhatsApp Business app users*). O número continua
no celular e passa a funcionar também na Cloud API. Não há SMS: a confirmação é
um QR code lido pelo app WhatsApp Business.

## 1. Configuração única no app da Meta (sem código)

Feita uma vez, por quem administra o app em developers.facebook.com.

1. **App do tipo Business** com os produtos *WhatsApp* e *Facebook Login for
   Business*.
2. **Portfólio verificado** (Business Verification). Para conectar só o número
   da própria empresa, dono do app, não é preciso virar Tech Provider nem
   passar por App Review. Isso passa a ser exigido quando **outras empresas**
   forem conectar os números delas.
3. **Configuração de Embedded Signup** em *Facebook Login for Business →
   Configurations*, com o produto WhatsApp. O id gerado é o
   `META_ES_CONFIG_ID`.
4. **Webhook** em *WhatsApp → Configuration*:
   - URL: a que aparece em Configurações → WhatsApp → "URL do webhook"
     (`https://<seu-domínio>/api/wa/webhook`);
   - token de verificação: o valor de `WHATSAPP_VERIFY_TOKEN`;
   - campos assinados: `messages`, `account_update`, `history`,
     `smb_message_echoes` e `smb_app_state_sync`.
5. **Domínio permitido** no Facebook Login: o domínio de produção (o popup
   recusa abrir em domínio não cadastrado).

## 2. Variáveis do servidor

| Variável | O que é |
|---|---|
| `META_APP_ID` | id do app na Meta (público) |
| `META_ES_CONFIG_ID` | id da configuração de Embedded Signup (público) |
| `WHATSAPP_APP_SECRET` | App Secret do app. Troca o código pelo token **e** assina o webhook |
| `WHATSAPP_VERIFY_TOKEN` | o token do passo 4 |

Sem as três primeiras, o botão aparece apagado em Configurações dizendo o que
falta (`GET /api/wa/connection/embedded/config`).

## 3. O que o usuário faz

1. Configurações → WhatsApp → **Conectar com a Meta**.
2. No popup: entra com o Facebook, escolhe a empresa e a opção de conectar o
   WhatsApp Business existente, e lê o QR code com o app no celular.
3. Autoriza (ou não) o compartilhamento do histórico.

No mesmo clique, o servidor (`POST /api/wa/connection/embedded`):

- troca o `code` (válido por 30 s) pelo token;
- descobre o número da conta e confere que ninguém mais o conectou;
- inscreve o app nos webhooks da conta;
- pede à Meta a **agenda** e depois o **histórico**. Cada pedido vale uma vez
  por conexão e só nas 24 h seguintes. Se falhar, a conexão continua de pé e a
  tela mostra o aviso.

## 4. O que chega e como é tratado

Tudo em `services/wa/coexistencia.py`, a partir do webhook.

| Webhook | Vira |
|---|---|
| `history` | Conversas e mensagens antigas (até ~6 meses). Idempotente pelo id da Meta. **Não** abre janela de 24 h, **não** marca "aguardando você", **não** aciona a IA |
| `smb_message_echoes` | O que o dono digitou no celular, como mensagem enviada por você. **Tira a IA da conversa** (handoff) |
| `smb_app_state_sync` | A agenda (`wa_contacts`). Dá nome às conversas |
| `messages` de número desconhecido | Conversa **sem lead**, só humana (antes era descartada) |

### Conversas sem lead

O histórico traz clientes, fornecedores e conhecidos. Eles viram conversas com
`lead_id` nulo:

- aparecem no filtro **Outros contatos**, com o selo cinza "Contato";
- a automação não atua (o portão recusa sem lead, e "Devolver para a IA" é
  recusado com 409);
- ficam fora das métricas e do resumo diário, que medem prospecção;
- podem ser **vinculadas a um lead** pelo painel da conversa. Os leads com o
  mesmo telefone aparecem primeiro. Vincular não liga a IA: isso é um segundo
  clique, consciente;
- enviar um convite a um lead com o mesmo número **adota** a conversa existente
  em vez de abrir uma segunda.

## 5. Limitações da Coexistência (Meta)

Conferir a documentação atual da Meta, porque estas regras mudam:

- vazão fixa de 20 mensagens/s no número;
- desligados no app depois da conexão: mensagens temporárias, visualização
  única, localização em tempo real e listas de transmissão (estas ficam somente
  leitura);
- dispositivos conectados são desvinculados (até 4 podem ser religados;
  WhatsApp para Windows não é suportado);
- o celular precisa continuar em uso: cerca de 14 dias sem abrir o app
  desconecta (`PRIMARY_INACTIVITY`);
- mídia do histórico só chega se for dos últimos 14 dias;
- desconectar no LeadEnricher não desconecta na Meta: para cortar de vez, o
  dono desconecta no próprio app WhatsApp Business.

## 6. Versão do Embedded Signup

A Meta anunciou a descontinuação das versões v2 e v3 do Embedded Signup para
15/10/2026. A tela envia `featureType: whatsapp_business_app_onboarding` e
`sessionInfoVersion: "3"` (`conectarPelaMeta` em `static/js/app.js`). **Antes
de ligar em produção, confira na página de versões da Meta se a v4 mudou
esses parâmetros** e ajuste o objeto `extras` se for o caso. O servidor não
depende da versão: ele recebe `code`, `waba_id` e, quando vier,
`phone_number_id`.
