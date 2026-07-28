import logging
from datetime import datetime, timezone
from pathlib import Path
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field , field_serializer
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.models import Board, Label, LabelTypeEnum, ReportRun, ReportStatusEnum
from app.services.excel.excel_service import ExcelReportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["Reports"])

# Router séparé pour l'endpoint de listing des projets d'un board (GET /api/boards/{id}/projects) :
# il ne s'agit pas d'un rapport, donc il ne partage pas le prefix "/api/reports" — mais on le
# garde dans ce fichier faute d'un boards_endpoints.py dédié. À déplacer si un tel fichier existe.
boards_router = APIRouter(prefix="/api/boards", tags=["Boards"])

excel_service = ExcelReportService()


# ============================================================================
# SCHÉMAS DE RÉPONSE (Pydantic)
# ============================================================================

class ReportGenerateResponse(BaseModel):
    success: bool
    report_run_id: int | None = None
    file_path: str | None = None
    error: str | None = None
    timestamp: str


class ReportRunOut(BaseModel):
    id: int
    board_id: int
    project_label_id: int
    report_month: int
    report_year: int
    sprint_numbers: list[int]
    status: str
    file_path: str | None = None
    generated_at: datetime | None = None

    @field_serializer("generated_at")
    def _serialize_generated_at(self, value: datetime | None, _info):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    error_message: str | None = None

    class Config:
        from_attributes = True


class ExtraBoardInput(BaseModel):
    trello_board_id: str = Field(..., description="ID Trello du board supplémentaire")
    project_label_id: int | None = Field(
        None,
        description=(
            "ID interne du label PROJECT à utiliser SUR CE BOARD, si connu — l'id du label "
            "'Adomlingua' sur ce board n'est PAS le même que sur le board principal (labels "
            "scopés par board sur Trello), donc ne pas réutiliser project_label_id tel quel. "
            "Si omis, le label est retrouvé automatiquement par NOM (même nom que le label "
            "PROJECT du board principal) — utile en dépannage rapide, mais suppose que le nom "
            "est identique à l'octet près sur les 2 boards ; renseigner cet id explicitement "
            "si les noms diffèrent légèrement (typo, casse, espace)."
        ),
    )


class ExtraProjectInput(BaseModel):
    """Projet FACULTATIF, différent de project_label_id, à afficher comme UN SEUL module
    supplémentaire dans ce même rapport (ses tickets ne sont pas éclatés sous leurs propres
    labels MODULE comme avec extra_boards, mais regroupés en bloc sous le nom de ce projet —
    cf. ExcelReportService.generate_monthly_report, paramètre extra_project)."""
    trello_board_id: str | None = Field(
        None,
        description=(
            "ID Trello du board où vit ce projet facultatif. Si omis, on cherche le label "
            "project_label_id sur le board PRINCIPAL de ce rapport (cas le plus courant : "
            "2 projets du même board, l'un affiché en module supplémentaire de l'autre)."
        ),
    )
    project_label_id: int = Field(
        ...,
        description=(
            "ID interne du label PROJECT (scopé au board ci-dessus, ou au board principal si "
            "trello_board_id est omis) dont les tickets doivent apparaître comme module "
            "supplémentaire. Contrairement à extra_boards, pas de résolution par nom : cet id "
            "est obligatoire."
        ),
    )


class ReportGenerateRequest(BaseModel):
    project_label_id: int = Field(
        ...,
        description=(
            "ID interne du label Trello de type PROJECT pour lequel générer le rapport "
            "(un board peut contenir plusieurs projets — voir GET /api/boards/{trello_board_id}/projects)"
        ),
    )
    month: int = Field(..., ge=1, le=12, description="Mois du rapport (1-12) — étiquette de classement/nommage")
    year: int = Field(..., ge=2020, le=2100, description="Année du rapport")
    sprint_numbers: list[int] = Field(
        ...,
        min_length=1,
        max_length=6,
        description=(
            "Les numéros de sprint composant ce mois de reporting (ex: [31, 32, 33, 34]) — le "
            "filtrage des cartes se fait sur ces numéros, pas sur le mois calendaire. Le nombre "
            "de semaines affichées dans le Gantt suit directement le nombre de sprints fournis "
            "(4 sprints = 4 semaines, 5 sprints = 5 semaines, etc.) — utile pour un mois qui "
            "s'étale sur 5 semaines calendaires (ex: avril). Maximum 6, capacité physique du "
            "template (cf. GANTT_MAX_WEEKS dans excel_service.py)."
        ),
    )
    chef_de_projet: str = Field("", description="Nom affiché en en-tête du rapport (optionnel)")
    extra_boards: list[ExtraBoardInput] = Field(
        default_factory=list,
        description=(
            "Board(s) SUPPLÉMENTAIRE(s) à inclure en plus du board principal (ex: le board "
            "Transverse partagé UI/UX + Integration). Ces boards doivent avoir été "
            "synchronisés au préalable (POST /api/sync/full/{board_id}) comme le board "
            "principal."
        ),
    )
    extra_project: ExtraProjectInput | None = Field(
        None,
        description=(
            "Projet FACULTATIF affiché comme un module supplémentaire à part entière dans ce "
            "rapport (ses tickets prennent des lignes comme les modules du projet principal, "
            "regroupés sous le nom de ce projet). Différent de extra_boards : ici on ajoute UN "
            "AUTRE PROJET (pas un board entier fusionné module par module)."
        ),
    )


class ProjectOut(BaseModel):
    """Un label Trello de type PROJECT — représente un projet du board pour lequel un
    rapport peut être généré."""
    id: int
    name: str
    color: str | None = None

    class Config:
        from_attributes = True


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/generate/{trello_board_id}", response_model=ReportGenerateResponse)
def generate_report(trello_board_id: str, payload: ReportGenerateRequest, db: Session = Depends(get_db)):
    """
    Génère (ou régénère) le rapport Excel mensuel d'un board.
    Crée/MAJ une ligne `report_runs`, appelle ExcelReportService, met à jour le statut.

    `trello_board_id` est l'ID Trello (string, ex: 6a355a98aab74bc8f4acd983) — le même
    identifiant que celui utilisé pour POST /api/sync/full/{board_id}. Le board doit donc
    avoir été synchronisé au moins une fois avant de pouvoir générer un rapport.

    Testable directement via Swagger (/docs) : renseigner trello_board_id dans l'URL,
    puis month/year/chef_de_projet dans le body.
    """
    timestamp = datetime.now(timezone.utc)

    board = db.query(Board).filter(Board.trello_id == trello_board_id).first()
    if not board:
        raise HTTPException(
            status_code=404,
            detail=f"Board Trello {trello_board_id} introuvable en base — lancez d'abord POST /api/sync/full/{trello_board_id}",
        )
    board_id = board.id  # ID interne MySQL (int), utilisé pour toutes les FK internes

    project_label = (
        db.query(Label)
        .filter(Label.id == payload.project_label_id, Label.board_id == board_id)
        .first()
    )
    if not project_label:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Label {payload.project_label_id} introuvable sur le board {trello_board_id} — "
                f"consultez GET /api/boards/{trello_board_id}/projects pour la liste des projets disponibles"
            ),
        )
    if project_label.label_type != LabelTypeEnum.PROJECT:
        raise HTTPException(
            status_code=400,
            detail=f"Le label '{project_label.name}' (id={payload.project_label_id}) n'est pas un label PROJECT",
        )

    # Liste de tuples (board_id interne, project_label_id_override) attendue par
    # ExcelReportService.generate_monthly_report (cf. sa docstring) — override est None si
    # non renseigné dans le payload, auquel cas le service résout le label PROJECT par NOM.
    # extra_boards est FACULTATIF (board supplémentaire pas toujours présent) — mais un
    # client (formulaire front, Swagger avec une ligne laissée vide, etc.) peut envoyer une
    # entrée avec trello_board_id="" au lieu d'omettre extra_boards entièrement. On ignore ces
    # entrées vides/blanches plutôt que de tenter une recherche en base vouée à échouer avec un
    # 404 trompeur ("Board Trello supplémentaire  introuvable" — notez le double espace,
    # signature d'un trello_board_id vide).
    extra_boards: list[tuple[int, int | None]] = []
    for extra_board_input in payload.extra_boards:
        if not extra_board_input.trello_board_id or not extra_board_input.trello_board_id.strip():
            continue
        extra_board = db.query(Board).filter(Board.trello_id == extra_board_input.trello_board_id).first()
        if not extra_board:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Board Trello supplémentaire {extra_board_input.trello_board_id} introuvable en base — "
                    f"lancez d'abord POST /api/sync/full/{extra_board_input.trello_board_id}"
                ),
            )
        extra_boards.append((extra_board.id, extra_board_input.project_label_id))

    # extra_project : contrairement à extra_boards, un SEUL projet facultatif, sans fallback
    # par nom (project_label_id y est obligatoire côté service) — on ne résout donc ici QUE le
    # board (le principal si trello_board_id est omis/vide), la validation du label lui-même
    # (existence, type PROJECT) reste faite par ExcelReportService.generate_monthly_report.
    extra_project: tuple[int, int] | None = None
    if payload.extra_project is not None:
        extra_project_trello_board_id = payload.extra_project.trello_board_id
        if extra_project_trello_board_id and extra_project_trello_board_id.strip():
            extra_project_board = (
                db.query(Board).filter(Board.trello_id == extra_project_trello_board_id).first()
            )
            if not extra_project_board:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Board Trello du projet facultatif {extra_project_trello_board_id} introuvable "
                        f"en base — lancez d'abord POST /api/sync/full/{extra_project_trello_board_id}"
                    ),
                )
            extra_project_board_id = extra_project_board.id
        else:
            extra_project_board_id = board_id
        extra_project = (extra_project_board_id, payload.extra_project.project_label_id)

    report_run = (
        db.query(ReportRun)
        .filter(
            ReportRun.board_id == board_id,
            ReportRun.project_label_id == payload.project_label_id,
            ReportRun.report_month == payload.month,
            ReportRun.report_year == payload.year,
        )
        .first()
    )
    if not report_run:
        report_run = ReportRun(
            board_id=board_id,
            project_label_id=payload.project_label_id,
            report_month=payload.month,
            report_year=payload.year,
            sprint_numbers=payload.sprint_numbers,
            status=ReportStatusEnum.RUNNING,
        )
        db.add(report_run)
    else:
        report_run.sprint_numbers = payload.sprint_numbers
        report_run.status = ReportStatusEnum.RUNNING
        report_run.error_message = None
    db.commit()
    db.refresh(report_run)

    try:
        file_path = excel_service.generate_monthly_report(
            db=db,
            board_id=board_id,
            project_label_id=payload.project_label_id,
            month=payload.month,
            year=payload.year,
            sprint_numbers=payload.sprint_numbers,
            chef_de_projet=payload.chef_de_projet,
            extra_boards=extra_boards,
            extra_project=extra_project,
        )

        report_run.status = ReportStatusEnum.DONE
        report_run.file_path = file_path
        report_run.generated_at = datetime.now(timezone.utc)
        db.commit()

        return ReportGenerateResponse(
            success=True,
            report_run_id=report_run.id,
            file_path=file_path,
            timestamp=timestamp.replace(tzinfo=timezone.utc).isoformat(),
        )

    except Exception as e:
        db.rollback()
        report_run.status = ReportStatusEnum.ERROR
        report_run.error_message = str(e)
        db.commit()
        logger.error(f"❌ Erreur génération rapport board={trello_board_id}: {e}", exc_info=True)

        return ReportGenerateResponse(
            success=False,
            report_run_id=report_run.id,
            error=str(e),
            timestamp=timestamp.replace(tzinfo=timezone.utc).isoformat(),
        )


@router.get("/{trello_board_id}", response_model=list[ReportRunOut])
def list_reports(trello_board_id: str, project_label_id: int | None = None, db: Session = Depends(get_db)):
    """Liste tous les rapports générés (ou tentés) pour un board, du plus récent au plus ancien.
    `trello_board_id` est l'ID Trello (string), même convention que /generate.
    Un board pouvant désormais produire plusieurs rapports par mois (un par projet),
    `project_label_id` permet optionnellement de ne lister que ceux d'un projet donné."""
    board = db.query(Board).filter(Board.trello_id == trello_board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail=f"Board Trello {trello_board_id} introuvable en base")

    query = db.query(ReportRun).filter(ReportRun.board_id == board.id)
    if project_label_id is not None:
        query = query.filter(ReportRun.project_label_id == project_label_id)

    reports = query.order_by(ReportRun.report_year.desc(), ReportRun.report_month.desc()).all()
    return reports


@boards_router.get("/{trello_board_id}/projects", response_model=list[ProjectOut])
def list_board_projects(trello_board_id: str, db: Session = Depends(get_db)):
    """Liste les projets (labels Trello de type PROJECT) disponibles pour un board — sert à
    peupler `project_label_id` dans POST /api/reports/generate/{trello_board_id}."""
    board = db.query(Board).filter(Board.trello_id == trello_board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail=f"Board Trello {trello_board_id} introuvable en base")

    projects = (
        db.query(Label)
        .filter(Label.board_id == board.id, Label.label_type == LabelTypeEnum.PROJECT)
        .order_by(Label.name)
        .all()
    )
    return projects


@router.get("/download/{report_run_id}")
def download_report(report_run_id: int, db: Session = Depends(get_db)):
    """Télécharge le fichier .xlsx généré pour un ReportRun donné."""
    report_run = db.query(ReportRun).filter(ReportRun.id == report_run_id).first()
    if not report_run:
        raise HTTPException(status_code=404, detail=f"ReportRun {report_run_id} introuvable")

    if report_run.status != ReportStatusEnum.DONE or not report_run.file_path:
        raise HTTPException(
            status_code=409,
            detail=f"Rapport non disponible (statut actuel: {report_run.status})",
        )

    file_path = Path(report_run.file_path)

    logger.info(f"CWD = {os.getcwd()}")
    logger.info(f"DB path = {report_run.file_path}")
    logger.info(f"Absolute = {file_path.resolve()}")
    logger.info(f"Exists = {file_path.exists()}")

    if not file_path.exists():
        raise HTTPException(
            status_code=410,
            detail="Fichier introuvable sur le disque (a-t-il été déplacé/supprimé ?)",
            headers={"Cache-Control": "no-store"},
        )

    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Cache-Control": "no-store"},
    )