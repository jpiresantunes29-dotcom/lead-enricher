"""
Contact Intelligence — camada de pessoas.

Módulos:
  identity        normalização de nome/cargo/empresa e chave de deduplicação
  email_patterns  aprende o formato de e-mail de cada domínio (custo zero)
  optout          bloqueio LGPD por hash
  repository      upsert de Company/Person/e-mails/telefones
  waterfall       cascata de fontes, da mais barata para a mais cara
"""
