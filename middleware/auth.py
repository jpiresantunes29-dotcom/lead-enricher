"""
Autenticação: JWT do Supabase.

Regra que não pode ser quebrada: **JWT só é decodificado com uma chave real**.
Com `SUPABASE_JWT_SECRET` ausente o valor cai para string vazia, e o HS256
valida qualquer token assinado com "" — ou seja, qualquer pessoa assumiria
qualquer `sub`. Por isso a chave é checada antes de cada decode e a ausência
vira 503 (erro de configuração do servidor), nunca "token aceito".

Dois caminhos de verificação, nesta ordem:

1. **JWKS** (`/auth/v1/.well-known/jwks.json`) — projetos que usam as *JWT
   signing keys* assinam com uma chave própria e publicam só a parte pública.
   O token traz o `kid`, e é ele que diz qual chave usar. Nada de segredo no
   ambiente do deploy.
2. **Segredo legado** (`SUPABASE_JWT_SECRET`, HS256) — projetos que ainda
   assinam com o JWT Secret compartilhado.

Por que os dois: se o projeto migrou para signing keys com *segredo
compartilhado*, o material dessa chave **não é extraível do Supabase** (é
decisão de projeto deles), e aí nenhum valor de `SUPABASE_JWT_SECRET` faz o
login passar — o jeito é a chave ser assimétrica e vir pelo JWKS.
"""
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

_security = HTTPBearer()

# O JWT secret do Supabase tem 40+ caracteres. Qualquer coisa menor que isso é
# variável mal preenchida (placeholder do .env.example, string vazia) e não
# pode ser usada para validar assinatura.
MIN_SECRET_LENGTH = 32


def jwt_secret() -> str:
    return (os.getenv("SUPABASE_JWT_SECRET") or "").strip()


def jwt_configured() -> bool:
    """Há segredo de verdade para validar assinatura?"""
    return len(jwt_secret()) >= MIN_SECRET_LENGTH


# ── O projeto Supabase, em um lugar só ───────────────────────────────────────
# https://supabase.com/dashboard/project/sgfbplozrpjnsudoawpz
#
# Este `ref` é a fonte única de verdade do backend: dele saem a URL do projeto,
# o JWKS que verifica o login e a comparação com o `iss` dos tokens. O mesmo
# projeto está em `static/js/app.js` (`_SB_URL`/`_SB_ANON`) — os dois lados
# precisam ser o MESMO projeto, e um teste trava isso.
#
# Trocar de projeto é editar aqui: `PROJETO_REF`, `SUPABASE_ANON_KEY_PADRAO`,
# `JWKS_PADRAO` e as duas constantes do app.js. Não dá para trocar por variável
# de ambiente de propósito — ver `anon_key()` logo abaixo.
PROJETO_REF = "sgfbplozrpjnsudoawpz"

# Chave pública (anon) do projeto — a mesma de `static/js/app.js`.
# Ela é um JWT assinado com o JWT Secret do projeto, o que a torna um gabarito:
# um segredo que não valida esta chave é de **outro projeto**, e aí todo login
# real leva 401 mesmo com tudo o mais certo.
SUPABASE_ANON_KEY_PADRAO = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNnZmJwbG96cnBqbnN1ZG9hd3B6Iiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3ODY1NTAyMjksImV4cCI6MjEwMjEyNjIyOX0."
    "ry1F3IaKFd7yMFD8-pjj2j06xTZl6Xm8iUnweztCSMA"
)

# Variáveis de ambiente descartadas por apontarem para outro projeto: nome → o
# que elas diziam. Não participam de nada; existem para o diagnóstico poder
# nomear a variável que precisa ser apagada no painel do deploy. Sem isso, uma
# variável esquecida de um projeto antigo é invisível — o painel mostra que ela
# está lá, o app diz que está tudo certo, e ninguém liga uma coisa à outra.
_env_ignoradas: dict = {}


def _ignorar_env(nome: str, valor_visto: str) -> None:
    if _env_ignoradas.get(nome) != valor_visto:   # loga uma vez por valor novo
        logger.error(
            "%s aponta para o projeto Supabase '%s', mas este app é do projeto "
            "'%s' — variável ignorada. Apague-a (ou corrija-a) no ambiente do "
            "deploy: enquanto ela estiver aí, ninguém entende de onde vem a "
            "divergência.", nome, valor_visto, PROJETO_REF,
        )
    _env_ignoradas[nome] = valor_visto


def _ref_da_anon(chave: str) -> str:
    try:
        return jwt.get_unverified_claims(chave).get("ref") or ""
    except Exception:
        return ""


def anon_key() -> str:
    """
    A chave anon do projeto. `SUPABASE_ANON_KEY` só é aceita se for do **mesmo**
    projeto — rotacionar a chave pelo ambiente, sim; trocar de projeto por uma
    variável esquecida, não.

    Essa distinção é o bug que este arquivo já pagou caro: a anon key define
    contra qual projeto o servidor verifica o login, o frontend loga no projeto
    do código, e uma variável de um projeto antigo no deploy fazia o servidor
    conferir assinatura com o chaveiro errado. O login terminava bem no Google e
    voltava 401 — com a tela dizendo "projeto diferente" sobre uma configuração
    que, no código, estava certa.
    """
    env = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    if not env:
        return SUPABASE_ANON_KEY_PADRAO
    ref = _ref_da_anon(env)
    if ref == PROJETO_REF:
        return env
    _ignorar_env("SUPABASE_ANON_KEY", ref or "(chave ilegível)")
    return SUPABASE_ANON_KEY_PADRAO


def supabase_url() -> str:
    """
    URL do projeto de onde vêm as chaves de verificação — sempre a de
    `PROJETO_REF`. `SUPABASE_URL` de outro projeto é ignorada pelo mesmo motivo
    da anon key.
    """
    env = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    if env and PROJETO_REF not in env:
        _ignorar_env("SUPABASE_URL", env)
    return f"https://{PROJETO_REF}.supabase.co"


# ── Chave pública embutida ───────────────────────────────────────────────────
# O JWKS do projeto, gravado aqui. É chave **pública**: confere assinatura, não
# produz nenhuma. Publicá-la no código não expõe nada — ela já é servida aberta
# em /auth/v1/.well-known/jwks.json.
#
# Existe porque a verificação do login não pode depender de uma chamada de rede
# feita no meio da requisição. Em serverless cada instância fria busca o JWKS
# de novo, e um único timeout de 5 segundos recusa o login de quem estava
# entrando naquele instante — com 401, que na tela é idêntico a "chave errada".
# A busca continua acontecendo e tem precedência: é ela que faz uma rotação de
# chave entrar sozinha, sem redeploy. Isto aqui é só o piso, para o caso de a
# busca falhar.
JWKS_PADRAO = [
    {
        "alg": "ES256",
        "crv": "P-256",
        "kid": "f28bf17b-e28c-4c0e-9fbd-a50c3cb314ed",
        "kty": "EC",
        "use": "sig",
        "x": "KN6v45NMn0v2prLBlwmll_4iy0okTckRTQCgujSeaNU",
        "y": "HYMkOJWhbBiPtDcYyO-QDb46P_5r78m4oIdFFSS0MRA",
    }
]


def _ref_do_projeto_padrao() -> str:
    return _ref_da_anon(SUPABASE_ANON_KEY_PADRAO)


def _jwks_embutido() -> list:
    """
    A chave embutida só vale para o projeto de onde ela veio.

    As três constantes do projeto (`PROJETO_REF`, anon key e este JWKS) são
    editadas à mão e por isso podem sair de sincronia numa troca de projeto
    feita pela metade. Se a anon key do código já não é a de `PROJETO_REF`,
    continuar oferecendo esta chave só produziria um `kid` que nunca casa —
    ruído em cima de um problema que já é confuso.
    """
    return list(JWKS_PADRAO) if _ref_do_projeto_padrao() == PROJETO_REF else []


# Cache do JWKS: uma busca por chave nova, não uma por requisição. O TTL curto
# é o que faz uma rotação de chave entrar sozinha, sem redeploy.
_JWKS_TTL_SEGUNDOS = 600
_JWKS_TTL_ERRO = 30
# `origem` e `erro` não participam da verificação: são o que o diagnóstico lê
# para dizer de onde veio a chave que recusou (ou aceitou) o token.
_jwks_cache = {"em": 0.0, "chaves": [], "origem": "nao-buscado", "erro": None}


def _guardar(chaves: list, origem: str, erro=None) -> list:
    _jwks_cache["origem"] = origem
    _jwks_cache["erro"] = None if erro is None else str(erro)[:200]
    return chaves


def _jwks(forcar: bool = False) -> list:
    agora = time.monotonic()
    if not forcar and _jwks_cache["chaves"] and agora - _jwks_cache["em"] < _JWKS_TTL_SEGUNDOS:
        return _jwks_cache["chaves"]
    if not forcar and not _jwks_cache["chaves"] and agora - _jwks_cache["em"] < _JWKS_TTL_ERRO:
        return _jwks_embutido()
    base = supabase_url()
    if not base:
        return _jwks_embutido()
    try:
        resp = requests.get(f"{base}/auth/v1/.well-known/jwks.json", timeout=5)
        resp.raise_for_status()
        chaves = resp.json().get("keys") or []
    except Exception as erro:
        # Sem JWKS o decode cai no caminho legado, que exige segredo real: uma
        # falha de rede nunca vira "token aceito". Se já havia chave boa em
        # mãos, ela continua valendo — chave pública velha verifica igual.
        logger.warning("Não foi possível buscar o JWKS do Supabase: %s", erro)
        _jwks_cache["em"] = agora
        if _jwks_cache["chaves"]:
            return _guardar(_jwks_cache["chaves"], "cache", erro)
        return _guardar(_jwks_embutido(), "embutida", erro)
    if not chaves and _jwks_cache["chaves"]:
        # Resposta vazia com chave boa guardada: o JWKS do Supabase é servido
        # por borda, e durante uma rotação uma borda responde a lista nova e a
        # vizinha ainda a antiga. Descartar o que funciona por causa de uma
        # resposta dessas derrubaria logins válidos de forma intermitente.
        logger.warning("JWKS voltou vazio; mantendo as %d chave(s) já conhecidas.",
                       len(_jwks_cache["chaves"]))
        _jwks_cache["em"] = agora
        return _guardar(_jwks_cache["chaves"], "cache")
    if not chaves:
        # Projeto que ainda assina com o segredo legado não publica chave
        # nenhuma. Não é erro: é o outro caminho de verificação.
        _jwks_cache["em"] = agora
        return _guardar(_jwks_embutido(), "embutida" if _jwks_embutido() else "vazio")
    _jwks_cache["em"] = agora
    _jwks_cache["chaves"] = chaves
    return _guardar(chaves, "rede")


def _chave_publica(kid: str):
    """
    A chave pública do `kid`, ou None. Chaves simétricas (`oct`) são ignoradas
    de propósito: o Supabase não as publica, então uma que aparecesse aqui só
    poderia vir de uma resposta adulterada.
    """
    if not kid:
        return None
    for chave in _jwks():
        if chave.get("kid") == kid and chave.get("kty") != "oct":
            return chave
    # kid desconhecido pode ser rotação recém-feita: uma releitura, e só.
    for chave in _jwks(forcar=True):
        if chave.get("kid") == kid and chave.get("kty") != "oct":
            return chave
    return None


def secret_confere_com_projeto():
    """
    O segredo configurado é mesmo o do projeto que o frontend usa?

    Devolve True/False, ou None quando não dá para afirmar nada (sem segredo
    configurado, ou sem a chave anon para comparar).
    """
    if not jwt_configured():
        return None
    chave = anon_key()
    if not chave:
        return None
    try:
        jwt.decode(
            chave,
            jwt_secret(),
            algorithms=["HS256"],
            # Só interessa a assinatura: a chave anon tem validade de anos e a
            # comparação continua valendo mesmo se um dia ela expirar.
            options={"verify_aud": False, "verify_exp": False},
        )
        return True
    except JWTError:
        return False


def variaveis_de_outro_projeto() -> dict:
    """
    Variáveis de ambiente descartadas por serem de outro projeto: nome → o
    projeto que elas indicavam. Reavalia antes de responder porque o registro é
    efeito de quem lê as variáveis, e quem pergunta isso pode ser o primeiro a
    olhar (o preflight roda antes do primeiro login).
    """
    anon_key()
    supabase_url()
    return dict(_env_ignoradas)


def verificacao_por_jwks() -> bool:
    """Há chave pública em mãos? Então dá para validar login sem segredo."""
    return bool(_jwks())


def estado_das_chaves() -> dict:
    """De onde veio o chaveiro em uso — o que a conferência de prontidão e o
    diagnóstico leem para explicar uma recusa."""
    chaves = _jwks()
    return {
        "quantidade": len(chaves),
        "kids": [c.get("kid") for c in chaves],
        "origem": _jwks_cache["origem"],     # rede | cache | embutida | vazio
        "erro": _jwks_cache["erro"],
    }


def _origem_do_token(token: str) -> str:
    """
    De qual projeto Supabase veio o token, lido sem verificar assinatura.

    Só para o log. A causa mais comum de 401 em todo login é o
    `SUPABASE_JWT_SECRET` do servidor pertencer a outro projeto — e a única
    forma de ver isso é comparar o `iss` do token com o projeto do frontend.
    """
    try:
        claims = jwt.get_unverified_claims(token)
        header = jwt.get_unverified_header(token)
        return (
            f"iss={claims.get('iss')} alg={header.get('alg')} "
            f"kid={header.get('kid')} exp={claims.get('exp')}"
        )
    except Exception:  # token malformado: não é o caso interessante
        return "token ilegível"


# Algoritmos aceitos por chave do JWKS. Fixos de propósito: se o algoritmo
# viesse do cabeçalho do token, quem o forja escolheria como ele é conferido.
_ALGS_JWKS = {"EC": ["ES256"], "RSA": ["RS256"], "OKP": ["EdDSA"]}


@dataclass
class Veredito:
    """
    O resultado de examinar um token, com o motivo em português.

    `codigo` é para o programa decidir (401 x 503, o que a tela sugere);
    `detalhe` é a frase que a pessoa lê. Os dois saem do **mesmo** exame que
    autentica de verdade — um diagnóstico que refaz a conta por conta própria
    acaba discordando do que o login faz, e aí o texto na tela vira mais um
    palpite para conferir.
    """
    ok: bool
    codigo: str                        # ok | ilegivel | sem_chave | expirado |
                                       # assinatura | sem_sub
    detalhe: str
    via: str = "-"                     # jwks | segredo | -
    kid: Optional[str] = None
    alg: Optional[str] = None
    payload: Optional[dict] = field(default=None, repr=False)


def _kid_para_texto(kid) -> str:
    """
    O `kid` vem do token, que é de quem chama. Só sai em mensagem depois de
    passar por aqui — texto de terceiro não entra em resposta nossa cru.
    """
    if not kid:
        return "(sem kid)"
    kid = str(kid)[:48]
    return kid if re.fullmatch(r"[A-Za-z0-9._:-]+", kid) else "(kid ilegível)"


def avaliar_token(token: str) -> Veredito:
    """
    Examina o token e diz o que aconteceu. Não levanta exceção: quem chama
    decide o que fazer com a recusa (autenticação levanta 401/503; diagnóstico
    escreve na tela).
    """
    try:
        header = jwt.get_unverified_header(token)
    except Exception:
        return Veredito(False, "ilegivel",
                        "O que veio no cabeçalho Authorization não é um JWT legível.")
    kid, alg = header.get("kid"), header.get("alg")

    chave = _chave_publica(kid)
    if chave is not None:
        algs = _ALGS_JWKS.get(chave.get("kty"), [])
        # A chave pode declarar o algoritmo dela; só vale se for um dos aceitos.
        if chave.get("alg") in algs:
            algs = [chave["alg"]]
        try:
            payload = jwt.decode(token, chave, algorithms=algs, options={"verify_aud": False})
        except ExpiredSignatureError:
            return Veredito(False, "expirado", "A sessão expirou.", "jwks", kid, alg)
        except JWTError as erro:
            return Veredito(
                False, "assinatura",
                f"A assinatura não confere com a chave pública {_kid_para_texto(kid)} "
                f"publicada pelo projeto ({type(erro).__name__}).",
                "jwks", kid, alg,
            )
    else:
        if not jwt_configured():
            # Nunca cair para decode com segredo fraco/vazio: seria aceitar token
            # forjado. Erro de servidor, não do cliente.
            return Veredito(
                False, "sem_chave",
                f"O servidor não tem com o que conferir este token: o JWKS do "
                f"projeto não traz a chave {_kid_para_texto(kid)} e "
                f"SUPABASE_JWT_SECRET está ausente ou curto demais.",
                "-", kid, alg,
            )
        try:
            payload = jwt.decode(
                token,
                jwt_secret(),
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except ExpiredSignatureError:
            return Veredito(False, "expirado", "A sessão expirou.", "segredo", kid, alg)
        except JWTError as erro:
            return Veredito(
                False, "assinatura",
                f"A assinatura não confere com o SUPABASE_JWT_SECRET configurado "
                f"({type(erro).__name__}).",
                "segredo", kid, alg,
            )

    if not payload.get("sub"):
        return Veredito(False, "sem_sub", "O token não diz de quem é (sem `sub`).",
                        "jwks" if chave is not None else "segredo", kid, alg)
    return Veredito(True, "ok", "Token verificado.",
                    "jwks" if chave is not None else "segredo", kid, alg, payload)


def decode_jwt(token: str) -> dict:
    """
    Valida um JWT do Supabase. Levanta HTTPException em qualquer caminho que
    não seja "assinatura conferida com chave real".
    """
    v = avaliar_token(token)
    if v.ok:
        return v.payload

    if v.codigo == "sem_chave":
        logger.error("%s (%s)", v.detalhe, _origem_do_token(token))
        raise HTTPException(
            status_code=503,
            detail="Autenticação indisponível: servidor sem chave de verificação "
                   "configurada. Abra /api/auth/diagnostico para ver o que falta.",
        )

    # O motivo precisa aparecer no log: "assinatura não confere" e "token
    # expirado" pedem ações opostas, e da tela os dois chegam como o mesmo 401.
    logger.warning("JWT recusado (%s, via %s): %s — %s",
                   v.codigo, v.via, v.detalhe, _origem_do_token(token))
    if v.codigo == "assinatura" and v.via == "segredo" and v.kid:
        # Assinatura de uma signing key que o JWKS não publica: chave atual é
        # um segredo compartilhado, e esse material o Supabase não entrega.
        # Nenhum valor de SUPABASE_JWT_SECRET resolve.
        logger.error(
            "O token foi assinado pela signing key kid=%s, que não está no JWKS "
            "do projeto — sinal de chave atual do tipo 'segredo compartilhado', "
            "cujo material não é extraível do Supabase. Troque a chave de "
            "assinatura do projeto para assimétrica (ECC P-256).", v.kid,
        )
    raise HTTPException(status_code=401, detail=v.detalhe)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_security),
) -> dict:
    return decode_jwt(credentials.credentials or "")


# ── Diagnóstico ──────────────────────────────────────────────────────────────
# Um 401 no login tem cinco causas plausíveis e todas produzem exatamente a
# mesma tela. Antes disto o único lugar com a resposta era o log do servidor —
# que em serverless significa abrir o painel do deploy, achar a invocação certa
# e ler. Na prática ninguém lê, e a pessoa troca variáveis no escuro por horas.
#
# O que sai daqui é o que o próprio verificador acabou de fazer, mais o estado
# das duas fontes de chave. Nada de segredo: do `SUPABASE_JWT_SECRET` só saem
# "existe?" e o tamanho.

def _claims_sem_verificar(token: str) -> dict:
    try:
        return jwt.get_unverified_claims(token)
    except Exception:
        return {}


def _ref_do_iss(iss: str) -> str:
    """`https://xxxx.supabase.co/auth/v1` → `xxxx`."""
    achado = re.search(r"https://([a-z0-9]+)\.supabase\.co", iss or "")
    return achado.group(1) if achado else ""


def _veredito_de_configuracao(estado_chaves: dict) -> dict:
    """Sem token para examinar, ainda dá para dizer se o servidor tem com o que
    verificar um login quando ele chegar."""
    if estado_chaves["quantidade"]:
        return {
            "situacao": "sem_token",
            "resumo": "Nenhuma credencial foi apresentada, mas o servidor tem "
                      f"{estado_chaves['quantidade']} chave(s) pública(s) do projeto "
                      "para verificar o login.",
            "como_resolver": "Entre com o provedor e repita este diagnóstico se o "
                             "login ainda for recusado.",
        }
    if jwt_configured():
        return {
            "situacao": "sem_token",
            "resumo": "Nenhuma credencial foi apresentada. O projeto não publica "
                      "chave pública; a verificação depende do SUPABASE_JWT_SECRET, "
                      "que está configurado."
                      + ("" if secret_confere_com_projeto() is not False else
                         " ATENÇÃO: esse segredo NÃO é o do projeto desta anon key."),
            "como_resolver": "Entre com o provedor e repita este diagnóstico se o "
                             "login ainda for recusado.",
        }
    return {
        "situacao": "servidor_sem_chave",
        "resumo": "O servidor não tem nenhuma forma de verificar login: o projeto "
                  "não publica chave pública e não há SUPABASE_JWT_SECRET.",
        "como_resolver": "No Supabase, em JWT Keys, troque a chave de assinatura "
                         "para assimétrica (ECC P-256) — ela é publicada e dispensa "
                         "segredo no deploy. Ou defina SUPABASE_JWT_SECRET nas "
                         "variáveis de ambiente do deploy.",
    }


def _veredito_do_token(v: Veredito, ref_token: str, ref_projeto: str,
                       estado_chaves: dict) -> dict:
    if v.ok:
        return {
            "situacao": "ok",
            "resumo": f"Token verificado pela {'chave pública do projeto' if v.via == 'jwks' else 'chave (JWT secret) configurada'}.",
            "como_resolver": "Nada a fazer: a autenticação está funcionando.",
        }

    if v.codigo == "ilegivel":
        return {"situacao": "token_ilegivel", "resumo": v.detalhe,
                "como_resolver": "Saia e entre de novo para o navegador pedir uma "
                                 "credencial nova."}

    if ref_token and ref_projeto and ref_token != ref_projeto:
        return {
            "situacao": "projeto_diferente",
            "resumo": f"O login foi feito no projeto Supabase '{ref_token}', mas este "
                      f"app é do projeto '{ref_projeto}'. Nenhuma chave de um projeto "
                      "valida token do outro.",
            "como_resolver": "O navegador está usando um app.js de outro projeto — "
                             "quase sempre versão antiga em cache, ou sessão guardada "
                             "de antes da troca. Saia, limpe os dados do site e entre "
                             "de novo (Ctrl+Shift+R). Se persistir, confira se o "
                             "deploy no ar é o commit atual.",
        }

    if v.codigo == "expirado":
        return {"situacao": "expirado",
                "resumo": "A credencial apresentada está expirada.",
                "como_resolver": "Saia e entre de novo. Se acontecer sempre, "
                                 "verifique o relógio do servidor."}

    if v.codigo == "sem_chave":
        return {
            "situacao": "servidor_sem_chave",
            "resumo": v.detalhe,
            "como_resolver": (
                "O token foi assinado por uma signing key que o JWKS do projeto não "
                f"publica ({_kid_para_texto(v.kid)}). No Supabase, em JWT Keys, "
                "troque a chave atual por uma assimétrica (ECC P-256): ela passa a "
                "ser publicada e o servidor verifica sozinho. Alternativa: definir "
                "SUPABASE_JWT_SECRET no ambiente do deploy — só funciona se a chave "
                "atual ainda for o segredo legado."
                + (f" A busca do JWKS também está falhando: {estado_chaves['erro']}."
                   if estado_chaves["erro"] else "")
            ),
        }

    if v.codigo == "assinatura" and v.via == "jwks":
        return {
            "situacao": "assinatura_nao_confere",
            "resumo": v.detalhe,
            "como_resolver": "A credencial não foi emitida pela chave que o projeto "
                             "publica. Saia e entre de novo; se persistir, houve "
                             "rotação de chave e o cache do servidor está velho "
                             "(passa sozinho em até 10 minutos).",
        }

    if v.codigo == "assinatura" and v.via == "segredo":
        confere = secret_confere_com_projeto()
        if v.kid:
            return {
                "situacao": "chave_de_assinatura_nao_publicada",
                "resumo": f"O token foi assinado pela signing key "
                          f"{_kid_para_texto(v.kid)}, que não está entre as "
                          f"{estado_chaves['quantidade']} chave(s) que o servidor "
                          "conhece — então ele tentou o JWT secret legado, e a "
                          "assinatura não confere com ele.",
                "como_resolver": (
                    (f"A busca do JWKS está falhando ({estado_chaves['erro']}), então "
                     "o servidor pode simplesmente não estar enxergando a chave certa; "
                     "libere a saída de rede do deploy para o Supabase. "
                     if estado_chaves["erro"] else "")
                    + "Se a rede está bem, a chave atual do projeto é do tipo 'segredo "
                      "compartilhado' — esse material o Supabase não entrega, e nenhum "
                      "valor de SUPABASE_JWT_SECRET resolve. Em JWT Keys, troque a "
                      "chave atual por uma assimétrica (ECC P-256)."
                ),
            }
        if confere is False:
            return {
                "situacao": "segredo_de_outro_projeto",
                "resumo": "O SUPABASE_JWT_SECRET configurado é de outro projeto: ele "
                          "não valida nem a anon key deste. Todo login real vira 401.",
                "como_resolver": "Copie o JWT Secret do MESMO projeto da anon key "
                                 "(Supabase > Settings > API > JWT Settings) e atualize "
                                 "a variável no ambiente do deploy — e só nele; mudar "
                                 "o .env local não muda o que está no ar.",
            }
        return {
            "situacao": "assinatura_nao_confere",
            "resumo": v.detalhe,
            "como_resolver": "O segredo confere com o projeto, mas não com este token. "
                             "Saia e entre de novo; se persistir, o projeto trocou de "
                             "chave de assinatura e o segredo legado deixou de valer.",
        }

    return {"situacao": v.codigo, "resumo": v.detalhe,
            "como_resolver": "Saia e entre de novo."}


def diagnostico(token: str = "") -> dict:
    """
    Por que este token foi (ou seria) recusado, com o que fazer a respeito.

    Aceita token vazio: sem credencial, relata o estado das chaves do servidor,
    que já responde "dá para logar aqui?".
    """
    token = (token or "").strip()
    estado_chaves = estado_das_chaves()
    ref_projeto = _ref_do_iss(supabase_url())
    anon_de_env = anon_key() != SUPABASE_ANON_KEY_PADRAO
    relatorio = {
        "projeto": {
            "ref": ref_projeto,
            "url": supabase_url(),
            "anon_key_de": "variável de ambiente" if anon_de_env
                           else "código (static/js/app.js)",
            # Variável do deploy apontando para outro projeto: ela não afeta a
            # verificação (é descartada), mas continua no painel confundindo
            # quem for conferir. Sai nomeada para poder ser apagada.
            "variaveis_ignoradas": variaveis_de_outro_projeto(),
        },
        "chaves_publicas": {
            "quantidade": estado_chaves["quantidade"],
            "kids": estado_chaves["kids"],
            "origem": estado_chaves["origem"],
            "erro_na_busca": estado_chaves["erro"],
        },
        "segredo_legado": {
            "configurado": jwt_configured(),
            "tamanho": len(jwt_secret()),
            "confere_com_o_projeto": secret_confere_com_projeto(),
        },
        "token": {"apresentado": bool(token)},
    }

    if not token:
        relatorio["veredito"] = _veredito_de_configuracao(estado_chaves)
        return relatorio

    v = avaliar_token(token)
    claims = _claims_sem_verificar(token)
    ref_token = _ref_do_iss(claims.get("iss", ""))
    relatorio["token"].update({
        "tipo": "supabase",
        "alg": v.alg,
        "kid": _kid_para_texto(v.kid),
        "kid_esta_no_jwks": bool(v.kid) and v.kid in estado_chaves["kids"],
        "emissor": claims.get("iss"),
        "projeto_do_token": ref_token,
        "expira_em": claims.get("exp"),
        "verificado": v.ok,
        "verificado_por": v.via,
        "motivo": v.detalhe,
    })
    relatorio["veredito"] = _veredito_do_token(v, ref_token, ref_projeto, estado_chaves)
    return relatorio


def rate_limit_key(request: Request) -> str:
    """
    Chave de rate limit por usuário autenticado, com fallback para IP. Atrás de
    proxy todos compartilham IP — limitar só por IP puniria todo mundo junto.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return get_remote_address(request)

    token = auth[7:]
    if jwt_configured():
        try:
            payload = jwt.decode(
                token, jwt_secret(), algorithms=["HS256"],
                options={"verify_aud": False},
            )
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except JWTError:
            pass  # token inválido → cai no IP
    return get_remote_address(request)
