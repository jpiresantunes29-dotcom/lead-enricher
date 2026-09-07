"""Conteúdo editorial indexável (/guias).

Páginas de conteúdo existem por um motivo comercial: a landing só ranqueia para
quem já conhece a marca. Quem ainda não conhece busca pelo problema ("como
descobrir o e-mail de um decisor", "o que é registro MX"). Cada guia responde
uma dessas buscas e leva para o produto.

O corpo é HTML confiável escrito aqui (não vem de usuário) — o template o
imprime com `| safe`. As classes reaproveitam templates/legal_base.css.
"""

from __future__ import annotations

from dataclasses import dataclass

PUBLISHED = "2026-07-31"


@dataclass(frozen=True)
class Guide:
    slug: str
    title: str  # <h1> e headline do Article
    seo_title: str  # <title>
    description: str  # meta description e resumo no índice
    minutes: int
    body: str
    related: tuple[str, ...] = ()
    published: str = PUBLISHED
    updated: str = PUBLISHED

    @property
    def path(self) -> str:
        return f"/guias/{self.slug}"

    @property
    def updated_label(self) -> str:
        """`2026-07-31` → `julho de 2026` (o <time> mantém a data ISO)."""
        ano, mes, _ = self.updated.split("-")
        return f"{_MESES[int(mes) - 1]} de {ano}"


_MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


_CTA = """
<div class="lg-note">
  <strong>Atalho:</strong> o LeadEnricher faz esse levantamento sozinho — você digita o
  domínio e recebe infraestrutura, porte e decisores.
  <a href="/app">Analise um domínio grátis →</a>
</div>
"""


GUIDES: tuple[Guide, ...] = (
    Guide(
        slug="enriquecimento-de-leads-b2b",
        title="Enriquecimento de leads B2B: o guia prático para times comerciais",
        seo_title="Enriquecimento de leads B2B: guia prático | LeadEnricher",
        description=(
            "O que é enriquecimento de leads, quais dados realmente ajudam a vender, "
            "onde buscá-los de forma pública e como montar o processo sem comprar base."
        ),
        minutes=8,
        related=(
            "como-encontrar-o-decisor-de-uma-empresa",
            "registros-dns-para-vendas-b2b",
            "como-validar-email-corporativo",
        ),
        body="""
<h2>O que é enriquecimento de leads</h2>
<p>Enriquecer um lead é transformar um dado solto — um domínio, um nome de empresa, um
cartão recolhido em feira — no contexto necessário para decidir <em>se</em>, <em>quando</em>
e <em>como</em> abordar aquela empresa. Não é acumular campos: é reduzir o tempo entre
"apareceu um nome na lista" e "sei o que falar com quem".</p>
<p>Na prática, um lead enriquecido responde a quatro perguntas:</p>
<ul>
  <li><strong>Essa empresa tem o perfil que vendemos?</strong> Porte, setor, localização.</li>
  <li><strong>Ela tem maturidade para comprar?</strong> Sinais técnicos e de operação.</li>
  <li><strong>Quem decide?</strong> Cargo, nome e um canal de contato que funciona.</li>
  <li><strong>Vale a pena agora?</strong> Prioridade relativa aos outros leads da fila.</li>
</ul>

<h2>Quais dados realmente mudam a conversa</h2>
<p>A tentação é coletar tudo. Mas campo que ninguém usa na ligação é custo, não ativo.
O conjunto mínimo que sustenta uma abordagem consultiva:</p>
<ul>
  <li><strong>Identidade da empresa:</strong> razão social ou nome fantasia, domínio,
  site institucional e página no LinkedIn — o básico para não errar o alvo.</li>
  <li><strong>Porte:</strong> faixa de funcionários. Define o discurso, o ticket e quem
  atende o telefone.</li>
  <li><strong>Setor:</strong> permite reaproveitar casos de sucesso parecidos.</li>
  <li><strong>Infraestrutura de e-mail e hospedagem:</strong> conta muito sobre maturidade
  de TI e sobre quem já é fornecedor lá dentro. Detalhamos em
  <a href="/guias/registros-dns-para-vendas-b2b">registros DNS para vendas B2B</a>.</li>
  <li><strong>Decisores:</strong> nome, cargo e contato verificado. Ver
  <a href="/guias/como-encontrar-o-decisor-de-uma-empresa">como encontrar o decisor</a>.</li>
</ul>

<h2>De onde vêm os dados sem comprar base</h2>
<p>Praticamente tudo que importa está público e acessível por consulta direta:</p>
<ul>
  <li><strong>DNS</strong> — registros MX, SPF, DMARC, NS e o bloco de IP do hosting.
  É informação de infraestrutura, respondida por qualquer resolver.</li>
  <li><strong>Site institucional</strong> — descrição do negócio, produtos, endereço,
  telefone e, com frequência, a própria equipe de liderança.</li>
  <li><strong>Perfis profissionais públicos</strong> — página da empresa e perfis de
  pessoas, que trazem cargo e faixa de porte.</li>
  <li><strong>Buscadores</strong> — cruzamento de nome da empresa com cargo alvo.</li>
</ul>
<p>Bases compradas envelhecem: cargo muda, pessoa sai, empresa migra de provedor. Consulta
sob demanda devolve o estado de hoje — e é a diferença entre um e-mail que chega e um
bounce.</p>

<h2>LGPD: o que pode e o que não pode</h2>
<p>Prospecção B2B com dados profissionais públicos costuma se apoiar no <strong>legítimo
interesse</strong> (art. 7º, IX da LGPD), e não em consentimento prévio. Isso não é
carta branca — exige alguns cuidados concretos:</p>
<ul>
  <li>Tratar apenas dados de contexto profissional (cargo, e-mail corporativo, telefone
  comercial), nunca dados pessoais sensíveis ou de esfera privada.</li>
  <li>Identificar-se com clareza na abordagem e explicar como chegou até ali.</li>
  <li>Atender pedidos de descadastro e exclusão sem fricção.</li>
  <li>Manter registro da finalidade e política de retenção — dados que não servem mais
  ao processo devem ser eliminados ou anonimizados.</li>
</ul>
<p>Nossa aplicação desses princípios está em <a href="/privacidade">Política de
Privacidade</a>.</p>

<h2>Montando o processo em quatro etapas</h2>
<ul>
  <li><strong>1. Defina o ICP em critérios verificáveis.</strong> "Empresa média" não é
  critério; "50 a 500 funcionários, setor X, e-mail em nuvem" é.</li>
  <li><strong>2. Enriqueça antes de priorizar.</strong> Sem dado, priorização vira ordem
  alfabética.</li>
  <li><strong>3. Feche o ciclo no CRM.</strong> O dado precisa chegar onde o vendedor
  trabalha — exportação ou webhook — ou vira planilha paralela.</li>
</ul>

<h2>Erros que custam caro</h2>
<ul>
  <li><strong>Enriquecer a lista inteira de uma vez.</strong> Dado tem prazo de validade;
  enriqueça em lotes, perto do momento da abordagem.</li>
  <li><strong>Confiar em e-mail deduzido sem validar.</strong> Bounce alto derruba a
  reputação do domínio e afeta até os e-mails que dariam certo.</li>
  <li><strong>Ignorar o sinal de infraestrutura.</strong> Quem não tem SPF/DMARC
  configurado tem uma dor real — e uma conversa pronta.</li>
  <li><strong>Não registrar o motivo da priorização.</strong> Sem isso, não dá para
  aprender com o que converteu.</li>
</ul>
"""
        + _CTA,
    ),
    Guide(
        slug="como-encontrar-o-decisor-de-uma-empresa",
        title="Como encontrar o decisor de uma empresa (sem comprar base de dados)",
        seo_title="Como encontrar o decisor de uma empresa | LeadEnricher",
        description=(
            "Método em quatro passos para identificar quem decide a compra, achar o "
            "contato corporativo por fontes públicas e validar antes de enviar o primeiro "
            "e-mail."
        ),
        minutes=7,
        related=("como-validar-email-corporativo", "enriquecimento-de-leads-b2b"),
        body="""
<h2>Por que falar com a pessoa errada custa o dobro</h2>
<p>Abordagem que entra pelo nível operacional precisa ser vendida duas vezes: primeiro para
quem atendeu, depois — de segunda mão, sem você na sala — para quem assina. A cada
repasse a proposta perde nuance. Chegar direto em quem tem orçamento encurta o ciclo e
evita que sua mensagem seja resumida por alguém que não tem interesse no resultado.</p>

<h2>Passo 1 — Traduza o que você vende em um cargo</h2>
<p>O decisor não é sempre o cargo mais alto; é quem sente a dor e controla o orçamento
dela. Um mapeamento que funciona na maioria das vendas B2B:</p>
<ul>
  <li><strong>Software de infraestrutura, segurança ou dados:</strong> CTO, Diretor de TI,
  Gerente de Infraestrutura.</li>
  <li><strong>Ferramentas comerciais e marketing:</strong> Diretor Comercial, Head de
  Vendas, CMO.</li>
  <li><strong>Eficiência, custo e processos:</strong> COO, CFO, Diretor Administrativo.</li>
  <li><strong>Recrutamento, benefícios, cultura:</strong> Head de RH / Diretor de Gente.</li>
</ul>
<p>Em empresas de até ~50 pessoas, some o sócio-fundador à lista: nesse porte ele
costuma decidir tudo que passa de um valor pequeno.</p>

<h2>Passo 2 — Encontre a pessoa em fontes públicas</h2>
<ul>
  <li><strong>Página da empresa no LinkedIn</strong> → aba de pessoas, filtrando por cargo.
  Confirme que o perfil ainda lista a empresa como atual.</li>
  <li><strong>Busca aberta</strong> com o nome da empresa entre aspas somado ao cargo
  ("diretor de TI", "head de vendas"). Notícias, palestras e vagas publicadas
  frequentemente citam o nome de quem lidera a área.</li>
  <li><strong>Site institucional</strong> — páginas "quem somos", "liderança" e o próprio
  blog costumam ter nome e cargo.</li>
  <li><strong>Vagas abertas</strong> — o anúncio diz a quem a posição se reporta. É o
  organograma publicado de graça.</li>
</ul>

<h2>Passo 3 — Chegue ao contato corporativo</h2>
<p>Com nome e empresa, o e-mail quase sempre segue o padrão do domínio: <code>nome.sobrenome@</code>,
<code>inicial+sobrenome@</code> ou <code>nome@</code>. Descobrir qual padrão a empresa usa
— e confirmar que aquele endereço existe — é o que separa um contato útil de um bounce.
O processo completo está em
<a href="/guias/como-validar-email-corporativo">como validar um e-mail corporativo</a>.</p>
<p>Telefone comercial do site funciona melhor do que parece: peça pela área, não pela
pessoa. "Queria falar com quem cuida de infraestrutura" costuma render o nome que faltava.</p>

<h2>Passo 4 — Reúna contexto antes de escrever</h2>
<p>Nome e e-mail dão a porta; o contexto é o que faz abrirem. Antes do primeiro contato,
tenha à mão pelo menos um fato específico da empresa: um sinal técnico (o provedor de
e-mail que ela usa, um DMARC ausente), uma mudança recente (vaga aberta, expansão,
mudança de sede) ou um número de porte que justifique sua solução. Genérico não recebe
resposta.</p>

<h2>Erros comuns</h2>
<ul>
  <li><strong>Parar no primeiro nome encontrado.</strong> Mapeie dois ou três cargos: em
  compra B2B, raramente decide uma pessoa só.</li>
  <li><strong>Usar dado antigo.</strong> Cargo em base comprada com meses de idade tem
  chance real de já ter mudado.</li>
  <li><strong>Escrever para o contato genérico.</strong> Caixas <code>contato@</code> e
  <code>comercial@</code> raramente chegam à liderança.</li>
  <li><strong>Ignorar o registro.</strong> Sem anotar quem foi abordado e quando, o time
  repete o contato — e queima o lead.</li>
</ul>

<h2>Fazendo isso em escala, sem virar trabalho manual</h2>
<p>O método acima leva de 10 a 20 minutos por empresa. Multiplicado por uma lista de
prospecção, consome a semana de um SDR. É exatamente esse levantamento que o
LeadEnricher automatiza: a partir do domínio, ele busca decisores por cargo em fontes
públicas, monta o e-mail corporativo pelo padrão que aquele domínio já demonstrou e
entrega cada endereço com a nota de confiança correspondente.</p>
"""
        + _CTA,
    ),
    Guide(
        slug="registros-dns-para-vendas-b2b",
        title="MX, SPF e DMARC: o que os registros DNS revelam sobre um lead",
        seo_title="MX, SPF e DMARC: ler DNS para qualificar leads | LeadEnricher",
        description=(
            "Como interpretar registros MX, SPF, DMARC e hospedagem para estimar "
            "maturidade de TI, descobrir fornecedores já contratados e abrir conversa "
            "com um argumento técnico."
        ),
        minutes=6,
        related=("enriquecimento-de-leads-b2b", "como-encontrar-o-decisor-de-uma-empresa"),
        body="""
<h2>Por que um vendedor deveria olhar DNS</h2>
<p>O DNS de uma empresa é público, gratuito de consultar e difícil de maquiar. Ele diz
qual provedor de e-mail ela paga, onde o site está hospedado e quanto cuidado o time
técnico tem com a própria configuração. É o mais barato dos sinais de qualificação — e
o único que ninguém preenche em formulário.</p>

<h2>MX — quem entrega o e-mail da empresa</h2>
<p>O registro MX aponta os servidores que recebem e-mail do domínio. Na prática você
descobre o fornecedor:</p>
<ul>
  <li><strong>Google Workspace ou Microsoft 365:</strong> operação em nuvem, provavelmente
  com identidade centralizada. Empresa acostumada a pagar SaaS por usuário.</li>
  <li><strong>Servidor da própria empresa ou de hospedagem compartilhada:</strong>
  infraestrutura legada ou terceirizada em provedor pequeno — muitas vezes uma dor
  latente de confiabilidade.</li>
  <li><strong>Provedor de segurança de e-mail na frente do MX:</strong> há orçamento e
  preocupação com segurança; o interlocutor certo tende a ser mais técnico.</li>
</ul>

<h2>SPF — quem tem permissão de enviar em nome do domínio</h2>
<p>O SPF é um registro TXT que lista as origens autorizadas a enviar e-mail pelo domínio.
Além de indicar maturidade, ele denuncia o <em>stack</em>: é comum encontrar ali,
explicitamente, as ferramentas de marketing, CRM e disparo que a empresa já usa. Um SPF
ausente é sinal de que a empresa está sujeita a falsificação do próprio domínio.</p>

<h2>DMARC — a política de tratamento das falsificações</h2>
<p>O DMARC define o que fazer quando uma mensagem falha na verificação. Leia a política:</p>
<ul>
  <li><strong>Sem DMARC:</strong> nenhum controle. Terreno fértil para golpe de cobrança
  falsa em nome da empresa — e uma conversa de segurança pronta.</li>
  <li><strong><code>p=none</code>:</strong> só monitora. Alguém começou e parou no meio.</li>
  <li><strong><code>p=quarantine</code> ou <code>p=reject</code>:</strong> política de fato
  aplicada. Time técnico maduro; argumento genérico de segurança não vai colar.</li>
</ul>

<h2>Hospedagem e ASN — onde o site vive</h2>
<p>O IP do site resolve para um bloco pertencente a um provedor (o ASN). Descobrir se a
empresa está em nuvem pública, em datacenter nacional ou em hospedagem compartilhada
ajuda a estimar orçamento de TI e a antecipar quem já é fornecedor lá dentro.</p>

<h2>Transformando o sinal em abordagem</h2>
<ul>
  <li><strong>DMARC ausente + porte relevante</strong> → conversa sobre risco de
  falsificação do domínio, com evidência verificável na hora.</li>
  <li><strong>E-mail em nuvem</strong> → a empresa aceita modelo de assinatura; venda de
  SaaS enfrenta menos objeção de modelo.</li>
  <li><strong>Servidor próprio antigo</strong> → discussão de migração e continuidade.</li>
  <li><strong>Concorrente listado no SPF</strong> → você sabe contra quem está competindo
  antes da primeira reunião.</li>
</ul>
<div class="lg-note">
  <strong>Cuidado com a conclusão fácil:</strong> DNS mostra configuração, não intenção.
  Ele indica <em>com quem falar</em> e <em>sobre o quê</em> — a dor real ainda se confirma
  na conversa.
</div>

<h2>Consultando na prática</h2>
<p>Qualquer terminal resolve os três registros: <code>dig MX dominio.com.br</code>,
<code>dig TXT dominio.com.br</code> (procure a linha <code>v=spf1</code>) e
<code>dig TXT _dmarc.dominio.com.br</code>. No Windows, <code>nslookup -type=mx</code>.
Fazer isso a cada lead da lista, porém, não escala — o LeadEnricher resolve os quatro
sinais (MX, SPF, DMARC e hosting) automaticamente e já converte em pontuação.</p>
"""
        + _CTA,
    ),
    Guide(
        slug="como-validar-email-corporativo",
        title="Como validar um e-mail corporativo antes de enviar a primeira mensagem",
        seo_title="Como validar e-mail corporativo antes de enviar | LeadEnricher",
        description=(
            "Padrões de e-mail corporativo, verificação por SMTP, o problema do catch-all "
            "e por que bounce alto derruba a entregabilidade de toda a operação."
        ),
        minutes=6,
        related=("como-encontrar-o-decisor-de-uma-empresa", "enriquecimento-de-leads-b2b"),
        body="""
<h2>O custo de um endereço inválido</h2>
<p>Bounce não é só um e-mail perdido. Provedores usam a taxa de rejeição como sinal de
qualidade do remetente: uma lista suja derruba a reputação do domínio e passa a afetar
também as mensagens endereçadas corretamente — inclusive as comerciais que já
funcionavam. Validar antes é higiene da operação inteira, não capricho.</p>

<h2>Os padrões de e-mail corporativo</h2>
<p>Empresas raramente criam endereços aleatórios. A esmagadora maioria segue um padrão
único para todo o domínio:</p>
<ul>
  <li><code>nome.sobrenome@empresa.com.br</code> — o mais comum no Brasil.</li>
  <li><code>nome@empresa.com.br</code> — típico de empresas menores.</li>
  <li><code>inicial+sobrenome@empresa.com.br</code> — comum em multinacionais.</li>
  <li><code>nome_sobrenome@</code> ou <code>sobrenome.nome@</code> — menos frequentes.</li>
</ul>
<p>Descobrir o padrão de um domínio geralmente é fácil: um único e-mail público — de
imprensa, de vaga, do rodapé do site — já revela a regra que vale para todos.</p>

<h2>Como funciona a verificação por SMTP</h2>
<p>É possível perguntar ao servidor de e-mail se uma caixa existe sem enviar mensagem
alguma. O verificador abre uma conversa SMTP com o servidor apontado no MX do domínio,
declara um remetente e pergunta pelo destinatário (comando <code>RCPT TO</code>). O
servidor responde se aceitaria aquela entrega — e a conexão é encerrada antes de qualquer
conteúdo ser transmitido. Ninguém recebe nada.</p>

<h2>Os três resultados possíveis</h2>
<ul>
  <li><strong>Válido:</strong> o servidor confirma a caixa. Pode enviar.</li>
  <li><strong>Inválido:</strong> rejeição explícita. Descarte o endereço — não insista com
  variações no mesmo destinatário.</li>
  <li><strong>Indeterminado:</strong> o servidor não confirma nem nega. É o caso do
  <em>catch-all</em> e de provedores que bloqueiam essa checagem.</li>
</ul>

<h2>O problema do catch-all</h2>
<p>Domínios configurados como catch-all aceitam qualquer destinatário e só depois decidem
o destino da mensagem. Nesses casos, a verificação responde "aceito" para endereços que
não existem, e a única saída é reduzir o risco por outros meios: preferir o padrão
confirmado por um e-mail público conhecido, começar por um volume pequeno e observar as
respostas antes de escalar.</p>
<div class="lg-note">
  <strong>Regra prática:</strong> trate "indeterminado" como hipótese, não como contato
  confirmado. Envie, mas conte esse endereço separadamente ao medir a taxa de resposta.
</div>

<h2>Boas práticas de higiene</h2>
<ul>
  <li><strong>Valide perto do envio.</strong> Verificação de três meses atrás não vale
  nada: gente sai da empresa toda semana.</li>
  <li><strong>Aqueça o domínio.</strong> Volume que sobe de repente é sinal clássico de
  spam para os provedores.</li>
  <li><strong>Configure SPF, DKIM e DMARC no seu próprio domínio.</strong> Quem cobra
  configuração correta do lead precisa ter a sua em ordem.</li>
  <li><strong>Respeite o descadastro imediatamente</strong> e registre a solicitação — é
  exigência da LGPD e proteção da sua reputação.</li>
  <li><strong>Monitore a taxa de bounce</strong> por campanha. Alta e persistente, pare e
  limpe a lista antes de continuar.</li>
</ul>

<h2>Fazendo em escala</h2>
<p>O LeadEnricher aprende o padrão de e-mail de cada domínio a partir dos endereços já
confirmados, confirma por SMTP quando a rede permite a sondagem e marca explicitamente
os casos indeterminados — para que você saiba em qual contato está apostando.</p>
"""
        + _CTA,
    ),
)

BY_SLUG: dict[str, Guide] = {guide.slug: guide for guide in GUIDES}

INDEX_TITLE = "Guias de prospecção e enriquecimento de leads B2B"
INDEX_DESCRIPTION = (
    "Guias práticos sobre prospecção B2B: como encontrar decisores, ler sinais de DNS, "
    "validar e-mails corporativos e enriquecer leads com dados públicos."
)


def get(slug: str) -> Guide | None:
    return BY_SLUG.get(slug)


def related_of(guide: Guide) -> list[Guide]:
    return [BY_SLUG[slug] for slug in guide.related if slug in BY_SLUG]
