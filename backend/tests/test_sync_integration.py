"""
TESTS D'INTÉGRATION - Synchronisation Trello ↔ MySQL
Lance ces tests pour vérifier que la synchronisation fonctionne correctement
"""

import pytest
import logging
from datetime import datetime

from app.database.db import SessionLocal, engine, Base
from app.services.trello.trello_service import TrelloService
from services.sync.sync_service import TrelloSyncService
from app.models.models import (
    Board, List, Member, Label, Card, CardMember, CardLabel, CardHistory,
    WorkflowStageEnum, ActionTypeEnum
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def db():
    """Fixture pour une session BD isolée pour chaque test"""
    # Créer les tables
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    yield db

    # Nettoyer
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def trello_service():
    """Fixture pour TrelloService (nécessite .env avec credentials)"""
    return TrelloService()


@pytest.fixture
def sync_service(trello_service):
    """Fixture pour TrelloSyncService"""
    return TrelloSyncService(trello_service)


# ============================================================================
# TESTS UNITAIRES - HELPERS
# ============================================================================

class TestHelpers:
    """Tests des fonctions utilitaires de mapping"""

    def test_map_workflow_stage(self, sync_service):
        """Teste le mapping des noms de liste → workflow_stage"""
        assert sync_service._map_workflow_stage("Product Backlog") == WorkflowStageEnum.PRODUCT_BACKLOG
        assert sync_service._map_workflow_stage("in progress") == WorkflowStageEnum.IN_PROGRESS
        assert sync_service._map_workflow_stage("waiting validation") == WorkflowStageEnum.WAITING_VALIDATION
        assert sync_service._map_workflow_stage("Unknown List") == WorkflowStageEnum.OTHER

    def test_parse_label_type(self, sync_service):
        """Teste le parsing des label_type"""
        # Test sprint
        label_type, sprint_num = sync_service._parse_label_type("Sprint 1")
        assert label_type.value == "sprint"
        assert sprint_num == 1

        # Test project
        label_type, sprint_num = sync_service._parse_label_type("Project Backend")
        assert label_type.value == "project"
        assert sprint_num is None

        # Test unknown
        label_type, sprint_num = sync_service._parse_label_type("Random Label")
        assert label_type.value == "other"
        assert sprint_num is None

    def test_parse_datetime(self, sync_service):
        """Teste le parsing des datetimes ISO 8601"""
        dt_str = "2024-06-15T10:30:00Z"
        dt = sync_service._parse_datetime(dt_str)
        assert dt is not None
        assert isinstance(dt, datetime)
        assert dt.day == 15


# ============================================================================
# TESTS D'INTÉGRATION - SYNCHRONISATION
# ============================================================================

class TestBoardSync:
    """Tests de synchronisation des boards"""

    def test_sync_board_creation(self, db, sync_service):
        """Test la création d'un board lors de la sync"""
        # Note: Nécessite un board_id Trello valide
        # Ce test est commenté par défaut car il nécessite l'API Trello
        pass

    def test_board_exists_in_db(self, db):
        """Test la présence d'un board en BD après sync"""
        # Créer manuellement un board pour le test
        board = Board(
            trello_id="test_board_123",
            name="Test Board",
            created_at=datetime.utcnow(),
        )
        db.add(board)
        db.commit()

        # Vérifier
        retrieved = db.query(Board).filter(Board.trello_id == "test_board_123").first()
        assert retrieved is not None
        assert retrieved.name == "Test Board"


class TestListSync:
    """Tests de synchronisation des lists"""

    def test_list_creation(self, db):
        """Test la création d'une list"""
        board = Board(
            trello_id="board_123",
            name="Test Board",
            created_at=datetime.utcnow(),
        )
        db.add(board)
        db.flush()

        list_obj = List(
            trello_id="list_123",
            board_id=board.id,
            name="Product Backlog",
            workflow_stage=WorkflowStageEnum.PRODUCT_BACKLOG,
            position=1.0,
        )
        db.add(list_obj)
        db.commit()

        # Vérifier
        retrieved = db.query(List).filter(List.trello_id == "list_123").first()
        assert retrieved is not None
        assert retrieved.workflow_stage == WorkflowStageEnum.PRODUCT_BACKLOG

    def test_list_board_relationship(self, db):
        """Test la relation board → lists"""
        board = Board(
            trello_id="board_123",
            name="Test Board",
            created_at=datetime.utcnow(),
        )
        db.add(board)
        db.flush()

        for i in range(3):
            list_obj = List(
                trello_id=f"list_{i}",
                board_id=board.id,
                name=f"List {i}",
                workflow_stage=WorkflowStageEnum.PRODUCT_BACKLOG,
            )
            db.add(list_obj)

        db.commit()

        # Vérifier via relationship
        retrieved_board = db.query(Board).filter(Board.id == board.id).first()
        assert len(retrieved_board.lists) == 3


class TestCardSync:
    """Tests de synchronisation des cartes"""

    def test_card_creation(self, db):
        """Test la création d'une carte"""
        # Setup
        board = Board(trello_id="board_1", name="Test", created_at=datetime.utcnow())
        list_obj = List(trello_id="list_1", board_id=1, name="List", workflow_stage=WorkflowStageEnum.IN_PROGRESS)
        db.add(board)
        db.flush()
        list_obj.board_id = board.id
        db.add(list_obj)
        db.flush()

        # Créer une carte
        card = Card(
            trello_id="card_1",
            board_id=board.id,
            list_id=list_obj.id,
            name="Test Card",
            description="Test Description",
            date_last_activity=datetime.utcnow(),
            created_at=datetime.utcnow(),
        )
        db.add(card)
        db.commit()

        # Vérifier
        retrieved = db.query(Card).filter(Card.trello_id == "card_1").first()
        assert retrieved is not None
        assert retrieved.name == "Test Card"

    def test_card_member_relationship(self, db):
        """Test l'assignation de members à une carte"""
        # Setup
        board = Board(trello_id="board_1", name="Test", created_at=datetime.utcnow())
        db.add(board)
        db.flush()

        member1 = Member(trello_id="member_1", username="user1", created_at=datetime.utcnow())
        member2 = Member(trello_id="member_2", username="user2", created_at=datetime.utcnow())
        db.add_all([member1, member2])
        db.flush()

        list_obj = List(trello_id="list_1", board_id=board.id, name="List",
                        workflow_stage=WorkflowStageEnum.IN_PROGRESS)
        db.add(list_obj)
        db.flush()

        card = Card(
            trello_id="card_1", board_id=board.id, list_id=list_obj.id,
            name="Card", date_last_activity=datetime.utcnow(), created_at=datetime.utcnow()
        )
        db.add(card)
        db.flush()

        # Ajouter les members
        card_member_1 = CardMember(card_id=card.id, member_id=member1.id)
        card_member_2 = CardMember(card_id=card.id, member_id=member2.id)
        db.add_all([card_member_1, card_member_2])
        db.commit()

        # Vérifier
        retrieved_card = db.query(Card).filter(Card.id == card.id).first()
        assert len(retrieved_card.members) == 2
        assert any(m.username == "user1" for m in retrieved_card.members)
        assert any(m.username == "user2" for m in retrieved_card.members)

    def test_card_label_relationship(self, db):
        """Test l'assignation de labels à une carte"""
        # Setup
        board = Board(trello_id="board_1", name="Test", created_at=datetime.utcnow())
        db.add(board)
        db.flush()

        label1 = Label(
            trello_id="label_1", board_id=board.id, name="Bug",
            label_type=WorkflowStageEnum.OTHER
        )
        label2 = Label(
            trello_id="label_2", board_id=board.id, name="Feature",
            label_type=WorkflowStageEnum.OTHER
        )
        db.add_all([label1, label2])
        db.flush()

        list_obj = List(trello_id="list_1", board_id=board.id, name="List",
                        workflow_stage=WorkflowStageEnum.IN_PROGRESS)
        db.add(list_obj)
        db.flush()

        card = Card(
            trello_id="card_1", board_id=board.id, list_id=list_obj.id,
            name="Card", date_last_activity=datetime.utcnow(), created_at=datetime.utcnow()
        )
        db.add(card)
        db.flush()

        # Ajouter les labels
        card_label_1 = CardLabel(card_id=card.id, label_id=label1.id)
        card_label_2 = CardLabel(card_id=card.id, label_id=label2.id)
        db.add_all([card_label_1, card_label_2])
        db.commit()

        # Vérifier
        retrieved_card = db.query(Card).filter(Card.id == card.id).first()
        assert len(retrieved_card.labels) == 2
        assert any(l.name == "Bug" for l in retrieved_card.labels)


class TestCardHistorySync:
    """Tests de synchronisation de l'historique des cartes"""

    def test_card_history_creation(self, db):
        """Test la création d'un enregistrement d'historique"""
        # Setup
        board = Board(trello_id="board_1", name="Test", created_at=datetime.utcnow())
        db.add(board)
        db.flush()

        list_from = List(
            trello_id="list_1", board_id=board.id, name="Backlog",
            workflow_stage=WorkflowStageEnum.PRODUCT_BACKLOG
        )
        list_to = List(
            trello_id="list_2", board_id=board.id, name="In Progress",
            workflow_stage=WorkflowStageEnum.IN_PROGRESS
        )
        db.add_all([list_from, list_to])
        db.flush()

        card = Card(
            trello_id="card_1", board_id=board.id, list_id=list_to.id,
            name="Card", date_last_activity=datetime.utcnow(), created_at=datetime.utcnow()
        )
        db.add(card)
        db.flush()

        # Créer un historique
        history = CardHistory(
            card_id=card.id,
            from_list_id=list_from.id,
            to_list_id=list_to.id,
            moved_at=datetime.utcnow(),
            action_trello_id="action_123",
            action_type=ActionTypeEnum.UPDATE_CARD_LIST,
        )
        db.add(history)
        db.commit()

        # Vérifier
        retrieved = db.query(CardHistory).filter(CardHistory.action_trello_id == "action_123").first()
        assert retrieved is not None
        assert retrieved.action_type == ActionTypeEnum.UPDATE_CARD_LIST
        assert retrieved.from_list_id == list_from.id

    def test_card_history_relationships(self, db):
        """Test les relations card ↔ history"""
        # Setup (même que le test précédent)
        board = Board(trello_id="board_1", name="Test", created_at=datetime.utcnow())
        db.add(board)
        db.flush()

        list_from = List(
            trello_id="list_1", board_id=board.id, name="Backlog",
            workflow_stage=WorkflowStageEnum.PRODUCT_BACKLOG
        )
        list_to = List(
            trello_id="list_2", board_id=board.id, name="In Progress",
            workflow_stage=WorkflowStageEnum.IN_PROGRESS
        )
        db.add_all([list_from, list_to])
        db.flush()

        card = Card(
            trello_id="card_1", board_id=board.id, list_id=list_to.id,
            name="Card", date_last_activity=datetime.utcnow(), created_at=datetime.utcnow()
        )
        db.add(card)
        db.flush()

        # Ajouter 3 mouvements
        for i in range(3):
            history = CardHistory(
                card_id=card.id,
                from_list_id=list_from.id if i > 0 else None,
                to_list_id=list_to.id if i < 2 else list_from.id,
                moved_at=datetime.utcnow(),
                action_trello_id=f"action_{i}",
                action_type=ActionTypeEnum.UPDATE_CARD_LIST,
            )
            db.add(history)

        db.commit()

        # Vérifier
        retrieved_card = db.query(Card).filter(Card.id == card.id).first()
        assert len(retrieved_card.history) == 3


# ============================================================================
# TESTS D'INTÉGRITÉ
# ============================================================================

class TestDataIntegrity:
    """Tests d'intégrité des données synchronisées"""

    def test_unique_trello_ids(self, db):
        """Teste que les IDs Trello sont uniques"""
        board1 = Board(trello_id="board_1", name="Board 1", created_at=datetime.utcnow())
        db.add(board1)
        db.commit()

        # Essayer d'ajouter un doublon
        board2 = Board(trello_id="board_1", name="Board 2", created_at=datetime.utcnow())
        db.add(board2)

        with pytest.raises(Exception):  # IntegrityError
            db.commit()

    def test_cascade_delete(self, db):
        """Teste la suppression en cascade"""
        board = Board(trello_id="board_1", name="Test", created_at=datetime.utcnow())
        db.add(board)
        db.flush()

        # Ajouter une list
        list_obj = List(
            trello_id="list_1", board_id=board.id, name="List",
            workflow_stage=WorkflowStageEnum.IN_PROGRESS
        )
        db.add(list_obj)
        db.flush()

        # Ajouter une carte
        card = Card(
            trello_id="card_1", board_id=board.id, list_id=list_obj.id,
            name="Card", date_last_activity=datetime.utcnow(), created_at=datetime.utcnow()
        )
        db.add(card)
        db.commit()

        # Supprimer le board
        db.delete(board)
        db.commit()

        # Vérifier que tout est supprimé
        assert db.query(Board).filter(Board.id == board.id).first() is None
        assert db.query(List).filter(List.board_id == board.id).count() == 0
        assert db.query(Card).filter(Card.board_id == board.id).count() == 0


# ============================================================================
# EXÉCUTION DES TESTS
# ============================================================================

if __name__ == "__main__":
    """
    Exécuter avec: pytest tests/test_sync_integration.py -v

    Options utiles:
    - pytest tests/test_sync_integration.py::TestBoardSync -v  (un seul test)
    - pytest tests/ -v --tb=short  (tous les tests)
    """
    pytest.main([__file__, "-v", "--tb=short"])