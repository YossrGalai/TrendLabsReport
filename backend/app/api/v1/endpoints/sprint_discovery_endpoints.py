"""
Endpoint additif : détection des sprints existants pour un board+projet+mois donné,
alimente la pré-sélection du formulaire de rapport mensuel.

Chemin choisi : /api/boards/{trello_board_id}/sprints, PAS /api/reports/sprints — ce
dernier serait intercepté par la route générique GET /api/reports/{trello_board_id}
(historique des rapports, déjà définie dans excel_endpoints.py et enregistrée AVANT ce
routeur dans main.py) : "sprints" y serait traité comme une valeur de trello_board_id.
On suit plutôt la convention déjà utilisée par GET /api/boards/{trello_board_id}/projects.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

from app.database.db import get_db
from app.models.models import Board, Label, LabelTypeEnum
from app.services.excel.sprint_discovery_service import SprintDiscoveryService

router = APIRouter(prefix="/api/boards", tags=["Boards"])

_service = SprintDiscoveryService()


class SprintsInMonthResponse(BaseModel):
    sprint_numbers: List[int]


@router.get("/{trello_board_id}/sprints", response_model=SprintsInMonthResponse)
def get_sprints_in_month(
    trello_board_id: str,
    project_label_id: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2020, le=2100),
    db: Session = Depends(get_db),
) -> SprintsInMonthResponse:
    """Alimente le formulaire mensuel : liste par défaut (cochée) des sprints trouvés sur ce
    board+projet pour le mois choisi. Mêmes règles de résolution board/projet que
    POST /api/reports/generate/{trello_board_id} (cf. excel_endpoints.py)."""
    board = db.query(Board).filter(Board.trello_id == trello_board_id).first()
    if not board:
        raise HTTPException(
            status_code=404,
            detail=f"Board Trello {trello_board_id} introuvable en base — lancez d'abord POST /api/sync/full/{trello_board_id}",
        )

    project_label = (
        db.query(Label)
        .filter(Label.id == project_label_id, Label.board_id == board.id)
        .first()
    )
    if not project_label or project_label.label_type != LabelTypeEnum.PROJECT:
        raise HTTPException(
            status_code=404,
            detail=f"Label projet {project_label_id} introuvable sur le board {trello_board_id}",
        )

    sprint_numbers = _service.list_sprints_in_month(db, board.id, project_label_id, month, year)
    return SprintsInMonthResponse(sprint_numbers=sprint_numbers)