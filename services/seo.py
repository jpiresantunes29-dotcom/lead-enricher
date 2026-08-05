"""Metadados de SEO centralizados.

Uma única fonte de verdade para title/description/canonical/Open Graph/JSON-LD
de cada página pública, consumida por `main.py` (render dos templates),
`/robots.txt` e `/sitemap.xml`.

A URL canônica vem de `SITE_URL` (ex.: https://leadenricher.app). Sem essa
variável caímos na origem da própria requisição — funciona, mas o mesmo
conteúdo servido em domínios diferentes (apex + *.vercel.app) vira conteúdo
duplicado para o Google, então em produção `SITE_URL` deve estar definida.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from services import guides

BRAND = "LeadEnricher"
LOCALE = "pt_BR"
THEME_COLOR = "#1D4ED8"
OG_IMAGE_PATH = "/static/og-cover.png?v=1"
OG_IMAGE_ALT = (
    "Ficha de empresa do LeadEnricher: provedor de e-mail, porte, setor e decisores"
)
CONTACT_EMAIL = "contato@leadenricher.app"

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@dataclass(frozen=True)
class Page:
    """Uma página HTML pública (ou, com `indexable=False`, privada)."""

    path: str
    template: str
    title: str
    description: str
    og_title: str | None = None
    indexable: bool = True
    priority: str = "0.5"
    changefreq: str = "monthly"
    #: `lastmod` fixo (guias); sem ele, cai no mtime do template.
    lastmod: str | None = None

    @property
    def social_title(self) -> str:
        return self.og_title or self.title


PAGES: dict[str, Page] = {
    "/": Page(
        path="/",
        template="landing.html",
        title="LeadEnricher — enriquecimento de leads B2B a partir do domínio",
        og_title="Digite um domínio. Conheça a empresa inteira.",
        description=(
            "Digite o domínio e receba infraestrutura de e-mail, porte, setor, dados "
            "de CNPJ e decisores com e-mail corporativo em segundos. 5 análises "
            "grátis por mês, sem cartão."
        ),
        priority="1.0",
        changefreq="weekly",
    ),
    "/termos": Page(
        path="/termos",
        template="termos.html",
        title="Termos de Uso — LeadEnricher",
        description=(
            "Condições de uso do LeadEnricher: escopo do serviço, planos e cotas, "
            "cancelamento, uso aceitável dos dados e limitação de responsabilidade."
        ),
        priority="0.3",
        changefreq="yearly",
    ),
    "/privacidade": Page(
        path="/privacidade",
        template="privacidade.html",
        title="Política de Privacidade — LeadEnricher",
        description=(
            "Como o LeadEnricher trata dados pessoais sob a LGPD: quais dados "
            "coletamos, bases legais, retenção, subprocessadores e como exercer "
            "seus direitos de titular."
        ),
        priority="0.3",
        changefreq="yearly",
    ),
    "/seguranca": Page(
        path="/seguranca",
        template="seguranca.html",
        title="Segurança — LeadEnricher",
        description=(
            "Práticas de segurança do LeadEnricher: TLS obrigatório, autenticação "
            "sem senha via Supabase, criptografia em repouso, isolamento por "
            "usuário e webhooks assinados."
        ),
        priority="0.3",
        changefreq="yearly",
    ),
    "/remover-meus-dados": Page(
        path="/remover-meus-dados",
        template="remover-dados.html",
        title="Remover meus dados — LeadEnricher",
        description=(
            "Peça a remoção do seu e-mail profissional, telefone ou perfil da base "
            "do LeadEnricher. Sem cadastro, sem custo e com bloqueio permanente, "
            "conforme o art. 18 da LGPD."
        ),
        priority="0.4",
        changefreq="yearly",
    ),
    "/privacidade/confirmar": Page(
        path="/privacidade/confirmar",
        template="remover-dados-confirmado.html",
        title="Confirmação de remoção — LeadEnricher",
        description=(
            "Confirmação do pedido de remoção de dados feito em "
            "/remover-meus-dados."
        ),
        # Página de uso único, alcançada por link com token: não há o que
        # indexar e o buscador não deve tentar abrir o link de ninguém.
        indexable=False,
    ),
    "/guias": Page(
        path="/guias",
        template="guias.html",
        title=f"{guides.INDEX_TITLE} — LeadEnricher",
        og_title=guides.INDEX_TITLE,
        description=guides.INDEX_DESCRIPTION,
        priority="0.8",
        changefreq="monthly",
    ),
    "/app": Page(
        path="/app",
        template="index.html",
        title="Painel — LeadEnricher",
        description="Área logada do LeadEnricher: busca, pipeline, follow-ups e histórico.",
        indexable=False,
    ),
}


def _guide_page(guide: "guides.Guide") -> Page:
    """Cada guia vira uma Page — mesmo pipeline de canonical/OG/sitemap."""
    return Page(
        path=guide.path,
        template="guia.html",
        title=guide.seo_title,
        og_title=guide.title,
        description=guide.description,
        priority="0.7",
        changefreq="yearly",
        lastmod=guide.updated,
    )


def _all_pages() -> dict[str, Page]:
    return {**PAGES, **{g.path: _guide_page(g) for g in guides.GUIDES}}

#: Perguntas do FAQ da landing — renderizadas no HTML **e** no JSON-LD
#: (schema.org/FAQPage), para os dois nunca saírem de sincronia.
FAQ: list[tuple[str, str]] = [
    (
        "Como funciona a busca de decisores?",
        "Buscamos perfis públicos no LinkedIn, Google, Bing e DuckDuckGo filtrando "
        "por cargo na empresa. O e-mail corporativo é montado pelo padrão que o "
        "próprio domínio já demonstrou (por exemplo nome.sobrenome@), e cada "
        "endereço vem com nota de confiança. Quando a rede permite a sondagem SMTP, "
        "ela confirma a caixa e a nota sobe.",
    ),
    (
        "De onde vêm os dados?",
        "Só de fontes públicas: DNS ao vivo (MX, SPF, DMARC, DKIM), logs de "
        "certificado, RDAP do registro do domínio, o site da própria empresa, "
        "perfis públicos do LinkedIn e as bases abertas de CNPJ da Receita Federal. "
        "Cada dado guarda a origem e a data em que foi coletado.",
    ),
    (
        "Meus dados estão seguros?",
        "Sim. Autenticação sem senha via Supabase, tráfego em TLS e isolamento por "
        "usuário: você só enxerga os leads da sua conta. Não vendemos dados a "
        "terceiros. Quem for titular de um contato pode pedir a remoção em "
        "/remover-meus-dados, sem cadastro — o bloqueio é permanente e vale também "
        "para gravações futuras.",
    ),
    (
        "Posso integrar com meu CRM?",
        "Sim, por webhook assinado com HMAC-SHA256: cada lead (com decisores e "
        "atividades) é enviado ao endpoint que você configurar, o que cobre Zapier, "
        "Make, Power Automate ou um sistema próprio. Conectores nativos de HubSpot e "
        "Pipedrive estão no roadmap, ainda não disponíveis.",
    ),
    (
        "Como funciona a extensão para o LinkedIn?",
        "Você instala a extensão no Chrome ou Edge e conecta com um código gerado em "
        "Configurações. Nas páginas de perfil e de empresa ela mostra contato e dados "
        "de CNPJ, e salva o lead no seu pipeline. Cada revelação consome 1 crédito — "
        "5 no plano Free, 300 no Pro — e nada é cobrado quando não encontramos "
        "contato ou quando a mesma pessoa é revelada de novo em 90 dias.",
    ),
    (
        "E se a empresa não tiver website?",
        "A busca por domínio ainda valida DNS, o e-mail corporativo (via DNS) e os "
        "decisores via LinkedIn e buscadores. Sempre há alguma trilha.",
    ),
    (
        "Como funciona a cota de análises?",
        "Cada domínio novo analisado consome uma unidade da cota. Reabrir um lead já "
        "analisado nos últimos 7 dias vem do cache e não consome nada. A cota renova "
        "a cada ciclo de 30 dias e não é cumulativa.",
    ),
    (
        "Posso cancelar quando quiser?",
        "Sim. O cancelamento é feito pelo portal de assinatura em Configurações, sem "
        "contato com vendas, e vale até o fim do ciclo já pago. Não há multa nem "
        "fidelidade.",
    ),
    (
        "Emitem nota fiscal para empresa?",
        "Sim. A cobrança é processada pela Stripe e o recibo fiscal é enviado por "
        "e-mail a cada ciclo. Para faturamento com CNPJ, dados de cobrança e "
        "condições específicas, fale com o time comercial.",
    ),
]


# ── URL base / canonical ─────────────────────────────────────────────────────
def site_url(request) -> str:
    """Origem canônica do site, sem barra final."""
    configured = (os.getenv("SITE_URL") or "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


def absolute(request, path: str) -> str:
    return f"{site_url(request)}{path}"


def last_modified(page: Page) -> str:
    """`lastmod` do sitemap — data fixa da página ou mtime do template."""
    if page.lastmod:
        return page.lastmod
    try:
        mtime = (_TEMPLATES_DIR / page.template).stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc).date().isoformat()
    except OSError:
        return date.today().isoformat()


# ── JSON-LD ──────────────────────────────────────────────────────────────────
def _organization(base: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": f"{base}/#organization",
        "name": BRAND,
        "url": f"{base}/",
        "logo": f"{base}{OG_IMAGE_PATH}",
        "description": (
            "Plataforma brasileira de inteligência comercial B2B que enriquece leads "
            "a partir do domínio da empresa."
        ),
        "email": CONTACT_EMAIL,
        "areaServed": "BR",
        "contactPoint": [
            {
                "@type": "ContactPoint",
                "contactType": "sales",
                "email": CONTACT_EMAIL,
                "availableLanguage": ["Portuguese"],
            }
        ],
    }


def _website(base: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": f"{base}/#website",
        "url": f"{base}/",
        "name": BRAND,
        "inLanguage": "pt-BR",
        "publisher": {"@id": f"{base}/#organization"},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{base}/app?domain={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    }


def _software_application(base: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "@id": f"{base}/#software",
        "name": BRAND,
        "url": f"{base}/",
        "applicationCategory": "BusinessApplication",
        "applicationSubCategory": "Sales Intelligence",
        "operatingSystem": "Web",
        "inLanguage": "pt-BR",
        "description": PAGES["/"].description,
        "screenshot": f"{base}{OG_IMAGE_PATH}",
        "publisher": {"@id": f"{base}/#organization"},
        "featureList": [
            "Relatório DNS completo (MX, NS, SPF, DMARC, DKIM, hosts, ASN e RDAP)",
            "Decisores por cargo com e-mail corporativo e nota de confiança",
            "Dados públicos de CNPJ: razão social, porte, telefone e quadro societário",
            "Extensão de navegador para LinkedIn com créditos de revelação",
            "Follow-ups automáticos, pipeline kanban e dashboard comercial",
            "Exportação XLSX/CSV e envio para CRM por webhook assinado",
        ],
        "offers": [
            {
                "@type": "Offer",
                "name": "Free",
                "price": "0",
                "priceCurrency": "BRL",
                "description": (
                    "5 análises de empresa por mês, ficha completa com relatório DNS "
                    "e 5 revelações de contato na extensão."
                ),
                "url": f"{base}/app",
            },
            {
                "@type": "Offer",
                "name": "Pro",
                "price": "97",
                "priceCurrency": "BRL",
                "description": (
                    "500 análises por mês, 300 revelações de contato, exportação "
                    "XLSX/CSV, webhook para CRM e resumo executivo com IA."
                ),
                "url": f"{base}/app",
            },
        ],
    }


def _faq_page(base: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "@id": f"{base}/#faq",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for question, answer in FAQ
        ],
    }


def _breadcrumb(base: str, trilha: list[tuple[str, str]]) -> dict:
    """`trilha`: pares (nome, path), da home até a página atual."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": posicao,
                "name": nome,
                "item": f"{base}{path}",
            }
            for posicao, (nome, path) in enumerate(trilha, start=1)
        ],
    }


def _guides_collection(base: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "@id": f"{base}/guias#collection",
        "name": guides.INDEX_TITLE,
        "description": guides.INDEX_DESCRIPTION,
        "url": f"{base}/guias",
        "inLanguage": "pt-BR",
        "isPartOf": {"@id": f"{base}/#website"},
        "hasPart": [
            {
                "@type": "Article",
                "headline": guide.title,
                "description": guide.description,
                "url": f"{base}{guide.path}",
            }
            for guide in guides.GUIDES
        ],
    }


def _article(base: str, guide: "guides.Guide") -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "@id": f"{base}{guide.path}#article",
        "headline": guide.title,
        "description": guide.description,
        "url": f"{base}{guide.path}",
        "inLanguage": "pt-BR",
        "datePublished": guide.published,
        "dateModified": guide.updated,
        "author": {"@id": f"{base}/#organization"},
        "publisher": {"@id": f"{base}/#organization"},
        "image": f"{base}{OG_IMAGE_PATH}",
        "isPartOf": {"@id": f"{base}/#website"},
        "mainEntityOfPage": {"@type": "WebPage", "@id": f"{base}{guide.path}"},
    }


def _json_ld(base: str, page: Page) -> list[dict]:
    if not page.indexable:
        return []
    if page.path == "/":
        return [
            _organization(base),
            _website(base),
            _software_application(base),
            _faq_page(base),
        ]

    inicio = ("Início", "/")
    if page.path == "/guias":
        return [
            _guides_collection(base),
            _breadcrumb(base, [inicio, ("Guias", "/guias")]),
        ]

    guide = guides.get(page.path.removeprefix("/guias/"))
    if guide:
        return [
            _organization(base),
            _article(base, guide),
            _breadcrumb(
                base, [inicio, ("Guias", "/guias"), (guide.title, guide.path)]
            ),
        ]

    return [_breadcrumb(base, [inicio, (page.title.split(" — ")[0], page.path)])]


# ── Contexto para os templates ───────────────────────────────────────────────
def context(request, path: str) -> dict:
    """Bloco `seo` consumido por `templates/_seo_head.html`."""
    page = _all_pages()[path]
    base = site_url(request)
    return {
        "seo": {
            "title": page.title,
            "og_title": page.social_title,
            "description": page.description,
            "canonical": f"{base}{page.path}",
            "indexable": page.indexable,
            "image": f"{base}{OG_IMAGE_PATH}",
            "image_alt": OG_IMAGE_ALT,
            "site_name": BRAND,
            "locale": LOCALE,
            "theme_color": THEME_COLOR,
            "json_ld": _json_ld(base, page),
        }
    }


def indexable_pages() -> list[Page]:
    return [p for p in _all_pages().values() if p.indexable]
