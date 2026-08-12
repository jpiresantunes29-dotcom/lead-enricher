"""
Prontidão para produção: o que precisa estar no lugar antes de ligar.

Existe porque a lista de coisas que precisam estar certas ficou grande demais
para caber na cabeça de alguém às onze da noite de uma sexta. Cada item traz
**o que acontece se estiver faltando** — uma checagem que diz só "faltando" faz
a pessoa conferir a variável e seguir sem entender o risco que correu.

Três severidades, e a diferença é o que se faz com elas:

  `impede`   sem isto o produto não funciona ou funciona errado. Não ligue.
  `perigoso` funciona, mas de um jeito que custa caro se der errado — meia
             configuração do WhatsApp, demo aberto em produção.
  `atencao`  funciona; alguma capacidade fica desligada, e é bom saber qual.

Não decide nada sozinho: é chamado pelo boot (que falha em `impede`) e pela
rota de conferência, para a pessoa ler antes da virada.
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional

from middleware.auth import (
    demo_mode_enabled,
    jwt_configured,
    secret_confere_com_projeto,
)
from models.database import ALEMBIC_HEAD, SessionLocal, schema_status
from services import ai_insights, crypto
from services.wa import brain, client as wa_client, webhook as wa_webhook

IMPEDE = "impede"
PERIGOSO = "perigoso"
ATENCAO = "atencao"

_ORDEM = {IMPEDE: 0, PERIGOSO: 1, ATENCAO: 2}


@dataclass
class Achado:
    severidade: str
    titulo: str
    consequencia: str          # o que acontece se ficar assim
    como_resolver: str


@dataclass
class Relatorio:
    producao: bool
    achados: List[Achado] = field(default_factory=list)

    @property
    def pronto(self) -> bool:
        """Pronto = nada que impeça. `perigoso` é decisão de quem liga."""
        return not any(a.severidade == IMPEDE for a in self.achados)

    @property
    def bloqueios(self) -> List[Achado]:
        return [a for a in self.achados if a.severidade == IMPEDE]

    def como_dict(self) -> dict:
        return {
            "pronto": self.pronto,
            "producao": self.producao,
            "achados": [
                {"severidade": a.severidade, "titulo": a.titulo,
                 "consequencia": a.consequencia, "como_resolver": a.como_resolver}
                for a in sorted(self.achados, key=lambda a: _ORDEM.get(a.severidade, 9))
            ],
        }


def _env(nome: str) -> str:
    return (os.getenv(nome) or "").strip()


def _checar_fundacao(rel: Relatorio) -> None:
    """Banco, autenticação e modo de operação."""
    schema = schema_status()
    if not schema.get("ok"):
        faltando = ", ".join(
            schema.get("missing_tables", [])
            + list(schema.get("missing_columns", {}).keys())
        ) or schema.get("error", "desconhecido")
        rel.achados.append(Achado(
            IMPEDE, "O banco não tem o schema que o código espera",
            f"Cada clique que tocar o que falta responde 500. Ausente: {faltando}.",
            f"Rode `alembic upgrade head` no banco de produção "
            f"(a revisão atual é {ALEMBIC_HEAD}).",
        ))

    if not jwt_configured():
        rel.achados.append(Achado(
            IMPEDE, "SUPABASE_JWT_SECRET ausente ou curto demais",
            "Nenhum login real é aceito: as rotas autenticadas respondem 503.",
            "Copie o JWT Secret em Supabase > Settings > API.",
        ))
    elif secret_confere_com_projeto() is False:
        rel.achados.append(Achado(
            IMPEDE, "SUPABASE_JWT_SECRET é de outro projeto Supabase",
            "O login pelo Google termina bem e mesmo assim o app volta "
            "deslogado: o token que o Supabase emite não passa na verificação "
            "e toda rota autenticada responde 401. Só o modo demo funciona.",
            "Pegue o JWT Secret do MESMO projeto cuja anon key está em "
            "static/js/app.js (Supabase > Settings > API > JWT Settings) e "
            "atualize a variável no ambiente do deploy.",
        ))

    if rel.producao and demo_mode_enabled():
        rel.achados.append(Achado(
            PERIGOSO, "DEMO_MODE ligado em produção",
            "Qualquer visitante recebe uma conta de demonstração no plano Pro, "
            "sem cadastro — inclusive quem só achou o endereço.",
            "Defina DEMO_MODE=0.",
        ))

    if not _env("CRON_SECRET"):
        rel.achados.append(Achado(
            IMPEDE, "CRON_SECRET ausente",
            "As rotas internas respondem 503, então a fila de análise não anda "
            "e as conversas que caírem na madrugada nunca são retomadas.",
            'Gere com: python -c "import secrets; print(secrets.token_urlsafe(32))"',
        ))

    if rel.producao and _env("DATABASE_URL").startswith("sqlite"):
        rel.achados.append(Achado(
            IMPEDE, "Produção apontando para SQLite",
            "O disco da função serverless é efêmero: os dados somem a cada "
            "deploy, e leituras simultâneas travam.",
            "Aponte DATABASE_URL para o Postgres do Supabase.",
        ))


def _checar_whatsapp(rel: Relatorio) -> None:
    """
    O caso perigoso não é "desligado" — é **meio ligado**.

    Com envio configurado e `WHATSAPP_APP_SECRET` ausente, o produto manda
    mensagem e não consegue provar que o que volta é mesmo da Meta. O webhook
    recusa tudo (falha fechada, como deve), então o efeito prático é: o lead
    responde e ninguém nunca fica sabendo. Convite pago, resposta perdida.
    """
    envia = wa_client.is_configured()
    assina = wa_webhook.is_configured()

    if not envia and not assina:
        rel.achados.append(Achado(
            ATENCAO, "WhatsApp desligado",
            "A aba Conversas fica visível e explicando o que falta; nenhum "
            "convite pode ser enviado.",
            "Configure as variáveis WHATSAPP_* quando o WABA estiver aprovado.",
        ))
        return

    if envia and not assina:
        rel.achados.append(Achado(
            PERIGOSO, "WhatsApp envia, mas não valida o que recebe",
            "O convite sai e é cobrado; quando o lead responder, o webhook "
            "recusa a entrega por falta de assinatura e a resposta se perde.",
            "Defina WHATSAPP_APP_SECRET e WHATSAPP_VERIFY_TOKEN, e cadastre a "
            "URL do webhook no painel da Meta.",
        ))
    elif assina and not envia:
        rel.achados.append(Achado(
            ATENCAO, "WhatsApp recebe, mas não envia",
            "Nenhum primeiro contato pode ser iniciado; /api/wa/start responde "
            f"503 pedindo: {', '.join(wa_client.missing_config())}.",
            "Defina WHATSAPP_PHONE_NUMBER_ID e WHATSAPP_ACCESS_TOKEN.",
        ))

    if envia and not wa_client.template_name():
        rel.achados.append(Achado(
            PERIGOSO, "Nenhum template de abertura configurado",
            "O botão de iniciar contato responde erro: sem template aprovado a "
            "Meta não deixa abrir conversa.",
            "Aprove um template na Meta e defina WHATSAPP_TEMPLATE_NAME.",
        ))

    if not _env("WHATSAPP_VERIFY_TOKEN") and envia:
        rel.achados.append(Achado(
            ATENCAO, "WHATSAPP_VERIFY_TOKEN ausente",
            "O cadastro da URL do webhook no painel da Meta vai falhar no "
            "handshake.",
            "Defina qualquer segredo e use o mesmo valor no painel da Meta.",
        ))

    if envia and not brain.is_configured():
        rel.achados.append(Achado(
            ATENCAO, "IA desligada com WhatsApp ligado",
            "Toda resposta de lead vira pendência humana com o motivo escrito. "
            "Não se perde nada, mas ninguém responde sozinho.",
            "Defina ANTHROPIC_API_KEY para a automação responder.",
        ))


def _checar_segredos(rel: Relatorio, db) -> None:
    """
    As credenciais de CRM estão cifradas no banco?

    A severidade olha para o que existe gravado, não só para a variável: sem
    nenhuma conexão configurada, `SECRETS_KEY` ausente é um aviso sobre o
    futuro; com segredo real em claro, é exposição que já aconteceu — quem
    tiver uma cópia do banco assina payload como se fosse a gente.
    """
    try:
        inv = crypto.inventario(db)
    except Exception:   # banco fora do ar já é reportado por _checar_fundacao
        return

    if not crypto.chave_configurada():
        em_claro = inv["em_claro"]
        rel.achados.append(Achado(
            (PERIGOSO if (em_claro or rel.producao) else ATENCAO),
            "SECRETS_KEY ausente: credenciais de CRM gravadas em claro",
            (f"{em_claro} segredo(s) já gravado(s) legível(is) para quem "
             "tiver uma cópia do banco — o do webhook assina o que chega no "
             "CRM do usuário." if em_claro else
             "Os próximos tokens e segredos de CRM serão gravados em claro."),
            'Gere com: python -c "import secrets; print(secrets.token_urlsafe(32))" '
            "e depois rode scripts/recriptografar_segredos.py.",
        ))
        return

    if inv["em_claro"]:
        rel.achados.append(Achado(
            ATENCAO, f"{inv['em_claro']} segredo(s) de CRM ainda em claro",
            "São de antes da chave existir. Continuam funcionando, mas seguem "
            "legíveis em qualquer cópia do banco.",
            "Rode scripts/recriptografar_segredos.py (idempotente).",
        ))

    if inv["ilegiveis"]:
        rel.achados.append(Achado(
            PERIGOSO, f"{inv['ilegiveis']} segredo(s) não abrem com a SECRETS_KEY atual",
            "O push para o CRM dessas conexões responde 409 em vez de enviar "
            "sem assinatura. A chave foi trocada ou perdida.",
            "Volte a SECRETS_KEY anterior, ou peça ao usuário para regravar a "
            "conexão em Configurações.",
        ))


def _checar_opcionais(rel: Relatorio) -> None:
    """Capacidades que ficam desligadas em silêncio se ninguém conferir."""
    if not ai_insights.is_configured():
        rel.achados.append(Achado(
            ATENCAO, "Resumo executivo por IA desligado",
            "Os endpoints de resumo e roteiro de ligação respondem 503.",
            "Defina ANTHROPIC_API_KEY.",
        ))

    if not _env("RESEND_API_KEY"):
        rel.achados.append(Achado(
            PERIGOSO if rel.producao else ATENCAO,
            "E-mail transacional desligado",
            "O pedido de remoção (LGPD) fica pendente e o link de confirmação "
            "só aparece no log do servidor — o titular nunca recebe, e o "
            "bloqueio nunca se confirma.",
            "Defina RESEND_API_KEY e MAIL_FROM.",
        ))

    if rel.producao and not _env("SITE_URL"):
        rel.achados.append(Achado(
            ATENCAO, "SITE_URL ausente",
            "As URLs canônicas saem da origem da requisição, o que gera "
            "conteúdo duplicado entre o domínio próprio e o *.vercel.app.",
            "Defina SITE_URL com o domínio final, sem barra no fim.",
        ))


def verificar(producao: Optional[bool] = None, db=None) -> Relatorio:
    """
    Roda todas as conferências e devolve o relatório.

    `db` é opcional porque o boot roda antes de existir requisição; quando a
    rota chama, ela passa a própria sessão — é o que faz a conferência olhar
    para o mesmo banco que o resto do processo, e não para o que a variável de
    ambiente apontava quando o módulo carregou.
    """
    if producao is None:
        producao = (os.getenv("APP_ENV") or "").lower() == "production"
    rel = Relatorio(producao=producao)
    _checar_fundacao(rel)
    _checar_whatsapp(rel)

    propria = db is None
    if propria:
        db = SessionLocal()
    try:
        _checar_segredos(rel, db)
    finally:
        if propria:
            db.close()

    _checar_opcionais(rel)
    return rel


def resumo_para_log(rel: Relatorio) -> str:
    """Uma linha por achado, para o log do boot."""
    if not rel.achados:
        return "Tudo conferido: nada pendente."
    return "\n".join(
        f"  [{a.severidade}] {a.titulo} — {a.consequencia}" for a in
        sorted(rel.achados, key=lambda a: _ORDEM.get(a.severidade, 9))
    )
