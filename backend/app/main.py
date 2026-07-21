from dotenv import load_dotenv
load_dotenv()

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints.sync_endpoints import router as sync_router
from app.api.v1.endpoints.trello_endpoints import router as trello_router
from app.api.v1.endpoints.excel_endpoints import router as excel_router,boards_router

app = FastAPI(
    title="TrendLabs Reporting Tool",
    description="Outil de génération de rapports depuis Trello vers Excel",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sync_router)
app.include_router(trello_router)
app.include_router(excel_router)

app.include_router(boards_router)


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