from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from models.database import get_db, Profile
from middleware.auth import get_current_user, DEMO_USER_ID

router = APIRouter(prefix="/api", tags=["auth"])


def get_or_create_profile(db: Session, user_id: str) -> Profile:
    profile = db.query(Profile).filter(Profile.id == user_id).first()
    if not profile:
        # User demo nasce no plano Pro para que toda a UI seja explorável
        # sem topar paywall nem cota de 3.
        is_demo = user_id == DEMO_USER_ID
        profile = Profile(
            id=user_id,
            plan="pro" if is_demo else "free",
            searches_limit=500 if is_demo else 5,
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
        "quota_reset_at": profile.quota_reset_at.isoformat() if profile.quota_reset_at else None,
    }
