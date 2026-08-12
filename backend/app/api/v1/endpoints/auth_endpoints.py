"""
Endpoints d'authentification — PAS protégés par get_current_user (sauf /me), sinon
personne ne pourrait jamais se connecter. main.py doit enregistrer ce routeur SANS
dependencies=[Depends(get_current_user)], contrairement aux autres routeurs.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.database.db import get_db
from app.models.models import User
from app.services.auth.auth_service import authenticate_user, create_access_token

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str | None = None

    class Config:
        from_attributes = True


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        # Message volontairement générique — ne précise jamais si c'est l'email ou le mot
        # de passe qui est incorrect (évite de révéler quels comptes existent en base).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
        )
    token = create_access_token(user)
    return LoginResponse(access_token=token)


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)) -> User:
    """Protégé (contrairement à /login) — le front l'appelle au démarrage pour vérifier si
    un token stocké est toujours valide, sans avoir à décoder le JWT côté client."""
    return current_user