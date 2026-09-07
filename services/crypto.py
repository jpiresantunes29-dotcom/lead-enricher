"""
Segredos de integração cifrados em repouso.

O que mora em `crm_connections` não é dado do produto: é **credencial de
terceiro**. O `webhook_secret` assina o payload que chega no Dynamics do
usuário — quem o tiver forja um lead que o CRM dele aceita como nosso. O
`access_token` é a chave da conta dele no Dataverse. Uma cópia de banco
tirada para depurar, um backup mal guardado ou um `SELECT` de alguém com
acesso de leitura passam a valer tanto quanto a senha.

O que **não** é cifrado, de propósito:

  `webhook_url`   é destino, não credencial, e precisa ser lido em claro na
                  hora do envio para passar pela checagem anti-SSRF.
  `account_id`    identifica, não autentica.

Cifrar tudo o que não precisa só aumenta a superfície de "e se a chave sumir".

## Sem `SECRETS_KEY`

Grava em claro e avisa. A alternativa — recusar a gravação — quebraria
desenvolvimento e teste por uma variável que ninguém tem motivo para definir
na própria máquina. Quem cobra é o preflight, que sabe se há segredo real em
risco e se o ambiente é produção.

## Se a chave mudar

O valor cifrado com a chave antiga vira ilegível, e é aí que a decisão
importa: devolver `None` faria o envio ao CRM sair **sem assinatura** — a
proteção desligada em silêncio, que é o pior resultado possível. Devolvemos
`ILEGIVEL`, um marcador que o site de envio reconhece e recusa. Perder a
chave custa regravar a conexão; assinar com o segredo errado custa confiança
no que o CRM recebe.
"""
import base64
import hashlib
import logging
import os
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)

#: Marca o texto cifrado. A versão vai junto para que uma futura troca de
#: algoritmo consiga conviver com o que já está gravado, em vez de exigir que
#: o banco inteiro migre no mesmo deploy.
PREFIXO = "enc:v1:"

#: Devolvido quando o valor está cifrado mas a chave atual não o abre. Começa
#: com NUL para não colidir com nenhum segredo real e para saltar aos olhos em
#: qualquer log ou depurador.
ILEGIVEL = "\x00segredo-ilegivel"

#: Abaixo disto a chave não é uma chave, é uma senha — e o derivador aqui é uma
#: passada de SHA-256, que não foi feito para resistir a força bruta sobre
#: senha curta.
TAMANHO_MINIMO = 32

#: Colunas cobertas. Usada pelo inventário e pelo script de recriptografia.
COLUNAS_SEGREDO = ("webhook_secret", "access_token", "refresh_token")


def _chave_bruta() -> str:
    return (os.getenv("SECRETS_KEY") or "").strip()


def chave_configurada() -> bool:
    return len(_chave_bruta()) >= TAMANHO_MINIMO


@lru_cache(maxsize=4)
def _fernet_para(chave: str) -> Fernet:
    """
    Deriva a chave Fernet (32 bytes em base64 urlsafe) do valor do ambiente.

    Derivamos em vez de exigir o formato exato do Fernet para que a variável
    seja gerada do mesmo jeito que todas as outras do projeto
    (`secrets.token_urlsafe(32)`) — uma exigência de formato a mais é uma
    chance a mais de alguém colar algo que "parece certo" e só descobrir o
    erro quando o primeiro segredo não abrir.
    """
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(chave.encode()).digest()))


def esta_cifrado(valor: Optional[str]) -> bool:
    return bool(valor) and valor.startswith(PREFIXO)


def cifrar(valor: Optional[str]) -> Optional[str]:
    """Cifra se houver chave; devolve o texto original se não houver."""
    if valor is None or valor == "":
        return valor
    if esta_cifrado(valor):
        return valor
    if not chave_configurada():
        return valor
    token = _fernet_para(_chave_bruta()).encrypt(valor.encode())
    return PREFIXO + token.decode()


def decifrar(valor: Optional[str]) -> Optional[str]:
    """
    Abre o valor gravado.

    Texto sem prefixo volta como está: é o que foi gravado antes desta chave
    existir, e ele continua funcionando até alguém rodar a recriptografia.
    """
    if not esta_cifrado(valor):
        return valor
    if not chave_configurada():
        logger.error(
            "Segredo cifrado no banco e SECRETS_KEY ausente: a integração "
            "que depende dele vai recusar o envio até a variável voltar."
        )
        return ILEGIVEL
    try:
        return _fernet_para(_chave_bruta()).decrypt(
            valor[len(PREFIXO):].encode()
        ).decode()
    except (InvalidToken, ValueError):
        # Sem o valor no log: mesmo ilegível, é material criptográfico.
        logger.error(
            "Segredo não abre com a SECRETS_KEY atual (chave trocada ou "
            "valor corrompido). Regrave a conexão em Configurações."
        )
        return ILEGIVEL


class SegredoCriptografado(TypeDecorator):
    """
    Coluna de texto que se cifra sozinha ao gravar e se abre ao ler.

    Fica no tipo da coluna, e não em `@property` no modelo, porque assim não
    existe caminho que escreva sem passar por aqui — um `bulk_update`, uma
    migração ou um `session.merge` continuariam valendo se a regra morasse no
    Python do modelo.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value == ILEGIVEL:
            # Gravar o marcador cifraria a *falha* e apagaria de vez o segredo
            # que ainda pode voltar quando a chave certa reaparecer.
            raise ValueError(
                "Tentativa de gravar o marcador de segredo ilegível. "
                "Peça o valor real ao usuário em vez de reescrever o que não abriu."
            )
        if value and not chave_configurada() and not esta_cifrado(value):
            logger.warning(
                "Segredo de integração gravado em claro: SECRETS_KEY não "
                "configurada. Veja /api/internal/preflight."
            )
        return cifrar(value)

    def process_result_value(self, value, dialect):
        return decifrar(value)


def inventario(db) -> dict:
    """
    Conta o estado dos segredos gravados: em claro, cifrados e ilegíveis.

    Lê por SQL cru de propósito — passar pelo tipo da coluna devolveria o valor
    já traduzido e esconderia justamente a diferença que queremos medir.
    """
    from sqlalchemy import text

    contagem = {"em_claro": 0, "cifrados": 0, "ilegiveis": 0}
    colunas = ", ".join(COLUNAS_SEGREDO)
    try:
        linhas = db.execute(text(f"SELECT {colunas} FROM crm_connections")).all()
    except Exception:
        # Banco sem a tabela ainda (schema novo, teste isolado): nada a contar.
        return contagem

    for linha in linhas:
        for valor in linha:
            if not valor:
                continue
            if not esta_cifrado(valor):
                contagem["em_claro"] += 1
            elif decifrar(valor) == ILEGIVEL:
                contagem["ilegiveis"] += 1
            else:
                contagem["cifrados"] += 1
    return contagem


def recriptografar(db) -> dict:
    """
    Cifra o que ficou em claro de deploys anteriores. Idempotente.

    Não commita — quem chama decide. Valor já cifrado é deixado como está,
    inclusive o ilegível: reescrevê-lo com a chave nova apagaria a chance de
    recuperá-lo com a chave antiga.
    """
    from sqlalchemy import text

    if not chave_configurada():
        raise RuntimeError(
            f"SECRETS_KEY ausente ou com menos de {TAMANHO_MINIMO} caracteres."
        )

    resultado = {"convertidos": 0, "ja_cifrados": 0, "ilegiveis": 0}
    colunas = ", ".join(COLUNAS_SEGREDO)
    linhas = db.execute(
        text(f"SELECT user_id, provider, {colunas} FROM crm_connections")
    ).all()

    for linha in linhas:
        user_id, provider = linha[0], linha[1]
        novos = {}
        for nome, valor in zip(COLUNAS_SEGREDO, linha[2:]):
            if not valor:
                continue
            if esta_cifrado(valor):
                chave = "ilegiveis" if decifrar(valor) == ILEGIVEL else "ja_cifrados"
                resultado[chave] += 1
                continue
            novos[nome] = cifrar(valor)

        if not novos:
            continue
        atribuicoes = ", ".join(f"{nome} = :{nome}" for nome in novos)
        db.execute(
            text(
                f"UPDATE crm_connections SET {atribuicoes} "
                "WHERE user_id = :_uid AND provider = :_prov"
            ),
            {**novos, "_uid": user_id, "_prov": provider},
        )
        resultado["convertidos"] += len(novos)

    return resultado
