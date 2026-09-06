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
from models.database import WhatsAppConnection, get_db, utcnow
from services.wa import client, credenciais

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

    db.commit()
    db.refresh(existente)
    logger.info("WhatsApp conectado para o usuário %s.", user_id)

    return _resposta(existente, request, credenciais.do_usuario(db, user_id))


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
