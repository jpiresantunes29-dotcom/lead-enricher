"""
De quem é o WhatsApp que vai enviar esta mensagem.

Antes havia um só: número, token e template vinham do ambiente e valiam para
a instalação inteira. Agora cada conta conecta o próprio WhatsApp Business
(`whatsapp_connections`), e toda saída precisa dizer por qual número está
saindo — enviar pelo número errado não é um detalhe de configuração, é falar
com o lead de alguém usando a identidade de outra pessoa.

Duas origens, nesta ordem:

  `conta`      a conexão do usuário. É o caminho normal.
  `ambiente`   as variáveis do servidor. Continuam valendo como reserva para
               quem ainda não conectou e para desenvolvimento/teste — tirar
               isso quebraria toda instalação que hoje funciona com env var.

O que **não** cai para o ambiente: uma conexão gravada e ilegível. Quando a
`SECRETS_KEY` muda, o token cifrado não abre (`crypto.ILEGIVEL`); tratar isso
como "sem conexão" faria a mensagem sair pelo número do ambiente — o de outro
número, com outro remetente. Recusa-se o envio e a tela manda regravar.
"""
import logging
import os
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from models.database import WhatsAppConnection
from services.crypto import ILEGIVEL

logger = logging.getLogger(__name__)

ORIGEM_CONTA = "conta"
ORIGEM_AMBIENTE = "ambiente"


@dataclass(frozen=True)
class Credenciais:
    """
    O que é preciso para falar com a Meta em nome de um número.

    `origem` não é enfeite: a tela precisa dizer se o usuário está usando o
    WhatsApp dele ou o do servidor, senão "está configurado" vira uma frase
    que não diz de quem é o número que o lead vai ver.
    """
    phone_number_id: str = ""
    access_token: str = ""
    template_name: str = ""
    template_language: str = "pt_BR"
    app_secret: str = ""
    verify_token: str = ""
    #: Conta do WhatsApp Business a que o número pertence. Não é usado para
    #: enviar — só para listar os templates aprovados, que é uma consulta do
    #: WABA e não do número.
    waba_id: str = ""
    origem: str = ORIGEM_AMBIENTE
    #: Preenchido quando a conexão existe mas não serve (segredo ilegível).
    #: Quem chama devolve isto ao usuário em vez de tentar enviar.
    erro: Optional[str] = None

    @property
    def configurado(self) -> bool:
        return bool(self.phone_number_id and self.access_token and not self.erro)

    def faltando(self) -> list:
        """O que falta, em nomes que a tela entende."""
        falta = []
        if not self.phone_number_id:
            falta.append("id do número (Phone Number ID)")
        if not self.access_token:
            falta.append("token de acesso")
        if not self.template_name:
            falta.append("template de abertura")
        return falta


def do_ambiente() -> Credenciais:
    """As variáveis do servidor — a reserva de quem ainda não conectou."""
    return Credenciais(
        phone_number_id=(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip(),
        access_token=(os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip(),
        template_name=(os.getenv("WHATSAPP_TEMPLATE_NAME") or "").strip(),
        template_language=(os.getenv("WHATSAPP_TEMPLATE_LANG") or "pt_BR").strip(),
        app_secret=(os.getenv("WHATSAPP_APP_SECRET") or "").strip(),
        verify_token=(os.getenv("WHATSAPP_VERIFY_TOKEN") or "").strip(),
        origem=ORIGEM_AMBIENTE,
    )


def _da_conexao(conexao: WhatsAppConnection) -> Credenciais:
    token = conexao.access_token or ""
    if token == ILEGIVEL or conexao.app_secret == ILEGIVEL:
        # Chave de cifra trocada. Não é "sem conexão": é conexão que existe e
        # não abre. Cair para o ambiente aqui mandaria a mensagem por outro
        # número sem ninguém perceber.
        logger.error("Conexão de WhatsApp ilegível com a SECRETS_KEY atual (user %s).",
                     conexao.user_id)
        return Credenciais(
            origem=ORIGEM_CONTA,
            erro="As credenciais do WhatsApp desta conta não puderam ser lidas "
                 "(a chave de segredos do servidor mudou). Reconecte o WhatsApp.",
        )
    return Credenciais(
        phone_number_id=conexao.phone_number_id or "",
        access_token=token,
        template_name=conexao.template_name or "",
        template_language=conexao.template_language or "pt_BR",
        app_secret="" if conexao.app_secret == ILEGIVEL else (conexao.app_secret or ""),
        verify_token="" if conexao.verify_token == ILEGIVEL else (conexao.verify_token or ""),
        waba_id=conexao.waba_id or "",
        origem=ORIGEM_CONTA,
    )


def conexao_do_usuario(db: Session, user_id: str) -> Optional[WhatsAppConnection]:
    """A conexão ativa do usuário, se houver."""
    if not user_id:
        return None
    return (
        db.query(WhatsAppConnection)
        .filter(WhatsAppConnection.user_id == user_id,
                WhatsAppConnection.is_active.is_(True))
        .first()
    )


def do_usuario(db: Session, user_id: str) -> Credenciais:
    """
    Por qual WhatsApp este usuário fala.

    A conexão dele quando existe; as variáveis do servidor quando não.
    """
    conexao = conexao_do_usuario(db, user_id)
    if conexao is None:
        return do_ambiente()
    return _da_conexao(conexao)


def por_phone_number_id(db: Session, phone_number_id: str):
    """
    De quem é o número que recebeu a mensagem.

    O webhook precisa disto antes de validar a assinatura: cada conta tem o
    próprio App Secret, e sem saber o dono não há com o que comparar. Devolve
    `(user_id, Credenciais)` — `user_id` é `None` quando o número é o do
    ambiente ou quando não se reconhece o número.
    """
    pnid = (phone_number_id or "").strip()
    if pnid:
        conexao = (
            db.query(WhatsAppConnection)
            .filter(WhatsAppConnection.phone_number_id == pnid,
                    WhatsAppConnection.is_active.is_(True))
            .first()
        )
        if conexao is not None:
            return conexao.user_id, _da_conexao(conexao)

    ambiente = do_ambiente()
    if pnid and ambiente.phone_number_id and pnid != ambiente.phone_number_id:
        # Número que ninguém conectou. Não é erro nosso — é webhook de uma
        # conta que foi desconectada aqui e continua apontando para cá.
        return None, Credenciais(origem=ORIGEM_AMBIENTE,
                                 erro="Número não reconhecido nesta instalação.")
    return None, ambiente
