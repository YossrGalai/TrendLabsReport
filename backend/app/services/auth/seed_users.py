"""
Crée les comptes utilisateurs par défaut s'ils n'existent pas déjà — appelé au démarrage de
l'app (cf. main.py, @app.on_event("startup")), donc rejoué à CHAQUE `docker compose up`.
Idempotent : vérifie l'email avant de créer, donc ne duplique jamais un compte existant.

⚠️ À FAIRE avant de démarrer :
1. Remplacez les emails ci-dessous par les vrais emails de Yossr/Ahlem/Wajih.
2. Définissez de vrais mots de passe via les variables d'env SEED_PASSWORD_* dans .env
   (sinon "changeme123" est utilisé pour les 3 — à ne surtout pas garder tel quel).
   Ce mécanisme ne gère que la CRÉATION initiale ; il n'y a pas encore de fonctionnalité
   "changer mon mot de passe" dans l'app — à ajouter plus tard si besoin.
"""

import logging
import os

from sqlalchemy.orm import Session

from app.models.models import User
from app.services.auth.auth_service import hash_password

logger = logging.getLogger(__name__)

DEFAULT_USERS = [
    # (email, nom affiché, variable d'env pour le mot de passe, mot de passe par défaut si absent)
    ("yossrgalai02@gmail.com", "Yossr", "SEED_PASSWORD_YOSSR"),
    ("ahlem@trendlabs.tn", "Ahlem", "SEED_PASSWORD_AHLEM"),
    ("wajih@trendlabs.tn", "Wajih", "SEED_PASSWORD_WAJIH"),
]
_FALLBACK_PASSWORD = "changeme123"


def ensure_default_users(db: Session) -> None:
    created = []
    for email, full_name, env_var in DEFAULT_USERS:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            continue  # déjà créé lors d'un démarrage précédent — on ne touche à rien
        password = os.getenv(env_var, _FALLBACK_PASSWORD)
        if password == _FALLBACK_PASSWORD:
            logger.warning(f"⚠️  {env_var} absent de .env — mot de passe par défaut utilisé pour {email}")
        db.add(User(email=email, hashed_password=hash_password(password), full_name=full_name))
        created.append(email)

    if created:
        db.commit()
        logger.info(f"✅ Comptes créés : {', '.join(created)}")