"""
Autenticação: JWT do Supabase + sessão demo.

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
import time

import requests
from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

_security = HTTPBearer()

# O JWT secret do Supabase tem 40+ caracteres. Qualquer coisa menor que isso é
# variável mal preenchida (placeholder do .env.example, string vazia) e não
# pode ser usada para validar assinatura.
MIN_SECRET_LENGTH = 32

# Demo mode — permite testar a plataforma sem OAuth real.
# Ligado por padrão; DEMO_MODE=0 desliga (produção com clientes reais).
DEMO_TOKEN_PREFIX = "demo-session-"
DEMO_USER_PREFIX = "demo-"


def demo_mode_enabled() -> bool:
    """Lido a cada chamada para que testes e deploys possam alternar sem reimport."""
    return os.getenv("DEMO_MODE", "1") == "1"


def jwt_secret() -> str:
    return (os.getenv("SUPABASE_JWT_SECRET") or "").strip()


def jwt_configured() -> bool:
    """Há segredo de verdade para validar assinatura?"""
    return len(jwt_secret()) >= MIN_SECRET_LENGTH


# Chave pública (anon) do projeto Supabase — a mesma de `static/js/app.js`.
# Ela é um JWT assinado com o JWT Secret do projeto, o que a torna um gabarito:
# um segredo que não valida esta chave é de **outro projeto**, e aí todo login
# real leva 401 mesmo com tudo o mais certo. Trocou de projeto? Troque os dois.
SUPABASE_ANON_KEY_PADRAO = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNnZmJwbG96cnBqbnN1ZG9hd3B6Iiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3ODY1NTAyMjksImV4cCI6MjEwMjEyNjIyOX0."
    "ry1F3IaKFd7yMFD8-pjj2j06xTZl6Xm8iUnweztCSMA"
)


def anon_key() -> str:
    return (os.getenv("SUPABASE_ANON_KEY") or SUPABASE_ANON_KEY_PADRAO).strip()


def supabase_url() -> str:
    """
    URL do projeto de onde vêm as chaves de verificação.

    Manda o `ref` da própria anon key, não `SUPABASE_URL`. Não é preciosismo:
    a anon key é a que o navegador usa para logar, então é ela que define
    contra qual projeto o usuário se autenticou. Buscar chave em outro projeto
    seria conferir assinatura com o chaveiro errado — e uma variável esquecida
    de um projeto antigo (ou apontada para um projeto de terceiro) viraria
    exatamente isso.

    `SUPABASE_URL` só entra quando não dá para derivar — instalação
    self-hosted, em que a anon key não traz `ref`.
    """
    try:
        ref = jwt.get_unverified_claims(anon_key()).get("ref")
    except Exception:
        ref = None
    env = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    if ref:
        derivada = f"https://{ref}.supabase.co"
        if env and ref not in env:
            logger.warning(
                "SUPABASE_URL (%s) não é o projeto da anon key (ref=%s); usando "
                "o projeto da anon key para buscar as chaves de verificação.",
                env, ref,
            )
        return derivada
    return env


# Cache do JWKS: uma busca por chave nova, não uma por requisição. O TTL curto
# é o que faz uma rotação de chave entrar sozinha, sem redeploy.
_JWKS_TTL_SEGUNDOS = 600
_JWKS_TTL_ERRO = 30
_jwks_cache = {"em": 0.0, "chaves": []}


def _jwks(forcar: bool = False) -> list:
    agora = time.monotonic()
    if not forcar and _jwks_cache["chaves"] and agora - _jwks_cache["em"] < _JWKS_TTL_SEGUNDOS:
        return _jwks_cache["chaves"]
    if not forcar and not _jwks_cache["chaves"] and agora - _jwks_cache["em"] < _JWKS_TTL_ERRO:
        return []
    base = supabase_url()
    if not base:
        return []
    try:
        resp = requests.get(f"{base}/auth/v1/.well-known/jwks.json", timeout=5)
        resp.raise_for_status()
        chaves = resp.json().get("keys") or []
    except Exception as erro:
        # Sem JWKS o decode cai no caminho legado, que exige segredo real: uma
        # falha de rede nunca vira "token aceito".
        logger.warning("Não foi possível buscar o JWKS do Supabase: %s", erro)
        _jwks_cache["em"] = agora
        return []
    _jwks_cache["em"] = agora
    _jwks_cache["chaves"] = chaves
    return chaves


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


def verificacao_por_jwks() -> bool:
    """O projeto publica chave pública? Então dá para validar login sem segredo."""
    return bool(_jwks())


def is_demo_user(user_id: str) -> bool:
    """User demo (efêmero, por navegador). UUIDs do Supabase nunca têm esse prefixo."""
    return bool(user_id) and user_id.startswith(DEMO_USER_PREFIX)


def is_demo_token(token: str) -> bool:
    return bool(token) and token.startswith(DEMO_TOKEN_PREFIX)


def demo_identity(token: str) -> dict:
    """Cada token demo vira um user próprio — sessões não compartilham dados."""
    suffix = token[len(DEMO_TOKEN_PREFIX):][:32] or "anon"
    return {
        "sub": f"{DEMO_USER_PREFIX}{suffix}",
        "email": "demo@leadenricher.app",
        "_demo": True,
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


def decode_jwt(token: str) -> dict:
    """
    Valida um JWT do Supabase. Levanta HTTPException em qualquer caminho que
    não seja "assinatura conferida com chave real".
    """
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

    chave = _chave_publica(kid)
    if chave is not None:
        algs = _ALGS_JWKS.get(chave.get("kty"), [])
        # A chave pode declarar o algoritmo dela; só vale se for um dos aceitos.
        if chave.get("alg") in algs:
            algs = [chave["alg"]]
        try:
            payload = jwt.decode(token, chave, algorithms=algs, options={"verify_aud": False})
        except JWTError as erro:
            logger.warning("JWT recusado (JWKS): %s — %s",
                           type(erro).__name__, _origem_do_token(token))
            raise HTTPException(status_code=401, detail="Token inválido ou expirado.")
    else:
        if not jwt_configured():
            # Nunca cair para decode com segredo fraco/vazio: seria aceitar token
            # forjado. Erro de servidor, não do cliente.
            logger.error(
                "Sem chave para verificar o token: o JWKS do projeto não tem o "
                "kid=%s e SUPABASE_JWT_SECRET está ausente ou curto demais.", kid,
            )
            raise HTTPException(
                status_code=503,
                detail="Autenticação indisponível: servidor sem chave de verificação configurada.",
            )
        try:
            payload = jwt.decode(
                token,
                jwt_secret(),
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except JWTError as erro:
            # O motivo precisa aparecer no log: "assinatura não confere" e "token
            # expirado" pedem ações opostas, e da tela os dois chegam como o
            # mesmo 401 mudo.
            logger.warning("JWT recusado (segredo legado): %s — %s",
                           type(erro).__name__, _origem_do_token(token))
            if kid:
                # Assinatura de uma signing key que o JWKS não publica: chave
                # atual é um segredo compartilhado, e esse material o Supabase
                # não entrega. Nenhum valor de SUPABASE_JWT_SECRET resolve.
                logger.error(
                    "O token foi assinado pela signing key kid=%s, que não está "
                    "no JWKS do projeto — sinal de chave atual do tipo 'segredo "
                    "compartilhado', cujo material não é extraível do Supabase. "
                    "Troque a chave de assinatura do projeto para assimétrica "
                    "(ECC P-256) para que ela seja publicada no JWKS.", kid,
                )
            raise HTTPException(status_code=401, detail="Token inválido ou expirado.")

    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Token inválido.")
    return payload


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_security),
) -> dict:
    token = credentials.credentials or ""
    if demo_mode_enabled() and is_demo_token(token):
        return demo_identity(token)
    return decode_jwt(token)


def rate_limit_key(request: Request) -> str:
    """
    Chave de rate limit por usuário autenticado, com fallback para IP. Atrás de
    proxy todos compartilham IP — limitar só por IP puniria todo mundo junto.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return get_remote_address(request)

    token = auth[7:]
    # Sessão demo tem identidade própria: sem isto, todos os visitantes demo
    # dividem o balde do IP e um derruba o limite do outro.
    if demo_mode_enabled() and is_demo_token(token):
        return f"user:{demo_identity(token)['sub']}"

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
