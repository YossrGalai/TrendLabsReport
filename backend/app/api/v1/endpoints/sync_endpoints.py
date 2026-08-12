from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Dict, Any
import logging
from datetime import timezone

from app.database.db import get_db
from app.services.trello.trello_service import TrelloService
from app.services.sync.sync_service import TrelloSyncService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sync", tags=["Synchronization"])


# ============================================================================
# 1. SYNCHRONISATION COMPLÈTE
# ============================================================================

@router.post("/full/{board_id}")
def sync_full_board(
        board_id: str,
        db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Synchronise COMPLÈTE un board Trello :
    - Boards
    - Lists
    - Members
    - Labels
    - Cards + relations (members, labels)
    - Card History (actions)

    Exemple: POST /api/sync/full/abc123xyz789
    """
    try:
        trello_service = TrelloService()
        sync_service = TrelloSyncService(trello_service)
        result = sync_service.full_sync_board(db, board_id)
        return result
    except Exception as e:
        logger.error(f"❌ Sync failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# 2. DIAGNOSTIQUE ET VÉRIFICATION
# ============================================================================

@router.get("/status/{board_id}")
def check_sync_status(
        board_id: str,
        db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Vérifie l'état de synchronisation d'un board :
    - Nombre de lists, members, labels, cards, history
    - Dernière synchronisation

    Exemple: GET /api/sync/status/abc123xyz789
    """
    from app.models.models import Board, List, Member, Label, Card, CardHistory

    try:
        board = db.query(Board).filter(Board.trello_id == board_id).first()
        if not board:
            raise HTTPException(status_code=404, detail="Board not found")

        lists_count = db.query(List).filter(List.board_id == board.id).count()
        members_count = db.query(Member).count()  # Global, pas per-board
        labels_count = db.query(Label).filter(Label.board_id == board.id).count()
        cards_count = db.query(Card).filter(Card.board_id == board.id).count()
        history_count = db.query(CardHistory).filter(
            CardHistory.card_id.in_(
                db.query(Card.id).filter(Card.board_id == board.id)
            )
        ).count()

        return {
            "board_id": board.id,
            "board_name": board.name,
            "trello_id": board.trello_id,
            "last_sync_at": (
                board.last_sync_at.replace(tzinfo=timezone.utc).isoformat()
                if board.last_sync_at else None
            ),
            "created_at": board.created_at.replace(tzinfo=timezone.utc).isoformat(),
            "counts": {
                "lists": lists_count,
                "members": members_count,
                "labels": labels_count,
                "cards": cards_count,
                "card_history": history_count,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Status check failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verify/{board_id}")
def verify_sync_integrity(
        board_id: str,
        db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Vérifie l'intégrité des données synchronisées :
    - Cartes sans list
    - Members sans assignation
    - Labels orphelins
    - Doublons

    Exemple: GET /api/sync/verify/abc123xyz789
    """
    from app.models.models import Board, List, Card, CardMember, CardLabel, Label, Member

    try:
        board = db.query(Board).filter(Board.trello_id == board_id).first()
        if not board:
            raise HTTPException(status_code=404, detail="Board not found")

        # Cartes sans list
        cards_no_list = db.query(Card).filter(
            Card.board_id == board.id,
            Card.list_id == None
        ).count()

        # Labels sans cartes
        labels_no_cards = db.query(Label).filter(
            Label.board_id == board.id,
            ~Label.cards.any()
        ).count()

        # Vérifier les relations
        cards_with_members = db.query(Card).filter(
            Card.board_id == board.id
        ).filter(Card.members.any()).count()

        cards_with_labels = db.query(Card).filter(
            Card.board_id == board.id
        ).filter(Card.labels.any()).count()

        issues = []
        if cards_no_list > 0:
            issues.append(f"{cards_no_list} cards without list")

        return {
            "board_id": board.id,
            "board_name": board.name,
            "integrity": {
                "cards_no_list": cards_no_list,
                "labels_no_cards": labels_no_cards,
                "cards_with_members": cards_with_members,
                "cards_with_labels": cards_with_labels,
            },
            "issues": issues,
            "is_valid": len(issues) == 0,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Integrity check failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# 3. ENDPOINTS DE DÉBOGAGE
# ============================================================================

@router.get("/debug/boards")
def list_boards(
        db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Liste tous les boards synchronisés en BD
    """
    from app.models.models import Board

    boards = db.query(Board).all()
    return {
        "count": len(boards),
        "boards": [
            {
                "id": b.id,
                "name": b.name,
                "trello_id": b.trello_id,
                "last_sync_at": (
                    b.last_sync_at.replace(tzinfo=timezone.utc).isoformat()
                    if b.last_sync_at else None
                ),
            }
            for b in boards
        ],
    }


@router.get("/debug/trello-boards")
def list_trello_boards() -> Dict[str, Any]:
    """
    Liste tous les boards accessibles via l'API Trello
    """
    try:
        trello_service = TrelloService()
        boards = trello_service.get_boards()
        return {
            "count": len(boards),
            "boards": [
                {
                    "id": b.get("id"),
                    "name": b.get("name"),
                    "url": b.get("url"),
                }
                for b in boards
            ],
        }
    except Exception as e:
        logger.error(f"❌ Could not fetch Trello boards: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/debug/board/{board_id}/lists")
def list_board_lists(
        board_id: str,
        db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Liste les listes d'un board (BD + Trello pour comparaison)
    """
    from app.models.models import Board, List

    board = db.query(Board).filter(Board.trello_id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    db_lists = db.query(List).filter(List.board_id == board.id).all()

    try:
        trello_service = TrelloService()
        trello_lists = trello_service.get_board_lists(board_id)
    except:
        trello_lists = []

    return {
        "board_name": board.name,
        "db_count": len(db_lists),
        "trello_count": len(trello_lists),
        "db_lists": [
            {
                "id": l.id,
                "trello_id": l.trello_id,
                "name": l.name,
                "workflow_stage": l.workflow_stage.value,
                "is_archived": l.is_archived,
            }
            for l in db_lists
        ],
        "trello_lists": [
            {
                "id": l.get("id"),
                "name": l.get("name"),
                "closed": l.get("closed", False),
            }
            for l in trello_lists
        ],
    }


@router.get("/debug/board/{board_id}/cards")
def list_board_cards(
        board_id: str,
        limit: int = Query(5, ge=1, le=100),
        db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Liste les cartes d'un board avec leurs relations
    """
    from app.models.models import Board, Card

    board = db.query(Board).filter(Board.trello_id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    cards = db.query(Card).filter(Card.board_id == board.id).limit(limit).all()

    return {
        "board_name": board.name,
        "total_cards": db.query(Card).filter(Card.board_id == board.id).count(),
        "limit": limit,
        "cards": [
            {
                "id": c.id,
                "trello_id": c.trello_id,
                "name": c.name[:50],
                "list_id": c.list_id,
                "list_name": c.list.name if c.list else None,
                "members_count": len(c.members),
                "labels_count": len(c.labels),
                "history_count": len(c.history),
                "is_closed": c.is_closed,
            }
            for c in cards
        ],
    }


@router.post("/clear/{board_id}")
def clear_board_data(
        board_id: str,
        confirm: bool = Query(False),
        db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    ⚠️  SUPPRIMER toutes les données d'un board de la BD
    (Utile pour recommencer une sync depuis zéro)

    Usage: POST /api/sync/clear/abc123xyz789?confirm=true
    """
    if not confirm:
        return {
            "error": "Missing confirm=true parameter",
            "warning": "This will DELETE all board data from MySQL",
        }

    from app.models.models import Board

    board = db.query(Board).filter(Board.trello_id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    try:
        db.delete(board)
        db.commit()
        return {
            "success": True,
            "message": f"Board {board.name} deleted",
            "board_id": board_id,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Clear failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# INCLURE CES ENDPOINTS DANS main.py
# ============================================================================
# from app.api.sync_endpoints import router as sync_router
# app.include_router(sync_router)