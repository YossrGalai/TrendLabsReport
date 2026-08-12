"""
Dépendance FastAPI réutilisable pour protéger des routes : get_current_user.

Emplacement : app/core/security.py — nouveau fichier, dossier app/core/ à créer s'il
n'existe pas déjà (pattern standard FastAPI, ne dépend d'aucun fichier existant du projet).
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.models import User
from app.services.auth.auth_service import decode_access_token

# auto_error=True : renvoie directement 403 si l'en-tête Authorization est absent, sans
# avoir à le vérifier nous-mêmes — FastAPI/Starlette s'en charge.
_bearer_scheme = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """À utiliser en dependencies=[Depends(get_current_user)] sur un include_router(), ou
    en paramètre d'un endpoint précis si on a besoin de savoir QUI fait la requête (ex:
    remplir generated_by sur un ReportRun/AnnualReportRun avec current_user.id)."""
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable ou compte désactivé",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user