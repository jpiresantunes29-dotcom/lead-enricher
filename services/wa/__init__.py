"""
Contato por WhatsApp.

Módulos:
  gate     o portão: a única função que autoriza uma mensagem a sair
  states   transições de estado da conversa (pausar, assumir, encerrar)
  webhook  validação da assinatura da Meta

A divisão de responsabilidade que sustenta tudo: o modelo de linguagem
classifica e redige; quem decide se a mensagem sai é código determinístico,
aqui dentro. Regra crítica nunca mora num prompt.
"""
