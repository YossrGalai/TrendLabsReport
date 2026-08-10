from dotenv import load_dotenv
load_dotenv()

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints.sync_endpoints import router as sync_router
from app.api.v1.endpoints.trello_endpoints import router as trello_router
from app.api.v1.endpoints.excel_endpoints import router as excel_router,boards_router
from app.api.v1.endpoints.annual_report_endpoints import router as annual_report_router
from app.api.v1.endpoints.sprint_discovery_endpoints import router as sprint_discovery_router
from app.api.v1.endpoints.auth_endpoints import router as auth_router
from app.core.security import get_current_user
from app.database.db import SessionLocal
from app.services.auth.seed_users import ensure_default_users

app = FastAPI(
    title="TrendLabs Reporting Tool",
    description="Outil de génération de rapports depuis Trello vers Excel",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(auth_router)  # PAS protégé — sinon impossible de se connecter

# Tous les routeurs métier sont protégés au niveau du include_router() plutôt qu'endpoint
# par endpoint : aucune ligne touchée dans sync_endpoints.py, excel_endpoints.py,
# annual_report_endpoints.py, sprint_discovery_endpoints.py — chaque route qu'ils
# définissent devient protégée automatiquement.
_auth_dep = [Depends(get_current_user)]
app.include_router(sync_router, dependencies=_auth_dep)
app.include_router(trello_router, dependencies=_auth_dep)
app.include_router(excel_router, dependencies=_auth_dep)
app.include_router(annual_report_router, dependencies=_auth_dep)
app.include_router(sprint_discovery_router, dependencies=_auth_dep)

app.include_router(boards_router, dependencies=_auth_dep)


@app.on_event("startup")
def create_default_users_if_missing():
    """Rejoué à chaque démarrage de l'app (donc à chaque `docker compose up`) — idempotent,
    cf. seed_users.py."""
    db = SessionLocal()
    try:
        ensure_default_users(db)
    finally:
        db.close()


@app.get("/", tags=["Root"])
def root():
    """Bienvenue sur TrendLabs!"""
    return {
        "name": "TrendLabs Reporting Tool",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc"
    }


if __name__ == "__main__":
    # Note : ce bloc n'est PAS ce qui démarre l'app dans Docker — le Dockerfile lance
    # `uvicorn app.main:app --reload` directement en CLI (cf. CMD), donc ce bloc n'est utile
    # que si vous exécutez `python main.py` en dehors de Docker.
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)