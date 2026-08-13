from fastapi import APIRouter, Depends, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from slowapi import Limiter
from sqlalchemy.orm import Session
from models.database import get_db, Profile
from middleware.auth import diagnostico, get_current_user, is_demo_user, rate_limit_key

router = APIRouter(prefix="/api", tags=["auth"])
limiter = Limiter(key_func=rate_limit_key)

# Sem `auto_error`: o diagnóstico precisa responder também para quem chega sem
# credencial nenhuma — "o servidor tem chave para verificar login?" é metade da
# resposta, e é justamente a metade que falta quando nada funciona.
_bearer_opcional = HTTPBearer(auto_error=False)


def get_or_create_profile(db: Session, user_id: str) -> Profile:
    from routers.billing import _next_reset  # import tardio evita ciclo

    profile = db.query(Profile).filter(Profile.id == user_id).first()
    if not profile:
        # User demo nasce no plano Pro para que toda a UI seja explorável
        # sem topar paywall nem cota do plano free.
        is_demo = is_demo_user(user_id)
        profile = Profile(
            id=user_id,
            plan="pro" if is_demo else "free",
            searches_limit=500 if is_demo else 5,
            # Créditos de revelação da extensão (medidor separado das buscas)
            reveals_limit=300 if is_demo else 5,
            # Sem esta data o _maybe_reset_quota nunca renova o plano free —
            # a UI promete "buscas neste ciclo", então o ciclo precisa existir.
            quota_reset_at=_next_reset(),
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.get("/auth/diagnostico")
@limiter.limit("20/minute")
def diagnostico_de_login(
    request: Request,
    credenciais: HTTPAuthorizationCredentials = Security(_bearer_opcional),
):
    """
    Por que o login está sendo recusado — em português, na tela.

    A tela mostra isto quando /api/me responde 401 ou 503. Sem ele, as cinco
    causas possíveis de um 401 produzem a mesma mensagem e o único lugar com a
    resposta é o log da função no painel do deploy.

    Não devolve segredo: do JWT secret saem apenas "existe?", o tamanho e se
    ele valida a anon key do projeto. As chaves do JWKS já são públicas.
    """
    return diagnostico(credenciais.credentials if credenciais else "")


@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("sub")
    profile = get_or_create_profile(db, user_id)
    return {
        "id": profile.id,
        "email": current_user.get("email"),
        "plan": profile.plan,
        "searches_used": profile.searches_used,
        "searches_limit": profile.searches_limit,
        "reveals_used": profile.reveals_used or 0,
        "reveals_limit": profile.reveals_limit if profile.reveals_limit is not None else 0,
        "quota_reset_at": profile.quota_reset_at.isoformat() if profile.quota_reset_at else None,
    }
