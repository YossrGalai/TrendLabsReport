from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.services.trello.trello_service import TrelloService
from app.database.db import SessionLocal
from app.models.models import Board, List, Member, Label, Card

router = APIRouter()


# ============================================================================
# DEPENDENCIES
# ============================================================================

def get_db():
    """Dependency: obtient une session BDD"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_trello():
    """Dependency: crée une instance du service Trello"""
    return TrelloService()


# ============================================================================
# HEALTH CHECK
# ============================================================================

@router.get("/health", tags=["Health"])
def health_check(db: Session = Depends(get_db)):
    """Vérifie que l'app et la BDD fonctionnent"""
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc),
            "database": "connected"
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)}")


# ============================================================================
# TRELLO - AUTHENTICATION TEST
# ============================================================================

@router.get("/trello/auth", tags=["Trello - Test"])
def test_trello_auth(trello: TrelloService = Depends(get_trello)):
    """✅ Teste la connexion à l'API Trello"""
    try:
        user = trello.get_current_user()
        return {
            "status": "connected",
            "user": {
                "id": user.get("id"),
                "fullName": user.get("fullName"),
                "username": user.get("username"),
                "email": user.get("email")
            }
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Trello auth failed: {str(e)}")


# ============================================================================
# TRELLO - BOARDS
# ============================================================================

@router.get("/trello/boards", tags=["Trello - Boards"])
def list_trello_boards(trello: TrelloService = Depends(get_trello)):
    """📊 Récupère tous les boards Trello du user"""
    try:
        boards = trello.get_boards()
        return {
            "count": len(boards),
            "boards": [
                {
                    "id": b["id"],
                    "name": b["name"],
                    "url": b.get("url"),
                    "closed": b.get("closed"),
                    "dateLastActivity": b.get("dateLastActivity")
                }
                for b in boards
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trello/boards/{board_id}", tags=["Trello - Boards"])
def get_trello_board_details(board_id: str, trello: TrelloService = Depends(get_trello)):
    """📊 Récupère les détails d'un board spécifique"""
    try:
        board = trello.get_board_by_id(board_id)
        return {
            "id": board["id"],
            "name": board["name"],
            "url": board.get("url"),
            "description": board.get("desc"),
            "dateLastActivity": board.get("dateLastActivity")
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Board not found: {str(e)}")


# ============================================================================
# TRELLO - LISTS
# ============================================================================

@router.get("/trello/boards/{board_id}/lists", tags=["Trello - Lists"])
def get_board_lists(board_id: str, trello: TrelloService = Depends(get_trello)):
    """📋 Récupère toutes les listes d'un board"""
    try:
        lists = trello.get_board_lists(board_id)
        return {
            "board_id": board_id,
            "count": len(lists),
            "lists": [
                {
                    "id": l["id"],
                    "name": l["name"],
                    "closed": l.get("closed"),
                    "position": l.get("pos")
                }
                for l in lists
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# TRELLO - CARDS
# ============================================================================

@router.get("/trello/boards/{board_id}/cards", tags=["Trello - Cards"])
def get_board_cards(board_id: str, trello: TrelloService = Depends(get_trello)):
    """🎴 Récupère toutes les cartes d'un board"""
    try:
        cards = trello.get_board_cards(board_id)
        formatted_cards = []
        for card in cards:
            formatted_cards.append({
                "id": card["id"],
                "name": card["name"],
                "desc": card.get("desc"),
                "idList": card.get("idList"),
                "url": card.get("url"),
                "dateCreated": card.get("dateLastActivity"),
                "due": card.get("due"),
                "dueComplete": card.get("dueComplete"),
                "members": [
                    {"id": m["id"], "fullName": m["fullName"], "username": m["username"]}
                    for m in card.get("members", [])
                ],
                "labels": [
                    {"id": l["id"], "name": l["name"], "color": l.get("color")}
                    for l in card.get("labels", [])
                ]
            })
        return {
            "board_id": board_id,
            "count": len(formatted_cards),
            "cards": formatted_cards
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trello/cards/{card_id}", tags=["Trello - Cards"])
def get_card_details(card_id: str, trello: TrelloService = Depends(get_trello)):
    try:
        card = trello.get_card_by_id(card_id)
        actions = trello.get_card_actions(card_id)
        return {
            "id": card["id"],
            "name": card["name"],
            "desc": card.get("desc"),
            "idList": card.get("idList"),
            "dateCreated": card.get("dateLastActivity"),
            "due": card.get("due"),
            "dueComplete": card.get("dueComplete"),
            "members": [
                {"id": m["id"], "fullName": m["fullName"], "username": m["username"]}
                for m in card.get("members", [])
            ],
            "labels": [
                {"id": l["id"], "name": l["name"], "color": l.get("color")}
                for l in card.get("labels", [])
            ],
            "attachments": card.get("attachments", []),
            "actions_count": len(actions),
            "latest_actions": actions[:10]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# TRELLO - MEMBERS
# ============================================================================

@router.get("/trello/boards/{board_id}/members", tags=["Trello - Members"])
def get_board_members(board_id: str, trello: TrelloService = Depends(get_trello)):
    """👥 Récupère tous les membres d'un board"""
    try:
        members = trello.get_board_members(board_id)
        return {
            "board_id": board_id,
            "count": len(members),
            "members": [
                {
                    "id": m["id"],
                    "fullName": m["fullName"],
                    "username": m["username"],
                    "email": m.get("email"),
                    "avatarHash": m.get("avatarHash")
                }
                for m in members
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# TRELLO - LABELS
# ============================================================================

@router.get("/trello/boards/{board_id}/labels", tags=["Trello - Labels"])
def get_board_labels(board_id: str, trello: TrelloService = Depends(get_trello)):
    """🏷️ Récupère tous les labels d'un board"""
    try:
        labels = trello.get_board_labels(board_id)
        return {
            "board_id": board_id,
            "count": len(labels),
            "labels": [
                {"id": l["id"], "name": l["name"], "color": l.get("color"), "uses": l.get("uses")}
                for l in labels
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# DATABASE - RECAP
# ============================================================================

@router.get("/db/stats", tags=["Database"])
def get_db_stats(db: Session = Depends(get_db)):
    """📊 Affiche les statistiques de la BDD (données syncées)"""
    try:
        return {
            "status": "ok",
            "boards": db.query(Board).count(),
            "lists": db.query(List).count(),
            "cards": db.query(Card).count(),
            "members": db.query(Member).count(),
            "labels": db.query(Label).count()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))