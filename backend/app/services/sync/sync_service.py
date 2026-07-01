"""
SYNC SERVICE - Synchronise les données Trello → MySQL
Gère les relations, les doublons, et les mises à jour.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import (
    ActionTypeEnum,
    Board,
    Card,
    CardHistory,
    CardLabel,
    CardMember,
    Label,
    LabelTypeEnum,
    List,
    Member,
    WorkflowStageEnum,
)
from app.services.trello.trello_service import TrelloService

logger = logging.getLogger(__name__)


class TrelloSyncService:
    """Service de synchronisation bidirectionnelle Trello ↔ MySQL"""

    def __init__(self, trello_service: TrelloService):
        self.trello = trello_service

    # ========================================================================
    # 1. SYNCHRONISATION PRINCIPALE
    # ========================================================================

    def full_sync_board(self, db: Session, board_id: str) -> Dict[str, Any]:
        """Synchronisation COMPLÈTE d'un board"""
        sync_start = datetime.now(timezone.utc)

        try:
            # 1. Créer/récupérer le board
            board = self._sync_board(db, board_id)
            logger.info(f"✅ Board synced: {board.name} (ID={board.id})")

            # 2. Synchroniser les listes
            lists_count = self._sync_lists(db, board.id, board_id)
            logger.info(f"✅ {lists_count} lists synced")

            # 3. Synchroniser les membres
            members_count = self._sync_members(db, board_id)
            logger.info(f"✅ {members_count} members synced")

            # 4. Synchroniser les labels
            labels_count = self._sync_labels(db, board.id, board_id)
            logger.info(f"✅ {labels_count} labels synced")

            # 5. Synchroniser les cartes + relations
            cards_count = self._sync_cards(db, board.id, board_id)
            logger.info(f"✅ {cards_count} cards synced")

            # 6. Synchroniser l'historique des cartes
            history_count = self._sync_card_history(db, board_id)
            logger.info(f"✅ {history_count} history records synced")

            # Update last_sync_at (Timezone-aware)
            board.last_sync_at = datetime.now(timezone.utc)
            db.commit()

            return {
                "success": True,
                "board_id": board.id,
                "board_name": board.name,
                "counts": {
                    "lists": lists_count,
                    "members": members_count,
                    "labels": labels_count,
                    "cards": cards_count,
                    "history": history_count,
                },
                "sync_duration_seconds": (datetime.now(timezone.utc) - sync_start).total_seconds(),
                "timestamp": sync_start.isoformat(),
            }

        except Exception as e:
            db.rollback()
            logger.error(f"❌ Sync error: {str(e)}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "timestamp": sync_start.isoformat(),
            }

    # ========================================================================
    # 2. SYNCHRONISATION PAR ENTITÉ
    # ========================================================================

    def _sync_board(self, db: Session, trello_board_id: str) -> Board:
        """Crée ou met à jour un board"""
        trello_board = self.trello.get_board_by_id(trello_board_id)

        board = db.query(Board).filter(
            Board.trello_id == trello_board_id
        ).first()

        if board:
            board.name = trello_board.get("name", "Unknown")
            logger.debug(f"  Updated board: {board.name}")
        else:
            board = Board(
                trello_id=trello_board_id,
                name=trello_board.get("name", "Unknown"),
                created_at=datetime.now(timezone.utc),
                last_sync_at=datetime.now(timezone.utc),
            )
            db.add(board)
            logger.debug(f"  Created board: {board.name}")

        db.commit()
        return board

    def _sync_lists(self, db: Session, board_id: int, trello_board_id: str) -> int:
        """Synchronise les listes du board"""
        trello_lists = self.trello.get_board_lists(trello_board_id)
        count = 0

        for trello_list in trello_lists:
            trello_list_id = trello_list.get("id")
            existing_list = db.query(List).filter(
                List.trello_id == trello_list_id
            ).first()

            # On récupère directement le membre Enum correct
            workflow_stage = self._map_workflow_stage(trello_list.get("name", ""))
            is_archived_val = bool(trello_list.get("closed", False))

            if existing_list:
                existing_list.name = trello_list.get("name", "Unknown")
                existing_list.workflow_stage = workflow_stage
                existing_list.position = trello_list.get("pos", 0)
                existing_list.is_archived = is_archived_val
            else:
                new_list = List(
                    trello_id=trello_list_id,
                    board_id=board_id,
                    name=trello_list.get("name", "Unknown"),
                    workflow_stage=workflow_stage,
                    position=trello_list.get("pos", 0),
                    is_archived=is_archived_val,
                )
                db.add(new_list)
                count += 1

        db.commit()
        return count

    def _sync_members(self, db: Session, trello_board_id: str) -> int:
        """Synchronise les membres du board"""
        trello_members = self.trello.get_board_members(trello_board_id)
        count = 0

        for trello_member in trello_members:
            trello_member_id = trello_member.get("id")
            existing_member = db.query(Member).filter(
                Member.trello_id == trello_member_id
            ).first()

            if existing_member:
                existing_member.full_name = trello_member.get("fullName")
                existing_member.username = trello_member.get("username")
                existing_member.avatar_url = self._get_avatar_url(trello_member)
            else:
                new_member = Member(
                    trello_id=trello_member_id,
                    full_name=trello_member.get("fullName"),
                    username=trello_member.get("username"),
                    avatar_url=self._get_avatar_url(trello_member),
                    created_at=datetime.now(timezone.utc),
                )
                db.add(new_member)
                count += 1

        db.commit()
        return count

    def _sync_labels(self, db: Session, board_id: int, trello_board_id: str) -> int:
        """Synchronise les labels du board"""
        trello_labels = self.trello.get_board_labels(trello_board_id)
        count = 0

        for trello_label in trello_labels:
            trello_label_id = trello_label.get("id")
            existing_label = db.query(Label).filter(
                Label.trello_id == trello_label_id
            ).first()

            label_name = trello_label.get("name", "")
            label_type, sprint_number = self._parse_label_type(label_name)

            if existing_label:
                existing_label.name = label_name
                existing_label.color = trello_label.get("color")
                existing_label.label_type = label_type
                existing_label.sprint_number = sprint_number
            else:
                new_label = Label(
                    trello_id=trello_label_id,
                    board_id=board_id,
                    name=label_name,
                    color=trello_label.get("color"),
                    label_type=label_type,
                    sprint_number=sprint_number,
                )
                db.add(new_label)
                count += 1

        db.commit()
        return count

    def _sync_cards(self, db: Session, board_id: int, trello_board_id: str) -> int:
        """Synchronise les cartes + relations"""
        trello_cards = self.trello.get_board_cards(trello_board_id)
        count = 0

        for trello_card in trello_cards:
            trello_card_id = trello_card.get("id")

            existing_card = db.query(Card).filter(
                Card.trello_id == trello_card_id
            ).first()

            trello_list_id = trello_card.get("idList")
            card_list = db.query(List).filter(
                List.trello_id == trello_list_id
            ).first()

            if not card_list:
                logger.warning(f"⚠️  List not found for card {trello_card_id}")
                continue

            if existing_card:
                existing_card.name = trello_card.get("name", "Unknown")
                existing_card.description = trello_card.get("desc", "")
                existing_card.due_date = self._parse_datetime(trello_card.get("due"))
                existing_card.due_complete = bool(trello_card.get("dueComplete", False))
                existing_card.date_last_activity = self._parse_datetime(
                    trello_card.get("dateLastActivity")
                )
                existing_card.is_closed = bool(trello_card.get("closed", False))
                existing_card.list_id = card_list.id
                existing_card.board_id = board_id
            else:
                new_card = Card(
                    trello_id=trello_card_id,
                    board_id=board_id,
                    list_id=card_list.id,
                    name=trello_card.get("name", "Unknown"),
                    description=trello_card.get("desc", ""),
                    due_date=self._parse_datetime(trello_card.get("due")),
                    due_complete=bool(trello_card.get("dueComplete", False)),
                    date_last_activity=self._parse_datetime(
                        trello_card.get("dateLastActivity")
                    ),
                    is_closed=bool(trello_card.get("closed", False)),
                    created_at=datetime.now(timezone.utc),
                )
                db.add(new_card)
                db.flush()
                existing_card = new_card
                count += 1

            if existing_card and getattr(existing_card, 'id', None) is not None:
                self._sync_card_members(
                    db, existing_card.id, trello_card.get("idMembers", [])
                )
                self._sync_card_labels(
                    db, existing_card.id, trello_card.get("idLabels", [])
                )

        db.commit()
        return count

    def _sync_card_members(self, db: Session, card_id: int, trello_member_ids: list[str]) -> None:
        """Synchronise les membres assignés à une carte"""
        current_members = db.query(CardMember).filter(
            CardMember.card_id == card_id
        ).all()
        current_member_ids = {cm.member_id for cm in current_members}

        trello_member_objects = db.query(Member).filter(
            Member.trello_id.in_(trello_member_ids)
        ).all()
        trello_member_db_ids = {m.id for m in trello_member_objects}

        for member_id in current_member_ids - trello_member_db_ids:
            db.query(CardMember).filter(
                CardMember.card_id == card_id,
                CardMember.member_id == member_id
            ).delete()

        for member_id in trello_member_db_ids - current_member_ids:
            new_card_member = CardMember(
                card_id=card_id,
                member_id=member_id
            )
            db.add(new_card_member)

        db.commit()

    def _sync_card_labels(self, db: Session, card_id: int, trello_label_ids: list[str]) -> None:
        """Synchronise les labels assignés à une carte"""
        current_labels = db.query(CardLabel).filter(
            CardLabel.card_id == card_id
        ).all()
        current_label_ids = {cl.label_id for cl in current_labels}

        trello_label_objects = db.query(Label).filter(
            Label.trello_id.in_(trello_label_ids)
        ).all()
        trello_label_db_ids = {l.id for l in trello_label_objects}

        for label_id in current_label_ids - trello_label_db_ids:
            db.query(CardLabel).filter(
                CardLabel.card_id == card_id,
                CardLabel.label_id == label_id
            ).delete()

        for label_id in trello_label_db_ids - current_label_ids:
            new_card_label = CardLabel(
                card_id=card_id,
                label_id=label_id
            )
            db.add(new_card_label)

        db.commit()

    def _sync_card_history(self, db: Session, trello_board_id: str) -> int:
        """Synchronise l'historique des cartes"""
        cards = db.query(Card).filter(Card.trello_id.in_(
            [c.get("id") for c in self.trello.get_board_cards(trello_board_id)]
        )).all()

        count = 0
        for card in cards:
            try:
                trello_actions = self.trello.get_card_actions(card.trello_id)

                for action in trello_actions:
                    action_type = action.get("type")
                    action_trello_id = action.get("id")

                    existing_action = db.query(CardHistory).filter(
                        CardHistory.action_trello_id == action_trello_id
                    ).first()

                    if existing_action:
                        continue

                    if action_type == "updateCard" and "data" in action:
                        card_data = action["data"].get("card", {})
                        list_data = action["data"].get("list", {})
                        list_before = action["data"].get("listBefore", {})

                        if "idList" in card_data or list_before:
                            from_list_id = None
                            to_list_id = None

                            if list_before:
                                from_list = db.query(List).filter(
                                    List.trello_id == list_before.get("id")
                                ).first()
                                if from_list:
                                    from_list_id = from_list.id

                            if list_data:
                                to_list = db.query(List).filter(
                                    List.trello_id == list_data.get("id")
                                ).first()
                                if to_list:
                                    to_list_id = to_list.id

                            if to_list_id:
                                new_history = CardHistory(
                                    card_id=card.id,
                                    from_list_id=from_list_id,
                                    to_list_id=to_list_id,
                                    moved_at=self._parse_datetime(
                                        action.get("date")
                                    ),
                                    action_trello_id=action_trello_id,
                                    action_type=ActionTypeEnum.UPDATE_CARD_LIST,
                                )
                                db.add(new_history)
                                count += 1

                    elif action_type == "createCard":
                        to_list = db.query(List).filter(
                            List.trello_id == action["data"]["list"]["id"]
                        ).first()

                        if to_list:
                            new_history = CardHistory(
                                card_id=card.id,
                                from_list_id=None,
                                to_list_id=to_list.id,
                                moved_at=self._parse_datetime(
                                    action.get("date")
                                ),
                                action_trello_id=action_trello_id,
                                action_type=ActionTypeEnum.CREATE_CARD,
                            )
                            db.add(new_history)
                            count += 1

            except IntegrityError:
                db.rollback()
                logger.warning(f"⚠️  Duplicate action for card {card.trello_id}")
                continue
            except Exception as e:
                logger.error(f"❌ Error syncing history for card {card.trello_id}: {e}")
                continue

        db.commit()
        return count

    # ========================================================================
    # 3. HELPERS - MAPPING ET PARSING
    # ========================================================================

    def _map_workflow_stage(self, list_name: str) -> WorkflowStageEnum:
        """Mappe le nom de la liste Trello → WorkflowStageEnum (robuste & multilingue)"""
        if not list_name:
            return WorkflowStageEnum.OTHER

        name_lower = list_name.lower()
        # Normaliser: remplacer underscores et espaces multiples par un seul espace
        name_normalized = re.sub(r'[\s_]+', ' ', name_lower).strip()

        # Mapping flexible (gère l'anglais et le français du Trello)
        mapping = [
            # Product Backlog
            ("product backlog", WorkflowStageEnum.PRODUCT_BACKLOG),

            # Sprint Backlog
            ("sprint backlog", WorkflowStageEnum.SPRINT_BACKLOG),

            # In Progress / En cours
            ("in progress", WorkflowStageEnum.IN_PROGRESS),
            ("en cours", WorkflowStageEnum.IN_PROGRESS),

            # Done / À valider / Waiting validation
            ("waiting validation", WorkflowStageEnum.WAITING_VALIDATION),
            ("à valider", WorkflowStageEnum.WAITING_VALIDATION),
            ("a valider", WorkflowStageEnum.WAITING_VALIDATION),

            # Done Sprint / Fini ce sprint
            ("done sprint", WorkflowStageEnum.DONE_SPRINT),
            ("fini ce sprint", WorkflowStageEnum.DONE_SPRINT),

            # Done Preprod
            ("done preprod", WorkflowStageEnum.DONE_PREPROD),
            ("préprod", WorkflowStageEnum.DONE_PREPROD),
            ("preprod", WorkflowStageEnum.DONE_PREPROD),

            # In Prod / En prod
            ("in prod", WorkflowStageEnum.IN_PROD),
            ("en prod", WorkflowStageEnum.IN_PROD),

            # Autres
            ("feedback", WorkflowStageEnum.FEEDBACK),
            ("retrospective", WorkflowStageEnum.RETROSPECTIVE),
            ("waiting", WorkflowStageEnum.WAITING),
        ]

        for key, enum_member in mapping:
            if key in name_normalized:
                # Utilise .name (ex: SPRINT_BACKLOG) au lieu de .value pour le log, c'est plus sûr
                logger.debug(f"  ✅ Mapped list '{list_name}' → {enum_member.name}")
                return enum_member

        logger.warning(f"  ⚠️  No workflow stage mapped for '{list_name}' → defaulting to OTHER")
        return WorkflowStageEnum.OTHER

    def _parse_label_type(self, label_name: str) -> Tuple[LabelTypeEnum, Optional[int]]:
        """Parse le nom du label → type + sprint_number"""
        label_lower = label_name.lower()

        if "sprint" in label_lower:
            match = re.search(r'\d+', label_name)
            sprint_num = int(match.group()) if match else None
            return LabelTypeEnum.SPRINT, sprint_num

        if "project" in label_lower:
            return LabelTypeEnum.PROJECT, None
        if "module" in label_lower:
            return LabelTypeEnum.MODULE, None
        if "status" in label_lower:
            return LabelTypeEnum.STATUS, None
        if "work" in label_lower or "type" in label_lower:
            return LabelTypeEnum.WORK_TYPE, None

        return LabelTypeEnum.OTHER, None

    def _parse_datetime(self, datetime_str: Optional[str]) -> Optional[datetime]:
        """Parse une chaîne datetime ISO 8601 (Timezone-Aware)"""
        if not datetime_str:
            return None
        try:
            return datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
        except Exception as e:
            logger.warning(f"⚠️  Could not parse datetime: {datetime_str} ({e})")
            return None

    def _get_avatar_url(self, trello_member: Dict[str, Any]) -> Optional[str]:
        """Construit l'URL de l'avatar Trello"""
        avatar_hash = trello_member.get("avatarHash")
        if avatar_hash:
            return f"https://trello-avatars.s3.amazonaws.com/{avatar_hash}/170.jpg"
        return None