import os
import logging
from datetime import datetime, UTC, timedelta

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from models.database import get_db, Profile, StripeEvent
from middleware.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

PLAN_PRICE_IDS = {
    "pro": os.getenv("STRIPE_PRICE_PRO", ""),
    "enterprise": os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
}

PLAN_LIMITS = {
    "free": 5,
    "pro": 500,
    "enterprise": -1,  # -1 = ilimitado
}


def _next_reset() -> datetime:
    """Retorna a data de reset 30 dias a partir de agora."""
    return datetime.now(UTC) + timedelta(days=30)


def _maybe_reset_quota(profile: Profile) -> bool:
    """Reseta searches_used se o prazo mensal passou. Retorna True se resetou."""
    if profile.quota_reset_at and datetime.now(UTC) >= profile.quota_reset_at.replace(tzinfo=UTC):
        profile.searches_used = 0
        profile.quota_reset_at = _next_reset()
        return True
    return False


@router.post("/checkout")
def create_checkout(
    body: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    plan = body.get("plan", "pro")
    price_id = PLAN_PRICE_IDS.get(plan)
    if not price_id:
        raise HTTPException(status_code=400, detail="Plano inválido.")

    user_id = current_user.get("sub")
    email = current_user.get("email")
    profile = db.query(Profile).filter(Profile.id == user_id).first()

    customer_id = profile.stripe_customer_id if profile else None
    if not customer_id and email:
        customer = stripe.Customer.create(email=email, metadata={"user_id": user_id})
        customer_id = customer.id
        if profile:
            profile.stripe_customer_id = customer_id
            db.commit()

    success_url = os.getenv("APP_URL", "http://localhost:8000") + "/app?upgraded=1"
    cancel_url = os.getenv("APP_URL", "http://localhost:8000") + "/app"

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user_id, "plan": plan},
    )
    return {"url": session.url}


@router.post("/portal")
def billing_portal(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Portal do cliente Stripe — gerenciar/cancelar assinatura (Configurações)."""
    user_id = current_user.get("sub")
    profile = db.query(Profile).filter(Profile.id == user_id).first()
    if not profile or not profile.stripe_customer_id:
        raise HTTPException(status_code=400, detail="Nenhuma assinatura ativa para gerenciar.")

    return_url = os.getenv("APP_URL", "http://localhost:8000") + "/app#settings"
    try:
        session = stripe.billing_portal.Session.create(
            customer=profile.stripe_customer_id,
            return_url=return_url,
        )
    except stripe.error.StripeError as e:
        logger.warning("Stripe portal error for user %s: %s", user_id, e)
        raise HTTPException(status_code=502, detail="Portal de assinatura indisponível no momento.")
    return {"url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Assinatura inválida.")

    event_id = event["id"]

    # Idempotência: ignora eventos já processados
    if db.query(StripeEvent).filter(StripeEvent.id == event_id).first():
        logger.info("Stripe event %s already processed, skipping.", event_id)
        return {"received": True}

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("metadata", {}).get("user_id")
        plan = session.get("metadata", {}).get("plan", "pro")
        customer_id = session.get("customer")

        if user_id:
            profile = db.query(Profile).filter(Profile.id == user_id).first()
            if profile and profile.plan != plan:
                profile.plan = plan
                profile.searches_limit = PLAN_LIMITS.get(plan, 500)
                profile.searches_used = 0
                profile.quota_reset_at = _next_reset()
                if customer_id:
                    profile.stripe_customer_id = customer_id
                logger.info("User %s upgraded to plan %s.", user_id, plan)

    elif event["type"] in ("customer.subscription.deleted", "customer.subscription.paused"):
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        if customer_id:
            profile = db.query(Profile).filter(Profile.stripe_customer_id == customer_id).first()
            if profile and profile.plan != "free":
                profile.plan = "free"
                profile.searches_limit = PLAN_LIMITS["free"]
                profile.quota_reset_at = _next_reset()  # free também renova por ciclo
                logger.info("Customer %s downgraded to free.", customer_id)

    elif event["type"] == "invoice.paid":
        # Reset mensal automático quando a fatura do ciclo é paga
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        if customer_id:
            profile = db.query(Profile).filter(Profile.stripe_customer_id == customer_id).first()
            if profile and profile.plan != "free":
                profile.searches_used = 0
                profile.quota_reset_at = _next_reset()
                logger.info("Monthly quota reset for customer %s.", customer_id)

    db.add(StripeEvent(id=event_id))
    db.commit()

    return {"received": True}
