from fastapi import APIRouter, Depends, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from slowapi import Limiter
from sqlalchemy.orm import Session
from models.database import get_db, Profile
from middleware.auth import diagnostico, get_current_user, rate_limit_key

router = APIRouter(prefix="/api", tags=["auth"])
limiter = Limiter(key_func=rate_limit_key)

# Sem `auto_error`: o diagnóstico precisa responder também para quem chega sem
# credencial nenhuma — "o servidor tem chave para verificar login?" é metade da
# resposta, e é justamente a metade que falta quando nada funciona.
_bearer_opcional = HTTPBearer(auto_error=False)


def get_or_create_profile(db: Session, user_id: str, email: str = None) -> Profile:
    """
    O perfil do usuário, criado na primeira vez que ele aparece.

    Não há plano nem cota para decidir: toda conta que entra tem o produto
    inteiro. O perfil existe para dar dono aos leads, atividades e conversas.

    `email` vem do claim do JWT, não de formulário — é só o que grava e
    atualiza o endereço para onde o digest diário escreve, e por isso nunca
    apaga o que já estava lá (um token sem o claim não pode limpar um e-mail
    que um token anterior já confirmou).
    """
    profile = db.query(Profile).filter(Profile.id == user_id).first()
    if not profile:
        profile = Profile(id=user_id, email=email)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    elif email and profile.email != email:
        profile.email = email
        db.commit()
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
    profile = get_or_create_profile(db, user_id, email=current_user.get("email"))
    return {
        "id": profile.id,
        "email": current_user.get("email"),
        "digest_diario": profile.digest_diario,
    }


class PreferenciasUpdate(BaseModel):
    digest_diario: bool


@router.patch("/me")
def atualizar_preferencias(
    body: PreferenciasUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Liga/desliga o resumo diário por e-mail."""
    user_id = current_user.get("sub")
    profile = get_or_create_profile(db, user_id, email=current_user.get("email"))
    profile.digest_diario = body.digest_diario
    db.commit()
    return {"digest_diario": profile.digest_diario}
