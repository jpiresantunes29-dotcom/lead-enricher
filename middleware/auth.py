from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from slowapi.util import get_remote_address
import os

_security = HTTPBearer()
_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")

# Demo mode — permite testar a plataforma sem OAuth real.
# Habilite com DEMO_MODE=1 (default: ligado em dev).
DEMO_MODE_ENABLED = os.getenv("DEMO_MODE", "1") == "1"
DEMO_TOKEN_PREFIX = "demo-session-"
DEMO_USER_ID = "demo-user-2026"
_DEMO_PAYLOAD = {
    "sub": DEMO_USER_ID,
    "email": "demo@leadenricher.app",
    "_demo": True,
}


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_security),
) -> dict:
    token = credentials.credentials
    # Demo bypass: qualquer token "demo-session-*" libera o user demo.
    if DEMO_MODE_ENABLED and token.startswith(DEMO_TOKEN_PREFIX):
        return dict(_DEMO_PAYLOAD)
    try:
        payload = jwt.decode(
            token,
            _JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        if not payload.get("sub"):
            raise HTTPException(status_code=401, detail="Token inválido.")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")


def rate_limit_key(request: Request) -> str:
    """
    Chave de rate limit por usuário autenticado (sub do JWT), com fallback
    para IP. Atrás de proxy todos os usuários compartilham IP — limitar por
    IP puniria todo mundo junto.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
        try:
            payload = jwt.decode(
                token, _JWT_SECRET, algorithms=["HS256"],
                options={"verify_aud": False},
            )
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except JWTError:
            pass  # token inválido → cai no IP
    return get_remote_address(request)
