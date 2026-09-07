"""
WhatsApp: recebimento (webhook da Meta) e abertura de conversa.

Duas rotas com naturezas opostas:

`/api/wa/webhook` é **público** — quem chama é a Meta, não um usuário logado.
A única prova de identidade é a assinatura HMAC do corpo. Ele precisa
responder rápido e responder 200: a Meta reentrega o que não confirmou, e uma
lentidão nossa vira mensagem duplicada.

`/api/wa/start` é **autenticado e caro** — dispara o template de abertura, que
a Meta cobra por mensagem. Passa pelo portão, é disparado por gente e nunca
por laço ou cron.
"""
import logging
import re
from collections import Counter
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from models.database import (
    AI_ACTIVE, AI_PAUSED, HUMAN_HANDOFF, STOPPED,
    AUDIT_ASSUMIDA, AUDIT_RELACIONAMENTO,
    RELATIONSHIP_CUSTOMER, RELATIONSHIP_DO_NOT_CONTACT,
    Activity, AuditLog, Conversation, DecisionMaker, Lead, WaMessage,
    WhatsAppConnection, get_db, utcnow,
)
from models.schemas import (
    AgendamentoOut, AuditEntryOut, ConversationAction, ConversationCard,
    ConversationDetail, ConversationOut, ConversationSeal, WaMessageOut,
    WaMetrics, WaReplyRequest, WaStartRequest, WaStartResponse,
)
from services.people import optout
from services.phone_normalizer import normalize_input
from services.wa import client, credenciais, gate, orchestrator, states, webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wa", tags=["whatsapp"])

_SO_DIGITOS = re.compile(r"\D")


def _e164(bruto: Optional[str]) -> Optional[str]:
    """
    Número no formato que a Meta usa e que o banco guarda.

    A Meta manda `5511988887777`, sem o `+`; o usuário digita
    `(11) 98888-7777`. Os dois precisam virar a mesma string, senão a mensagem
    que chega não encontra a conversa que existe.
    """
    if not bruto or not bruto.strip():
        return None
    bruto = bruto.strip()

    # Sem `+`, um número só de dígitos é ambíguo: `5511988887777` é o formato
    # da Meta (com país), mas `11988887777` é o que se digita no Brasil.
    # Tenta primeiro como internacional e cai para a região padrão.
    if not bruto.startswith("+"):
        digitos = _SO_DIGITOS.sub("", bruto)
        dados = normalize_input("+" + digitos) if digitos else None
        if dados:
            return dados["e164"]

    dados = normalize_input(bruto)
    return dados["e164"] if dados else None


# ── Recebimento ──────────────────────────────────────────────────────────────

@router.get("/webhook", include_in_schema=False)
def verificar_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    db: Session = Depends(get_db),
):
    """
    Handshake que a Meta faz uma vez, ao cadastrar a URL.

    Ela manda um desafio e espera receber o mesmo valor de volta, em texto
    puro, se o token bater.
    """
    # O GET não diz de qual número é, então vale qualquer token cadastrado:
    # cada conta aponta esta mesma URL com o token dela.
    tokens = [
        c.verify_token for c in
        db.query(WhatsAppConnection.verify_token)
        .filter(WhatsAppConnection.is_active.is_(True))
        .all()
    ]
    if hub_mode == "subscribe" and webhook.check_verify_token(hub_verify_token, tokens):
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificação inválido.")


@router.post("/webhook", include_in_schema=False)
async def receber_webhook(
    request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
):
    """
    Recebe mensagens e confirmações de entrega.

    Responde 200 mesmo quando não há nada para fazer: para a Meta, qualquer
    outra resposta significa "não entreguei" e ela tenta de novo — e a
    reentrega da mesma mensagem custaria uma resposta repetida ao lead.

    O turno (IA + envio pela Meta) NÃO roda aqui dentro: são chamadas de rede
    de vários segundos, e rodá-las antes de responder deixaria este processo
    ocupado com um lead só enquanto outras requisições esperam. A gravação da
    mensagem já está commitada neste ponto; o turno vira tarefa de segundo
    plano (`_responder_em_segundo_plano`), com sessão própria, e se ele não
    chegar a rodar (deploy, reinício), o cron `/api/internal/wa/pending`
    responde na rodada seguinte — a mesma rede de segurança que já existia
    para a função serverless ser interrompida no meio.
    """
    corpo = await request.body()
    assinatura = request.headers.get(webhook.SIGNATURE_HEADER)

    # De quem é este webhook. Lido do corpo ainda não confiável e usado só
    # para escolher contra qual App Secret a assinatura será conferida — a
    # explicação está em webhook.phone_number_id_do_corpo.
    pnid = webhook.phone_number_id_do_corpo(corpo)
    dono, cred = credenciais.por_phone_number_id(db, pnid)

    if not webhook.verify_signature(corpo, assinatura, secret=cred.app_secret or None):
        # 403 de propósito: requisição sem assinatura válida não é a Meta, e
        # não queremos que ela seja reentregue.
        raise HTTPException(status_code=403, detail="Assinatura inválida.")

    try:
        dados = await request.json()
    except Exception:
        logger.warning("Webhook com corpo que não é JSON.")
        return {"ok": True, "mensagens": 0}

    try:
        conversas = _processar(db, dados, dono)
        db.commit()
    except Exception:
        db.rollback()
        # Erro nosso não pode virar reentrega infinita da Meta. Fica no log,
        # a mensagem se perde, e é isso — melhor do que um laço de retentativas
        # respondendo ao lead várias vezes.
        logger.exception("Falha ao processar webhook do WhatsApp.")
        conversas = []

    if conversas:
        # Mesma fábrica de sessão que esta requisição usou para `db` — a real
        # `get_db` em produção, a que os testes põem no lugar dela em teste —
        # para a tarefa de segundo plano abrir a sua própria, já fechada
        # quando a resposta HTTP é enviada e esta função é chamada.
        fabrica_sessao = request.app.dependency_overrides.get(get_db, get_db)
        background_tasks.add_task(
            _responder_em_segundo_plano, [c.id for c in conversas], fabrica_sessao,
        )

    return {"ok": True, "mensagens": len(conversas)}


def _responder_em_segundo_plano(conversa_ids: list[int], fabrica_sessao) -> None:
    """Roda o turno de cada conversa fora do ciclo de resposta ao webhook."""
    gerador = fabrica_sessao()
    db = next(gerador)
    try:
        for conversa_id in conversa_ids:
            conversa = db.query(Conversation).filter(Conversation.id == conversa_id).first()
            if conversa is None:
                continue
            try:
                orchestrator.responder(db, conversa)
            except Exception:
                # `responder()` já não deveria levantar (ver seu docstring),
                # mas isto roda sem ninguém para ver um 500 — se escapar
                # mesmo assim, uma conversa não pode travar as seguintes.
                logger.exception(
                    "Falha ao rodar turno em segundo plano para a conversa %s.",
                    conversa_id,
                )
                db.rollback()
    finally:
        # Drena o gerador (`get_db` ou a substituta dos testes) até o
        # `finally: db.close()` dele rodar — mesmo contrato de quando o
        # FastAPI conduz essa mesma dependência numa requisição normal.
        try:
            next(gerador)
        except StopIteration:
            pass


def _processar(db: Session, dados: dict, dono: Optional[str] = None) -> list:
    """
    Percorre o envelope da Meta e grava o que chegou.

    Devolve as conversas que receberam mensagem nova — são elas que ganham um
    turno depois, já fora da transação de gravação.

    `dono` é o usuário de quem é o número que recebeu. Com uma conta por
    usuário, o mesmo telefone pode estar em conversa com mais de um: sem esse
    escopo, a mensagem entraria na conversa de outra pessoa.
    """
    tocadas = {}
    for entrada in dados.get("entry") or []:
        for mudanca in entrada.get("changes") or []:
            valor = mudanca.get("value") or {}
            for msg in valor.get("messages") or []:
                conversa = _guardar_recebida(db, msg, dono)
                if conversa is not None:
                    tocadas[conversa.id] = conversa
            for status in valor.get("statuses") or []:
                _atualizar_status(db, status)
            if mudanca.get("field") == "account_update":
                _avisar_qualidade(valor)
    return list(tocadas.values())


def _avisar_qualidade(valor: dict) -> None:
    """
    Aviso da Meta sobre o número (qualidade rebaixada, limite alterado).

    Vai para o log do servidor, não para a auditoria: o número é do WABA, não
    de um usuário, e atribuí-lo a alguém seria inventar um dono. A tela mostra
    o estado atual consultando a Meta em `/api/wa/status`.

    O nível é ERROR quando a qualidade cai porque, para uma operação que depende
    de um número só, esse é o alarme que antecede a restrição de envio.
    """
    evento = (valor.get("event") or "").upper()
    if "QUALITY" in evento or "DOWNGRADE" in evento or "FLAGGED" in evento:
        logger.error(
            "Meta rebaixou o número do WhatsApp (%s). Reduza o volume de "
            "convites frios e revise o template.", evento,
        )
    else:
        logger.warning("Aviso da Meta sobre a conta do WhatsApp: %s", evento or "sem evento")


def _corpo_da_mensagem(msg: dict) -> Optional[str]:
    """Texto quando há; senão, uma descrição do que veio (áudio, imagem…)."""
    tipo = msg.get("type")
    if tipo == "text":
        return (msg.get("text") or {}).get("body")
    if tipo == "button":
        return (msg.get("button") or {}).get("text")
    if tipo == "interactive":
        interativo = msg.get("interactive") or {}
        for chave in ("button_reply", "list_reply"):
            if chave in interativo:
                return (interativo[chave] or {}).get("title")
    return f"[{tipo or 'desconhecido'}]"


def _guardar_recebida(db: Session, msg: dict,
                      dono: Optional[str] = None) -> Optional[Conversation]:
    """Grava a mensagem. Devolve a conversa quando ela é nova; senão, None."""
    wamid = msg.get("id")
    telefone = _e164(msg.get("from"))
    if not telefone:
        logger.warning("Mensagem recebida sem número reconhecível; ignorada.")
        return None

    if wamid and db.query(WaMessage.id).filter(WaMessage.wa_message_id == wamid).first():
        # Reentrega da Meta: já processamos esta mensagem.
        return None

    consulta = db.query(Conversation).filter(Conversation.phone_e164 == telefone)
    if dono:
        # Só as conversas de quem é o número que recebeu. Duas contas podem
        # estar falando com o mesmo lead, e responder pela conversa errada
        # mostraria a um usuário o histórico do outro.
        consulta = consulta.filter(Conversation.user_id == dono)
    conversa = consulta.order_by(Conversation.updated_at.desc()).first()
    if conversa is None:
        # Alguém escreveu para o número sem ter sido convidado por nós. Não há
        # lead a que associar, e inventar um seria criar ficha de quem não
        # pediu. Fica no log para quem for investigar.
        logger.info("Mensagem de número sem conversa aberta; ignorada.")
        return None

    corpo = _corpo_da_mensagem(msg)
    db.add(WaMessage(
        conversation_id=conversa.id, direction="in", wa_message_id=wamid,
        type=msg.get("type") or "text", body=corpo, status="delivered",
    ))
    states.register_inbound(db, conversa, corpo)
    try:
        db.flush()
    except IntegrityError:
        # Duas entregas simultâneas da mesma mensagem: o índice único no banco
        # é a última linha de defesa, e ela funcionou.
        db.rollback()
        return None
    return conversa


def _atualizar_status(db: Session, status: dict) -> None:
    wamid = status.get("id")
    if not wamid:
        return
    msg = db.query(WaMessage).filter(WaMessage.wa_message_id == wamid).first()
    if msg:
        msg.status = status.get("status") or msg.status


# ── Abertura da conversa ─────────────────────────────────────────────────────

@router.get("/status")
def status_do_whatsapp(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    O que falta para o WhatsApp funcionar, e o que está esperando por você.

    A tela usa isto para deixar o botão apagado explicando o que falta, em vez
    de escondê-lo — um botão que some não ensina nada a quem procura por ele —
    e para o aviso na barra lateral.
    """
    user_id = current_user.get("sub")
    cred = credenciais.do_usuario(db, user_id)
    conversas = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id,
                Conversation.ai_status.in_((HUMAN_HANDOFF, AI_PAUSED)))
        .all()
    )
    return {
        "configurado": cred.configurado,
        "template": bool(cred.template_name),
        "webhook_assinado": bool(cred.app_secret),
        "faltando": cred.faltando(),
        # De quem é o número que envia: a conta do usuário ou o do servidor.
        # Sem isto, "configurado" não diz qual WhatsApp o lead vai ver.
        "origem": cred.origem,
        "erro": cred.erro,
        # A janela de horário vem junto para o botão da ficha já nascer apagado
        # quando não dá para enviar. Descobrir a recusa só depois de confirmar
        # um envio pago é o pior lugar possível para essa informação aparecer.
        "janela": gate.janela_de_envio(),
        "aguardando": sum(1 for c in conversas if gate.aguardando_voce(c)),
        "total": db.query(Conversation).filter(
            Conversation.user_id == user_id
        ).count(),
    }
    # A qualidade do número NÃO entra aqui de propósito: esta rota é carregada
    # a cada abertura do app, e consultar a Meta em toda carga somaria uma
    # chamada externa (e um modo de falha) ao caminho crítico. Ela vive em
    # /api/wa/metrics, que só a tela de conversas pede.


def _telefone_do_destino(db: Session, lead: Lead, body: WaStartRequest) -> Optional[str]:
    """
    De quem é o número: o informado no pedido, o do decisor escolhido ou, por
    último, o da empresa.

    A ordem não é arbitrária. O telefone da empresa é o que a coleta encontra e
    quase sempre é a central — mandar convite para lá gasta um template pago
    para falar com a recepção. Por isso ele é o último recurso, e a tela avisa.
    """
    if body.phone:
        return _e164(body.phone)
    if body.decision_maker_id:
        decisor = (
            db.query(DecisionMaker)
            .filter(DecisionMaker.id == body.decision_maker_id,
                    DecisionMaker.lead_id == lead.id)
            .first()
        )
        if decisor and decisor.phone:
            return _e164(decisor.phone)
    return _e164(lead.phone)


@router.post("/start", response_model=WaStartResponse)
def iniciar_conversa(
    body: WaStartRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Primeiro contato: envia o template aprovado e abre a conversa.

    É a ação irreversível e paga do produto — a mensagem chega no celular de
    alguém e a Meta cobra por ela. Por isso: exige usuário autenticado, exige
    que o lead seja dele, passa pelo portão de abertura e não é chamada por
    nada automático.
    """
    user_id = current_user.get("sub")
    cred = credenciais.do_usuario(db, user_id)
    if not cred.configurado:
        raise HTTPException(
            status_code=503,
            detail=cred.erro or ("Conecte seu WhatsApp Business em Configurações: falta "
                                 + ", ".join(cred.faltando()) + "."),
        )

    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.user_id == user_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")

    telefone = _telefone_do_destino(db, lead, body)
    if not telefone:
        raise HTTPException(
            status_code=422,
            detail="Este lead não tem telefone válido. Informe o celular do decisor na ficha.",
        )

    conversa = (
        db.query(Conversation)
        .filter(Conversation.lead_id == lead.id, Conversation.phone_e164 == telefone)
        .first()
    )
    decisao = gate.can_start(db, lead, telefone, conversa)
    if not decisao.allowed:
        # 409: o pedido está bem formado, o estado é que não permite. A tela
        # mostra `decisao.message` — o usuário precisa saber por que não saiu.
        raise HTTPException(status_code=409, detail=decisao.message)

    envio = client.send_template(
        telefone,
        variaveis=[lead.company_name or lead.domain or ""] if body.usar_nome_da_empresa else None,
        cred=cred,
    )
    if not envio.ok:
        raise HTTPException(status_code=502, detail=f"A Meta recusou o envio ({envio.error}).")

    conversa = states.start(db, lead, telefone, user_id,
                            decision_maker_id=body.decision_maker_id)
    db.flush()   # a conversa nova precisa de id antes da mensagem apontar para ela

    corpo = f"[template: {cred.template_name}]"
    db.add(WaMessage(
        conversation_id=conversa.id, direction="out",
        wa_message_id=envio.wa_message_id, type="template",
        template_name=cred.template_name, status="sent", sent_by="human",
        body=corpo,
    ))
    states.register_outbound(db, conversa, corpo)
    db.add(Activity(
        lead_id=lead.id, user_id=user_id, type="note",
        notes=f"Convite de WhatsApp enviado para {telefone}.",
    ))
    db.commit()
    db.refresh(conversa)

    return WaStartResponse(
        success=True,
        message="Convite enviado. A conversa fica aguardando a resposta do lead.",
        conversation=ConversationOut.model_validate(conversa),
    )


# ── A tela de conversas ──────────────────────────────────────────────────────

def _selo(conversa: Conversation, aguardando: bool) -> ConversationSeal:
    """
    Estado da conversa em uma palavra e uma frase.

    A frase não é enfeite: "pausada" sozinho não diz se o lead ainda recebe
    resposta, e é exatamente isso que quem olha o card precisa saber.
    """
    if conversa.ai_status == AI_ACTIVE:
        return ConversationSeal(
            tom="ativa", rotulo="IA ativa",
            explicacao="A automação responde sozinha, dentro das regras.",
        )
    if conversa.ai_status == HUMAN_HANDOFF:
        if aguardando:
            return ConversationSeal(
                tom="aguardando", rotulo="Aguardando você",
                explicacao="O lead escreveu e ainda não teve resposta.",
            )
        return ConversationSeal(
            tom="assumida", rotulo="Você assumiu",
            explicacao="A automação está calada; quem responde é você.",
        )
    if conversa.ai_status == AI_PAUSED:
        return ConversationSeal(
            tom="pausada", rotulo="Pausada",
            explicacao="Ninguém responde até você retomar ou assumir.",
        )
    return ConversationSeal(
        tom="encerrada", rotulo="Encerrada",
        explicacao="Esta conversa acabou. Nada mais é enviado.",
    )


def _card(db: Session, conversa: Conversation) -> ConversationCard:
    lead = db.query(Lead).filter(Lead.id == conversa.lead_id).first()
    contato = None
    if conversa.decision_maker_id:
        decisor = (
            db.query(DecisionMaker)
            .filter(DecisionMaker.id == conversa.decision_maker_id)
            .first()
        )
        contato = decisor.name if decisor else None

    aguardando = gate.aguardando_voce(conversa)
    return ConversationCard(
        id=conversa.id,
        lead_id=conversa.lead_id,
        company_name=(lead.company_name or lead.domain) if lead else None,
        contato=contato,
        phone_e164=conversa.phone_e164,
        ai_status=conversa.ai_status,
        selo=_selo(conversa, aguardando),
        last_message_body=conversa.last_message_body,
        last_inbound_at=conversa.last_inbound_at,
        last_outbound_at=conversa.last_outbound_at,
        handoff_reason=conversa.handoff_reason,
        janela_aberta=gate._janela_aberta(conversa, utcnow()),
        aguardando_voce=aguardando,
        updated_at=conversa.updated_at,
        stage=lead.stage if lead else None,
        janela_expira_em=gate.janela_da_conversa(conversa, utcnow()),
    )


def _ficha(lead: Optional[Lead]) -> Optional[dict]:
    """A planilha original do lead, para o painel da conversa — sem misturar
    com os campos de enriquecimento, que já aparecem em outras telas."""
    if lead is None or not lead.cells:
        return None
    return {k: str(v) for k, v in lead.cells.items() if v not in (None, "")}


def _agendamentos(db: Session, lead_id: int) -> list[AgendamentoOut]:
    """Próximos compromissos (reuniões) do lead, mais recentes primeiro no
    tempo — mesma consulta de `routers/activities.py::pending_activities`,
    só filtrada por lead em vez de global."""
    agora = utcnow()
    atividades = (
        db.query(Activity)
        .filter(
            Activity.lead_id == lead_id,
            Activity.type == "meeting",
            Activity.due_at.isnot(None),
            Activity.completed_at.is_(None),
        )
        .order_by(Activity.due_at.asc())
        .limit(5)
        .all()
    )
    return [
        AgendamentoOut(
            id=a.id, quando=a.due_at,
            ja_passou=gate._com_fuso(a.due_at) < agora, notas=a.notes,
        )
        for a in atividades
    ]


def _minha_conversa(db: Session, conversa_id: int, user_id: str) -> Conversation:
    conversa = (
        db.query(Conversation)
        .filter(Conversation.id == conversa_id, Conversation.user_id == user_id)
        .first()
    )
    if not conversa:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
    return conversa


@router.get("/conversations", response_model=list[ConversationCard])
def listar_conversas(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Conversas do usuário, da que mudou mais recentemente para a mais antiga."""
    conversas = (
        db.query(Conversation)
        .filter(Conversation.user_id == current_user.get("sub"))
        .order_by(Conversation.updated_at.desc())
        .limit(200)
        .all()
    )
    return [_card(db, c) for c in conversas]


@router.get("/conversations/{conversa_id}", response_model=ConversationDetail)
def ver_conversa(
    conversa_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    conversa = _minha_conversa(db, conversa_id, current_user.get("sub"))
    mensagens = (
        db.query(WaMessage)
        .filter(WaMessage.conversation_id == conversa.id)
        .order_by(WaMessage.id.asc())
        .all()
    )
    lead = db.query(Lead).filter(Lead.id == conversa.lead_id).first()
    return ConversationDetail(
        card=_card(db, conversa),
        messages=[WaMessageOut.model_validate(m) for m in mensagens],
        ficha=_ficha(lead),
        agendamentos=_agendamentos(db, conversa.lead_id),
    )


#: Verbo da tela → transição. A tela não escreve `ai_status`: se pudesse,
#: poderia inventar um estado que o portão não conhece e que ele, por ser
#: default-deny, recusaria para sempre sem explicar por quê.
_ACOES = {
    "pausar": (states.pause, "Pausada pelo usuário."),
    "assumir": (states.handoff, "Assumida pelo usuário."),
    "retomar": (states.resume, None),
    "encerrar": (states.stop, "Encerrada pelo usuário."),
}


@router.patch("/conversations/{conversa_id}", response_model=ConversationCard)
def controlar_conversa(
    conversa_id: int,
    body: ConversationAction,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Pausar, assumir, retomar ou encerrar.

    Vale imediatamente: o portão relê o estado do banco antes de cada envio,
    então uma resposta que esteja sendo redigida neste instante já não sai.
    """
    if body.acao not in _ACOES:
        raise HTTPException(
            status_code=422,
            detail=f"Ação inválida. Use: {', '.join(_ACOES)}.",
        )
    conversa = _minha_conversa(db, conversa_id, current_user.get("sub"))
    transicao, motivo_padrao = _ACOES[body.acao]

    if motivo_padrao is None:
        transicao(db, conversa)
    else:
        transicao(db, conversa, body.motivo or motivo_padrao)
    db.commit()
    db.refresh(conversa)
    return _card(db, conversa)


@router.post("/conversations/{conversa_id}/reply", response_model=ConversationDetail)
def responder(
    conversa_id: int,
    body: WaReplyRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Resposta escrita pelo próprio usuário.

    Não passa pelo portão da automação de propósito — este é o humano, e barrar
    alguém de responder a quem acabou de lhe escrever seria absurdo. As duas
    travas que continuam valendo são as que não são nossas para negociar: a
    janela de 24 h é regra da Meta, e o opt-out é a promessa de LGPD que o
    produto fez ao titular do número.

    Assumir é automático: quem digita a resposta está assumindo a conversa, e
    fazer a IA continuar respondendo por cima disso seria o pior dos mundos.
    """
    texto = (body.texto or "").strip()
    if not texto:
        raise HTTPException(status_code=422, detail="Escreva a mensagem antes de enviar.")

    user_id = current_user.get("sub")
    cred = credenciais.do_usuario(db, user_id)
    if not cred.configurado:
        raise HTTPException(
            status_code=503,
            detail=cred.erro or ("Conecte seu WhatsApp Business em Configurações: falta "
                                 + ", ".join(cred.faltando()) + "."),
        )

    conversa = _minha_conversa(db, conversa_id, user_id)

    if not gate._janela_aberta(conversa, utcnow()):
        raise HTTPException(
            status_code=409,
            detail="A janela de 24 horas fechou. Só um novo convite (template) "
                   "reabre a conversa — e ele é cobrado.",
        )
    if optout.is_blocked(db, "phone", conversa.phone_e164):
        raise HTTPException(
            status_code=409,
            detail="Este número pediu para não receber mensagens.",
        )

    envio = client.send_text(conversa.phone_e164, texto, cred=cred)
    if not envio.ok:
        raise HTTPException(status_code=502, detail=f"A Meta recusou o envio ({envio.error}).")

    if conversa.ai_status == AI_ACTIVE:
        states.handoff(db, conversa, "Você respondeu manualmente.", states.ATOR_HUMANO)
    db.add(WaMessage(
        conversation_id=conversa.id, direction="out",
        wa_message_id=envio.wa_message_id, type="text",
        body=texto, status="sent", sent_by="human",
    ))
    states.register_outbound(db, conversa, texto)
    db.commit()
    db.refresh(conversa)

    return ver_conversa(conversa.id, db, current_user)


# ── Auditoria e métricas ─────────────────────────────────────────────────────

@router.get("/conversations/{conversa_id}/audit", response_model=list[AuditEntryOut])
def auditoria_da_conversa(
    conversa_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    O histórico de decisões desta conversa.

    Responde a "por que isso continuou depois de eu ter pausado?" — que
    `handoff_reason` não responde, porque guarda só o último motivo.
    """
    conversa = _minha_conversa(db, conversa_id, current_user.get("sub"))
    registros = (
        db.query(AuditLog)
        .filter(AuditLog.conversation_id == conversa.id)
        .order_by(AuditLog.id.desc())
        .limit(100)
        .all()
    )
    return [AuditEntryOut.model_validate(r) for r in registros]


@router.get("/metrics", response_model=WaMetrics)
def metricas(
    dias: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Números do período: quanto se enviou, quanto respondeu, quanto virou humano.

    A taxa de handoff é calculada **sobre as conversas que tiveram resposta**,
    não sobre o total. Sobre o total ela cairia sozinha a cada convite não
    respondido, e passaria a medir a taxa de resposta em vez do que se quer
    saber — quanto a automação consegue tocar sozinha.
    """
    user_id = current_user.get("sub")
    desde = utcnow() - timedelta(days=dias)

    conversas = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id, Conversation.created_at >= desde)
        .all()
    )
    ids = [c.id for c in conversas]

    responderam = [c for c in conversas if c.last_inbound_at is not None]
    aguardando = sum(1 for c in conversas if gate.aguardando_voce(c))

    mensagens = (
        db.query(WaMessage.direction, WaMessage.type, WaMessage.sent_by,
                 func.count(WaMessage.id))
        .filter(WaMessage.conversation_id.in_(ids) if ids else False)
        .group_by(WaMessage.direction, WaMessage.type, WaMessage.sent_by)
        .all()
    ) if ids else []

    convites = sum(n for d, t, _s, n in mensagens if d == "out" and t == "template")
    resp_ia = sum(n for d, t, s, n in mensagens if d == "out" and s == "ai")
    resp_voce = sum(n for d, t, s, n in mensagens
                    if d == "out" and s == "human" and t != "template")

    registros = (
        db.query(AuditLog)
        .filter(AuditLog.user_id == user_id, AuditLog.created_at >= desde)
        .all()
    )
    handoffs = [r for r in registros if r.acao == AUDIT_ASSUMIDA]
    motivos = Counter((r.detalhe or "Sem motivo registrado.")[:120] for r in handoffs)
    relacionamentos = [r.detalhe or "" for r in registros
                       if r.acao == AUDIT_RELACIONAMENTO]

    return WaMetrics(
        dias=dias,
        conversas_iniciadas=len(conversas),
        convites_enviados=convites,
        leads_que_responderam=len(responderam),
        taxa_de_resposta=round(len(responderam) / len(conversas), 3) if conversas else 0.0,
        respostas_da_ia=resp_ia,
        respostas_suas=resp_voce,
        handoffs=len(handoffs),
        taxa_de_handoff=round(len(handoffs) / len(responderam), 3) if responderam else 0.0,
        motivos_de_handoff=[{"motivo": m, "vezes": n} for m, n in motivos.most_common(5)],
        viraram_cliente=sum(1 for d in relacionamentos if d.startswith(RELATIONSHIP_CUSTOMER)),
        pediram_para_parar=sum(1 for d in relacionamentos
                               if d.startswith(RELATIONSHIP_DO_NOT_CONTACT)),
        aguardando_voce=aguardando,
        qualidade_do_numero=client.phone_quality(credenciais.do_usuario(db, user_id)),
    )
