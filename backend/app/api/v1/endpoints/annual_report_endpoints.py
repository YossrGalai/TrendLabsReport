"""
Endpoints du RAPPORT ANNUEL — nouveau routeur, additif : ajouté dans main.py via un
include_router() supplémentaire, aucune ligne existante touchée.

ADAPTER les imports suivants à votre arborescence réelle si différente (suppositions
basées sur les patterns observés dans excel_service.py / schema_trendlabs.sql) :
- `from app.database.db import get_db, SessionLocal` : dépendance de session + factory de
  session pour la tâche de fond (qui a besoin de SA PROPRE session, cf. _run_generation)
- `from app.models.models import AnnualReportRun` : nouveau modèle ORM à ajouter dans
  app/models/models.py, correspondant à la table SQL annual_report_runs (voir fichier
  annual_report_runs.sql fourni séparément) — pas de modification des modèles existants,
  juste une nouvelle classe.

Génération en TÂCHE DE FOND (FastAPI BackgroundTasks) plutôt que synchrone comme le
mensuel : un an multi-board peut prendre nettement plus de temps qu'un mois mono-board/
mono-projet, donc on évite de bloquer la requête HTTP (risque de timeout côté proxy/front).
Le suivi se fait par polling sur GET /{report_run_id} (status: pending -> running -> done/
error), comme le fait déjà probablement votre front pour d'autres opérations longues.
"""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from sqlalchemy.orm import Session

from app.database.db import get_db, SessionLocal
from app.models.models import AnnualReportRun
from app.services.excel.annual_report_service import AnnualReportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports/annual", tags=["annual-reports"])

# Instance partagée (comme ExcelReportService ailleurs) : pas d'état mutable entre appels,
# juste output_dir/template — sûr de partager une seule instance.
_service = AnnualReportService()


# ============================================================================
# SCHÉMAS
# ============================================================================

class SprintsResponse(BaseModel):
    sprint_numbers: List[int]


class AnnualReportRequest(BaseModel):
    date_start: date
    date_end: date
    board_ids: Optional[List[int]] = None
    sprint_numbers: Optional[List[int]] = None

    @field_validator("date_end")
    @classmethod
    def _check_date_order(cls, v: date, info):
        start = info.data.get("date_start")
        if start and v < start:
            raise ValueError("date_end doit être postérieure ou égale à date_start")
        return v


class AnnualReportRunOut(BaseModel):
    id: int
    status: str
    date_start: date
    date_end: date
    board_ids: Optional[List[int]] = None
    sprint_numbers: Optional[List[int]] = None
    file_path: Optional[str] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True  # anciennement orm_mode, selon version pydantic du projet


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/sprints", response_model=SprintsResponse)
def get_sprints_in_period(
    date_start: date,
    date_end: date,
    board_ids: Optional[List[int]] = Query(None),
    db: Session = Depends(get_db),
) -> SprintsResponse:
    """Alimente le sélecteur de sprints du front : liste par défaut (cochée) des sprints
    couvrant la période choisie, avant que l'admin ne la modifie éventuellement."""
    if date_end < date_start:
        raise HTTPException(status_code=400, detail="date_end doit être postérieure à date_start")
    sprint_numbers = _service.list_sprints_in_period(db, date_start, date_end, board_ids)
    return SprintsResponse(sprint_numbers=sprint_numbers)


@router.post("/generate", response_model=AnnualReportRunOut, status_code=202)
def generate_annual_report(
    payload: AnnualReportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> AnnualReportRun:
    """Crée le run (status=pending) et lance la génération en tâche de fond. Retourne
    immédiatement le run — le front doit poller GET /{report_run_id} jusqu'à
    status='done' (ou 'error'), puis appeler /{report_run_id}/download."""
    run = AnnualReportRun(
        date_start=payload.date_start,
        date_end=payload.date_end,
        board_ids=payload.board_ids,
        sprint_numbers=payload.sprint_numbers,
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(
        _run_generation, run.id, payload.date_start, payload.date_end,
        payload.board_ids, payload.sprint_numbers,
    )
    return run


@router.get("/{report_run_id}", response_model=AnnualReportRunOut)
def get_report_status(report_run_id: int, db: Session = Depends(get_db)) -> AnnualReportRun:
    run = db.query(AnnualReportRun).filter(AnnualReportRun.id == report_run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Rapport annuel introuvable")
    return run


@router.get("/{report_run_id}/download")
def download_annual_report(report_run_id: int, db: Session = Depends(get_db)) -> FileResponse:
    run = db.query(AnnualReportRun).filter(AnnualReportRun.id == report_run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Rapport annuel introuvable")
    if run.status != "done" or not run.file_path:
        raise HTTPException(status_code=409, detail=f"Rapport pas encore prêt (status={run.status})")
    if not Path(run.file_path).exists():
        # Même symptôme que le 410 déjà rencontré côté mensuel (cf. commentaire
        # REPORTS_STORAGE_DIR dans excel_service.py) : fichier écrit hors du volume nommé
        # persistant → à surveiller de la même façon ici.
        raise HTTPException(status_code=410, detail="Fichier introuvable sur le disque")
    return FileResponse(run.file_path, filename=Path(run.file_path).name)


# ============================================================================
# TÂCHE DE FOND
# ============================================================================

def _run_generation(
    run_id: int,
    date_start: date,
    date_end: date,
    board_ids: Optional[List[int]],
    sprint_numbers: Optional[List[int]],
) -> None:
    """Exécutée hors du cycle requête/réponse : la session `db` injectée dans l'endpoint
    est fermée dès que la réponse HTTP 202 est envoyée, donc on ouvre ICI une session
    dédiée via SessionLocal() — ne JAMAIS réutiliser la session de la requête d'origine
    dans une tâche de fond FastAPI (comportement non garanti, source classique de bugs
    difficiles à reproduire)."""
    db = SessionLocal()
    try:
        run = db.query(AnnualReportRun).filter(AnnualReportRun.id == run_id).first()
        if not run:
            logger.error(f"AnnualReportRun id={run_id} introuvable au démarrage de la tâche de fond")
            return
        run.status = "running"
        db.commit()

        file_path = _service.generate_annual_report(
            db, date_start, date_end, board_ids, sprint_numbers,
        )

        run.status = "done"
        run.file_path = file_path
        run.generated_at = datetime.utcnow()
        db.commit()
        logger.info(f"✅ Rapport annuel run_id={run_id} terminé : {file_path}")
    except Exception as exc:
        logger.exception(f"❌ Échec génération rapport annuel run_id={run_id}")
        db.rollback()
        run = db.query(AnnualReportRun).filter(AnnualReportRun.id == run_id).first()
        if run:
            run.status = "error"
            run.error_message = str(exc)
            db.commit()
    finally:
        db.close()