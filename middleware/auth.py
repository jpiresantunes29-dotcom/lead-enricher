"""
Autenticação: JWT do Supabase + sessão demo.

Regra que não pode ser quebrada: **JWT só é decodificado com um segredo real**.
Com `SUPABASE_JWT_SECRET` ausente o valor cai para string vazia, e o HS256
valida qualquer token assinado com "" — ou seja, qualquer pessoa assumiria
qualquer `sub`. Por isso o segredo é checado antes de cada decode e a ausência
vira 503 (erro de configuração do servidor), nunca "token aceito".
"""
import logging
import os

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


def decode_jwt(token: str) -> dict:
    """
    Valida um JWT do Supabase. Levanta HTTPException em qualquer caminho que
    não seja "assinatura conferida com segredo real".
    """
    if not jwt_configured():
        # Nunca cair para decode com segredo fraco/vazio: seria aceitar token
        # forjado. Erro de servidor, não do cliente.
        logger.error(
            "SUPABASE_JWT_SECRET ausente ou curto demais — autenticação por JWT "
            "está desligada. Defina a variável de ambiente para aceitar logins."
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
    except JWTError:
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
