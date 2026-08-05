from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from models.database import get_db, Profile
from middleware.auth import get_current_user, is_demo_user

router = APIRouter(prefix="/api", tags=["auth"])


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
