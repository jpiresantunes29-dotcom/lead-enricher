"""
Conectar o WhatsApp Business da própria conta.

Antes, o número era da instalação: quem administrava o servidor colava as
credenciais em variável de ambiente e todo mundo falava pelo mesmo WhatsApp.
Aqui o usuário conecta o dele pela tela, e passa a enviar pelo próprio número.

Três cuidados que o resto do arquivo assume:

  **Testar antes de dar por conectado.** Gravar um token não prova que ele
  vale. Um token expirado só apareceria no primeiro convite — que é pago e
  chega no celular de alguém. Por isso o POST pergunta à Meta e só grava
  `verified_at` quando ela confirma.

  **Nunca devolver segredo.** O GET diz *se* há token, não qual. Depois de
  gravado, um segredo não volta para a tela: quem precisar trocar, regrava.

  **Um número, um dono.** `phone_number_id` é único no banco. Sem isso, dois
  usuários apontando para o mesmo número tornariam ambígua a pergunta que o
  webhook faz a cada mensagem: "de quem é isto?".
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from middleware.auth import get_current_user
from models.database import (
    HISTORICO_FALHOU, HISTORICO_SOLICITADO,
    ONBOARDING_CADASTRO, ONBOARDING_COEXISTENCIA, ONBOARDING_MANUAL,
    WhatsAppConnection, get_db, utcnow,
)
from services.wa import client, credenciais, embedded

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wa", tags=["whatsapp"])


class ConexaoRequest(BaseModel):
    """
    O que o usuário cola da Meta.

    Só `phone_number_id` é sempre obrigatório. Os segredos são opcionais na
    edição: em branco significa "mantenha o que está lá" — é o que permite
    trocar só o template sem precisar colar o token de novo.
    """
    phone_number_id: str = Field(..., min_length=1, max_length=64)
    access_token: str | None = Field(None, max_length=1000)
    app_secret: str | None = Field(None, max_length=200)
    verify_token: str | None = Field(None, max_length=200)
    template_name: str | None = Field(None, max_length=120)
    template_language: str | None = Field(None, max_length=10)


def _url_do_webhook(request: Request) -> str:
    """
    O endereço que o usuário precisa cadastrar na Meta.

    Montado a partir do host da requisição para funcionar igual em produção,
    preview e máquina local — fixar isso em configuração daria a URL errada
    exatamente para quem está testando.
    """
    return str(request.url_for("receber_webhook"))


def _resposta(conexao: WhatsAppConnection | None, request: Request,
              cred: credenciais.Credenciais) -> dict:
    """O estado da conexão como a tela precisa ver — sem segredo nenhum."""
    return {
        "conectado": conexao is not None,
        # Quando não há conexão, o envio ainda pode funcionar pelas variáveis
        # do servidor. A tela precisa dizer isso em vez de "não configurado".
        "origem": cred.origem,
        "configurado": cred.configurado,
        "faltando": cred.faltando(),
        "erro": cred.erro,
        "phone_number_id": conexao.phone_number_id if conexao else None,
        "waba_id": conexao.waba_id if conexao else None,
        "display_phone_number": conexao.display_phone_number if conexao else None,
        "template_name": conexao.template_name if conexao else cred.template_name or None,
        "template_language": (conexao.template_language if conexao
                              else cred.template_language),
        "tem_token": bool(conexao.access_token) if conexao else bool(cred.access_token),
        "tem_app_secret": bool(conexao.app_secret) if conexao else bool(cred.app_secret),
        "tem_verify_token": bool(conexao.verify_token) if conexao else bool(cred.verify_token),
        "verificado_em": (conexao.verified_at.isoformat()
                          if conexao and conexao.verified_at else None),
        "webhook_url": _url_do_webhook(request),
        "onboarding": conexao.onboarding if conexao else None,
        "historico_status": conexao.historico_status if conexao else None,
        "historico_progresso": conexao.historico_progresso if conexao else None,
    }


@router.get("/connection")
def ver_conexao(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """O WhatsApp desta conta: o que está conectado e o que falta."""
    user_id = current_user.get("sub")
    conexao = credenciais.conexao_do_usuario(db, user_id)
    return _resposta(conexao, request, credenciais.do_usuario(db, user_id))


@router.post("/connection")
def conectar(
    body: ConexaoRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Conecta (ou atualiza) o WhatsApp Business da conta.

    Pergunta à Meta antes de gravar: token que não vale é recusado aqui, com a
    frase que a Meta deu, em vez de virar um convite pago que não sai.
    """
    user_id = current_user.get("sub")
    pnid = body.phone_number_id.strip()

    dono_do_numero = (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.phone_number_id == pnid,
                WhatsAppConnection.user_id != user_id)
        .first()
    )
    if dono_do_numero is not None:
        # Não diz de quem é: quem tenta conectar um número que não é seu não
        # precisa saber qual conta o tem.
        raise HTTPException(
            status_code=409,
            detail="Este número já está conectado em outra conta.",
        )

    existente = (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.user_id == user_id)
        .first()
    )

    token = (body.access_token or "").strip() or (existente.access_token if existente else "")
    if not token:
        raise HTTPException(
            status_code=422,
            detail="Informe o token de acesso da Meta para conectar.",
        )

    ok, resultado = client.conferir(credenciais.Credenciais(
        phone_number_id=pnid, access_token=token,
    ))
    if not ok:
        # 422 e não 502: o que está errado é o que o usuário colou, e a tela
        # precisa devolvê-lo ao formulário em vez de sugerir "tente de novo".
        raise HTTPException(status_code=422, detail=str(resultado))

    if existente is None:
        existente = WhatsAppConnection(user_id=user_id, phone_number_id=pnid,
                                       access_token=token)
        db.add(existente)
    else:
        existente.phone_number_id = pnid
        existente.access_token = token

    # Campo em branco mantém o que está gravado — é o que permite editar só o
    # template sem recolar os segredos.
    if (body.app_secret or "").strip():
        existente.app_secret = body.app_secret.strip()
    if (body.verify_token or "").strip():
        existente.verify_token = body.verify_token.strip()
    if body.template_name is not None:
        existente.template_name = body.template_name.strip() or None
    if (body.template_language or "").strip():
        existente.template_language = body.template_language.strip()

    existente.waba_id = resultado.get("waba_id") or existente.waba_id
    existente.display_phone_number = (resultado.get("display_phone_number")
                                      or existente.display_phone_number)
    existente.is_active = True
    existente.verified_at = utcnow()
    existente.onboarding = existente.onboarding or ONBOARDING_MANUAL

    db.commit()
    db.refresh(existente)
    logger.info("WhatsApp conectado para o usuário %s.", user_id)

    return _resposta(existente, request, credenciais.do_usuario(db, user_id))


# ── Embedded Signup: conectar pelo popup da Meta ─────────────────────────────

class EmbeddedRequest(BaseModel):
    """
    O que o popup da Meta entregou à página.

    `code` é de uso único e vale 30 segundos — a tela manda assim que recebe.
    `phone_number_id` nem sempre vem (o evento de Coexistência traz só o
    `waba_id`); quando falta, a conta é consultada.
    """
    code: str = Field(..., min_length=1, max_length=2000)
    waba_id: str = Field(..., min_length=1, max_length=64)
    phone_number_id: str | None = Field(None, max_length=64)
    evento: str | None = Field(None, max_length=64)


@router.get("/connection/embedded/config")
def configuracao_do_popup(current_user: dict = Depends(get_current_user)):
    """
    Ids públicos para a tela abrir o popup da Meta, ou o que falta no servidor.
    """
    return embedded.configuracao()


@router.post("/connection/embedded")
def conectar_pelo_popup(
    body: EmbeddedRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Conclui a conexão feita pelo popup da Meta (Embedded Signup).

    Na Coexistência, também pede à Meta a agenda e o histórico do celular — o
    que só pode ser feito uma vez, nas 24 h seguintes à conexão. Por isso o
    pedido sai daqui, no mesmo clique, e não de um botão que alguém pode
    esquecer de apertar.

    Falha ao assinar os webhooks ou ao pedir a sincronização **não** desfaz a
    conexão: o `code` já foi gasto, e jogar fora um token válido obrigaria o
    usuário a refazer o fluxo inteiro no celular. A resposta traz `avisos`
    dizendo o que não deu certo.
    """
    user_id = current_user.get("sub")

    ok, token = embedded.trocar_codigo(body.code.strip())
    if not ok:
        raise HTTPException(status_code=422, detail=str(token))

    ok, dados = embedded.numero_da_conta(body.waba_id.strip(), token,
                                         (body.phone_number_id or "").strip() or None)
    if not ok:
        raise HTTPException(status_code=422, detail=str(dados))
    pnid = dados["phone_number_id"]

    if (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.phone_number_id == pnid,
                WhatsAppConnection.user_id != user_id)
        .first()
    ) is not None:
        raise HTTPException(status_code=409, detail="Este número já está conectado em outra conta.")

    coexistencia = (body.evento == embedded.EVENTO_COEXISTENCIA) or dados.get("coexistencia")
    avisos = []

    ok, erro = embedded.assinar_webhooks(dados["waba_id"], token)
    if not ok:
        avisos.append(f"{erro} Sem isso as mensagens não chegam aqui — "
                      "confira o app na Meta e conecte de novo.")

    conexao = (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.user_id == user_id)
        .first()
    )
    if conexao is None:
        conexao = WhatsAppConnection(user_id=user_id, phone_number_id=pnid, access_token=token)
        db.add(conexao)
    conexao.phone_number_id = pnid
    conexao.access_token = token
    conexao.waba_id = dados["waba_id"]
    conexao.display_phone_number = dados.get("display_phone_number")
    # O webhook desta conexão é assinado pelo App Secret do servidor, não por
    # um da conta: um segredo antigo colado à mão faria toda entrega ser
    # recusada como falsa.
    conexao.app_secret = None
    conexao.onboarding = ONBOARDING_COEXISTENCIA if coexistencia else ONBOARDING_CADASTRO
    conexao.historico_status = None
    conexao.historico_progresso = None
    conexao.is_active = True
    conexao.verified_at = utcnow()
    db.commit()

    if coexistencia:
        # Agenda antes do histórico: com os nomes já gravados, as conversas
        # importadas nascem com o nome do contato em vez de só o número.
        ok, erro = embedded.sincronizar(pnid, token, embedded.SYNC_CONTATOS)
        if not ok:
            avisos.append(f"Agenda de contatos: {erro}")
        ok, erro = embedded.sincronizar(pnid, token, embedded.SYNC_HISTORICO)
        conexao.historico_status = HISTORICO_SOLICITADO if ok else HISTORICO_FALHOU
        if not ok:
            avisos.append(f"Histórico: {erro}")
        db.commit()

    db.refresh(conexao)
    logger.info("WhatsApp conectado pelo popup da Meta para o usuário %s (%s).",
                user_id, conexao.onboarding)
    resposta = _resposta(conexao, request, credenciais.do_usuario(db, user_id))
    resposta["avisos"] = avisos
    return resposta


@router.get("/connection/templates")
def templates_aprovados(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Os templates aprovados da conta, para a tela oferecer uma lista.

    Devolve lista vazia sem erro quando não dá para consultar: escolher o
    template pelo nome digitado continua funcionando, e derrubar a tela de
    configuração por causa de uma consulta de conforto seria pior.
    """
    cred = credenciais.do_usuario(db, current_user.get("sub"))
    if not cred.configurado:
        return {"templates": []}
    _ok, templates = client.listar_templates(cred)
    return {"templates": templates if isinstance(templates, list) else []}


@router.delete("/connection")
def desconectar(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Desconecta o WhatsApp da conta.

    Apaga a linha inteira, com os segredos. As conversas ficam: elas são
    histórico do lead, não da credencial — some a forma de enviar, não o que
    já foi dito.

    Na Coexistência isto desliga o número **daqui**, não da Meta: a API não
    desconecta um número que está no app do celular. Para cortar de vez, o
    dono desconecta no próprio WhatsApp Business — a tela avisa.
    """
    user_id = current_user.get("sub")
    conexao = (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.user_id == user_id)
        .first()
    )
    if conexao is None:
        raise HTTPException(status_code=404, detail="Nenhum WhatsApp conectado nesta conta.")

    db.delete(conexao)
    db.commit()
    logger.info("WhatsApp desconectado para o usuário %s.", user_id)
    return {"desconectado": True}
