# Documentação do LeadEnricher

Índice do que existe aqui e qual é o estado de cada documento. Os arquivos
históricos ficam no lugar porque há comentários no código que os citam por
caminho e seção (`services/activity_rules.py`, `services/ai_insights.py`,
`static/landing/landing.js`, `main.py`).

| Documento | Estado | Do que trata |
|---|---|---|
| [ROADMAP_FUNCIONALIDADES.md](ROADMAP_FUNCIONALIDADES.md) | **Vigente** | O que vem a seguir, em ordem, com esforço estimado |
| [AUDITORIA_2026-08.md](AUDITORIA_2026-08.md) | **Vigente** | Falhas encontradas na auditoria de agosto/2026 e como cada uma foi fechada |
| [AUDITORIA_ORGANIZACAO_2026-09.md](AUDITORIA_ORGANIZACAO_2026-09.md) | **Vigente** | Check-up de arquivos de documentação: organização, duplicações, desatualização |
| [PLANO_WHATSAPP_E_DYNAMICS.md](PLANO_WHATSAPP_E_DYNAMICS.md) | **Vigente** | Planejamento técnico do agente de WhatsApp: decisões, fases, arquitetura (atualizado com nota sobre mudanças 2026-09-05) |
| [DOCUMENTACAO_IA_COMPLETA.md](DOCUMENTACAO_IA_COMPLETA.md) | **Vigente** | Manual de referência da IA: setup, troubleshooting, variáveis, exemplos (atualizado: horários removidos, 24h vs 72h corrigido) |
| [PRODUCAO.md](PRODUCAO.md) | **Vigente** | Passo a passo da virada para produção: banco, crons, WhatsApp, IA |
| [CONTACT_INTELLIGENCE.md](CONTACT_INTELLIGENCE.md) | **Vigente** | Banco de contatos próprio, padrão de e-mail por domínio e extensão |
| [LUSHA_PROSPECTING_IMPLEMENTACAO.md](LUSHA_PROSPECTING_IMPLEMENTACAO.md) | **Vigente** | Integração Lusha (BYOA): listar contatos de uma empresa e revelar sob clique. Traz o custo por ação e o que ainda não foi verificado contra a API real |
| [MIGRACOES.md](MIGRACOES.md) | **Vigente** | Como mudar o schema: Alembic, ambientes e comandos |
| [FILA_E_LOTE.md](FILA_E_LOTE.md) | **Vigente** | Como o lote roda sem estourar o tempo da função, e quem empurra a fila |
| [ANALISE_VIDEO_AGENTE_WHATSAPP.md](ANALISE_VIDEO_AGENTE_WHATSAPP.md) | Histórico (decisões arquivadas) | Análise de ideias de um vídeo externo contra o agente vigente; 6 itens de backlog absorvidos em **Fase 1.6–1.11 do ROADMAP** |
| [PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md](PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md) | Histórico (implementado) | Proposta que originou pipeline, atividades, dashboard, CRM e IA |
| [PLANO_REDESIGN_CORPORATIVO.md](PLANO_REDESIGN_CORPORATIVO.md) | Histórico (implementado) | Plano do redesenho do app |
| [DESIGN_LANDING_V3.md](DESIGN_LANDING_V3.md) | Histórico (implementado) | Especificação visual da landing e das animações |

## Onde olhar primeiro

- **Entender o produto:** `CONTACT_INTELLIGENCE.md` → como o dado entra, é
  aprendido e é revelado, tudo de graça.
- **Mexer na integração paga:** `LUSHA_PROSPECTING_IMPLEMENTACAO.md` → leia a
  §0 (estado de cada fase) e a §11 (o que ficou aberto) antes de escrever
  código de rede. A §9 tem os cinco princípios que não podem ser quebrados —
  o primeiro é nunca gastar crédito do usuário sem clique dele.
- **Retomar o trabalho:** `ROADMAP_FUNCIONALIDADES.md` → Fase 0 primeiro.
- **Entender o agente de WhatsApp:** `PLANO_WHATSAPP_E_DYNAMICS.md` (decisões
  arquiteturais fase-a-fase) + `DOCUMENTACAO_IA_COMPLETA.md` (manual de
  operação).
- **Entender uma decisão de segurança:** `AUDITORIA_2026-08.md` → cada trava
  tem o motivo e o teste que a protege.
- **Virar para produção:** `PRODUCAO.md` → passo a passo exato de como ligar
  banco, crons, WhatsApp e IA.
