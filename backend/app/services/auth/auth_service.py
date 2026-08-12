"""
Service d'authentification — hash de mot de passe (bcrypt via passlib, cohérent avec le
commentaire "Bcrypt hashed password" déjà présent sur User.hashed_password) et JWT
(python-jose) pour l'API.

Config requise dans .env (à ajouter) :
    AUTH_SECRET_KEY=<une chaîne aléatoire longue, ex: `openssl rand -hex 32`>
Ne JAMAIS committer une valeur par défaut utilisable en prod — le fallback ci-dessous
n'est là que pour ne pas planter immédiatement en dev si la variable est oubliée, mais loggue
un avertissement explicite dans ce cas.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.models.models import User

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12  # 12h — équipe de 3 personnes en interne, pas besoin
                                        # d'un access token très court + refresh token séparé

SECRET_KEY = os.getenv("AUTH_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = "dev-only-insecure-secret-change-me"
    logger.warning(
        "⚠️  AUTH_SECRET_KEY absent de l'environnement — utilisation d'une clé de "
        "développement NON SÉCURISÉE. À définir avant tout déploiement réel."
    )


def hash_password(plain_password: str) -> str:
    # bcrypt tronque silencieusement au-delà de 72 octets — mieux vaut lever une erreur
    # claire que produire un hash qui ignore la fin d'un mot de passe très long.
    if len(plain_password.encode("utf-8")) > 72:
        raise ValueError("Mot de passe trop long (max 72 octets pour bcrypt)")
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        # Hash stocké dans un format inattendu (corrompu / pas du bcrypt) — traiter comme
        # un échec d'authentification plutôt que de laisser remonter une exception 500.
        return False


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """Retourne le User si email+mot de passe corrects ET compte actif, sinon None — le
    même None pour "email inconnu" et "mauvais mot de passe" (ne jamais préciser lequel
    des deux à l'appelant, pour ne pas révéler quels emails existent en base)."""
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user.id), "email": user.email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[int]:
    """Retourne le user_id si le token est valide et non expiré, sinon None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        return int(user_id) if user_id is not None else None
    except JWTError:
        return None