"""
Configuração de integração CRM por usuário (proposta v3 §4).
Cada usuário pode ativar/configurar múltiplos provedores (webhook, Dynamics, HubSpot, etc.).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.database import get_db, CRMConnection
from middleware.auth import get_current_user

router = APIRouter(prefix="/api", tags=["crm-config"])


class CRMConnectionCreate(BaseModel):
    provider: str
    webhook_url: str | None = None
    webhook_secret: str | None = None
    access_token: str | None = None
    account_id: str | None = None


class CRMConnectionOut(BaseModel):
    provider: str
    webhook_url: str | None
    is_active: bool

    class Config:
        from_attributes = True


@router.get("/crm/connections")
def list_connections(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Lista conexões CRM configuradas do usuário."""
    user_id = current_user.get("sub")
    conns = db.query(CRMConnection).filter(CRMConnection.user_id == user_id).all()
    return [
        {
            "provider": c.provider,
            "is_active": c.is_active,
            "webhook_configured": bool(c.webhook_url),
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in conns
    ]


@router.post("/crm/connections")
def create_connection(
    body: CRMConnectionCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Cria ou atualiza uma conexão CRM para o usuário."""
    user_id = current_user.get("sub")
    if not body.provider:
        raise HTTPException(status_code=422, detail="provider é obrigatório")

    # Valida provider
    valid_providers = {"webhook", "dynamics", "hubspot", "pipedrive"}
    if body.provider not in valid_providers:
        raise HTTPException(
            status_code=422,
            detail=f"provider deve ser um de: {', '.join(valid_providers)}",
        )

    # Upsert
    existing = db.query(CRMConnection).filter(
        CRMConnection.user_id == user_id,
        CRMConnection.provider == body.provider,
    ).first()

    if existing:
        existing.webhook_url = body.webhook_url or existing.webhook_url
        existing.webhook_secret = body.webhook_secret or existing.webhook_secret
        existing.access_token = body.access_token or existing.access_token
        existing.account_id = body.account_id or existing.account_id
        existing.is_active = True
    else:
        existing = CRMConnection(
            user_id=user_id,
            provider=body.provider,
            webhook_url=body.webhook_url,
            webhook_secret=body.webhook_secret,
            access_token=body.access_token,
            account_id=body.account_id,
            is_active=True,
        )
        db.add(existing)

    db.commit()
    db.refresh(existing)
    return {"provider": existing.provider, "is_active": existing.is_active}


@router.patch("/crm/connections/{provider}/toggle")
def toggle_connection(
    provider: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Ativa/desativa uma conexão CRM."""
    user_id = current_user.get("sub")
    conn = db.query(CRMConnection).filter(
        CRMConnection.user_id == user_id,
        CRMConnection.provider == provider,
    ).first()

    if not conn:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    conn.is_active = not conn.is_active
    db.commit()
    return {"provider": provider, "is_active": conn.is_active}


@router.delete("/crm/connections/{provider}")
def delete_connection(
    provider: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Deleta uma conexão CRM."""
    user_id = current_user.get("sub")
    conn = db.query(CRMConnection).filter(
        CRMConnection.user_id == user_id,
        CRMConnection.provider == provider,
    ).first()

    if not conn:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    db.delete(conn)
    db.commit()
    return {"deleted": True}
