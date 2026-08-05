# Documentação do LeadEnricher

Índice do que existe aqui e qual é o estado de cada documento. Os arquivos
históricos ficam no lugar porque há comentários no código que os citam por
caminho e seção (`services/activity_rules.py`, `services/ai_insights.py`,
`static/landing/landing.js`, `main.py`).

| Documento | Estado | Do que trata |
|---|---|---|
| [ROADMAP_FUNCIONALIDADES.md](ROADMAP_FUNCIONALIDADES.md) | **Vigente** | O que vem a seguir, em ordem, com esforço estimado |
| [AUDITORIA_2026-08.md](AUDITORIA_2026-08.md) | **Vigente** | Falhas encontradas na auditoria de agosto/2026 e como cada uma foi fechada |
| [CONTACT_INTELLIGENCE.md](CONTACT_INTELLIGENCE.md) | **Vigente** | Banco de contatos próprio, padrão de e-mail por domínio, créditos e extensão |
| [PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md](PROPOSTA_V3_PROSPECCAO_INTELIGENTE.md) | Histórico (implementado) | Proposta que originou pipeline, atividades, dashboard, CRM e IA |
| [PLANO_REDESIGN_CORPORATIVO.md](PLANO_REDESIGN_CORPORATIVO.md) | Histórico (implementado) | Plano do redesenho do app |
| [DESIGN_LANDING_V3.md](DESIGN_LANDING_V3.md) | Histórico (implementado) | Especificação visual da landing e das animações |

## Onde olhar primeiro

- **Entender o produto:** `CONTACT_INTELLIGENCE.md` → como o dado entra, é
  aprendido e é revelado.
- **Retomar o trabalho:** `ROADMAP_FUNCIONALIDADES.md` → Fase 0 primeiro.
- **Entender uma decisão de segurança:** `AUDITORIA_2026-08.md` → cada trava
  tem o motivo e o teste que a protege.
