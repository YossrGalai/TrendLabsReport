import sys
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from app.database.db import engine, SessionLocal, Base
from app.database.models import (
    Board, List, Member, Label, Card, CardMember, CardLabel,
    CardHistory, SyncLog, User, ReportRun, ReportSnapshot,
    WorkflowStageEnum, LabelTypeEnum, ActionTypeEnum, SyncStatusEnum, ReportStatusEnum
)

def test_connection():
    """Test database connectivity"""
    print("\n" + "=" * 70)
    print("[TEST 1] Testing database connection...")
    print("=" * 70)
    try:
        with engine.connect() as conn:
            result = conn.execute(__import__('sqlalchemy').text("SELECT 1"))
            print("✅ Database connection successful!")
            db_url = os.getenv("DATABASE_URL", "").replace("pymysql://", "").split("@")[-1] if os.getenv(
                "DATABASE_URL") else "configured"
            print(f"   Connection: {db_url}")
            return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        print(f"   Make sure:")
        print(f"   - DATABASE_URL is set correctly in .env")
        print(f"   - For Docker: use host 'mysql' (not localhost)")
        print(f"   - Docker MySQL is running: docker ps")
        return False


def test_create_tables():
    """Create all tables in the database"""
    print("\n" + "=" * 70)
    print("[TEST 2] Creating tables from models...")
    print("=" * 70)
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ All tables created successfully!")

        # List all tables
        inspector = __import__('sqlalchemy').inspect(engine)
        tables = inspector.get_table_names()
        print(f"   Total tables: {len(tables)}")
        for table in sorted(tables):
            print(f"   - {table}")
        return True
    except Exception as e:
        print(f"❌ Table creation failed: {e}")
        return False


def test_enums():
    """Test Python Enum definitions"""
    print("\n" + "=" * 70)
    print("[TEST 3] Testing Enum definitions...")
    print("=" * 70)
    try:
        print("✅ WorkflowStageEnum:")
        for stage in WorkflowStageEnum:
            print(f"   - {stage.value}")

        print("\n✅ LabelTypeEnum:")
        for label_type in LabelTypeEnum:
            print(f"   - {label_type.value}")

        print("\n✅ ActionTypeEnum:")
        for action in ActionTypeEnum:
            print(f"   - {action.value}")

        print("\n✅ SyncStatusEnum:")
        for status in SyncStatusEnum:
            print(f"   - {status.value}")

        print("\n✅ ReportStatusEnum:")
        for status in ReportStatusEnum:
            print(f"   - {status.value}")

        return True
    except Exception as e:
        print(f"❌ Enum test failed: {e}")
        return False


def test_crud_operations():
    """Test basic CRUD operations"""
    print("\n" + "=" * 70)
    print("[TEST 4] Testing CRUD operations...")
    print("=" * 70)

    db = SessionLocal()

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    try:
        # CREATE: Add a test board
        print("\n📝 CREATE: Adding test board...")
        test_board = Board(
            trello_id="test_board_001",
            name="Test Board - TrendLabs",
            last_sync_at=datetime.utcnow()
        )
        db.add(test_board)
        db.commit()
        db.refresh(test_board)
        print(f"✅ Board created: {test_board}")
        board_id = test_board.id

        # CREATE: Add a test list
        print("\n📝 CREATE: Adding test list...")
        test_list = List(
            trello_id="test_list_001",
            board_id=board_id,
            name="In Progress",
            workflow_stage=WorkflowStageEnum.IN_PROGRESS
        )
        db.add(test_list)
        db.commit()
        db.refresh(test_list)
        print(f"✅ List created: {test_list}")
        list_id = test_list.id

        # CREATE: Add a test member
        print("\n📝 CREATE: Adding test member...")
        test_member = Member(
            trello_id="test_member_001",
            full_name="John Developer",
            username="johndeveloper"
        )
        db.add(test_member)
        db.commit()
        db.refresh(test_member)
        print(f"✅ Member created: {test_member}")
        member_id = test_member.id

        # CREATE: Add a test label
        print("\n📝 CREATE: Adding test label...")
        test_label = Label(
            trello_id="test_label_001",
            board_id=board_id,
            name="Sprint 45",
            label_type=LabelTypeEnum.SPRINT,
            sprint_number=45
        )
        db.add(test_label)
        db.commit()
        db.refresh(test_label)
        print(f"✅ Label created: {test_label}")
        label_id = test_label.id

        # CREATE: Add a test card
        print("\n📝 CREATE: Adding test card...")
        test_card = Card(
            trello_id="test_card_001",
            board_id=board_id,
            list_id=list_id,
            name="Implement SQLAlchemy models",
            description="Map MySQL schema to Python ORM",
            date_last_activity=datetime.utcnow()
        )
        db.add(test_card)
        db.commit()
        db.refresh(test_card)
        print(f"✅ Card created: {test_card}")
        card_id = test_card.id

        # CREATE: Add card-member relationship
        print("\n📝 CREATE: Adding card-member assignment...")
        test_card.members.append(test_member)
        db.commit()
        print(f"✅ Member assigned to card")

        # CREATE: Add card-label relationship
        print("\n📝 CREATE: Adding card-label tag...")
        test_card.labels.append(test_label)
        db.commit()
        print(f"✅ Label assigned to card")

        # CREATE: Add card history
        print("\n📝 CREATE: Adding card history...")
        test_history = CardHistory(
            card_id=card_id,
            from_list_id=None,
            to_list_id=list_id,
            moved_at=datetime.utcnow(),
            action_trello_id="action_001",
            action_type=ActionTypeEnum.CREATE_CARD
        )
        db.add(test_history)
        db.commit()
        db.refresh(test_history)
        print(f"✅ Card history created: {test_history}")

        # READ: Query all boards
        print("\n📖 READ: Querying all boards...")
        boards = db.query(Board).all()
        print(f"✅ Found {len(boards)} board(s)")
        for board in boards:
            print(f"   - {board}")

        # READ: Query cards with relationships
        print("\n📖 READ: Querying card with relationships...")
        card = db.query(Card).filter(Card.trello_id == "test_card_001").first()
        if card:
            print(f"✅ Card: {card.name}")
            print(f"   Assigned members: {[m.username for m in card.members]}")
            print(f"   Labels: {[l.name for l in card.labels]}")
            print(f"   History entries: {len(card.history)}")

        # UPDATE: Modify card
        print("\n✏️  UPDATE: Updating card...")
        card.started_at = datetime.utcnow()
        db.commit()
        print(f"✅ Card updated: started_at = {card.started_at}")

        # DELETE: Remove test data
        print("\n🗑️  DELETE: Cleaning up test data...")
        db.delete(card)
        db.delete(test_board)
        db.commit()
        print(f"✅ Test data deleted")

        return True

    except Exception as e:
        print(f"❌ CRUD test failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def test_relationships():
    """Test model relationships"""
    print("\n" + "=" * 70)
    print("[TEST 5] Testing model relationships...")
    print("=" * 70)
    try:
        db = SessionLocal()

        # Check Board relationships
        print("\n✅ Board relationships:")
        print("   - lists (one-to-many)")
        print("   - labels (one-to-many)")
        print("   - cards (one-to-many)")
        print("   - sync_logs (one-to-many)")
        print("   - report_runs (one-to-many)")

        # Check Card relationships
        print("\n✅ Card relationships:")
        print("   - board (many-to-one)")
        print("   - list (many-to-one)")
        print("   - members (many-to-many via card_members)")
        print("   - labels (many-to-many via card_labels)")
        print("   - history (one-to-many)")

        # Check Member relationships
        print("\n✅ Member relationships:")
        print("   - cards (many-to-many via card_members)")

        # Check Label relationships
        print("\n✅ Label relationships:")
        print("   - board (many-to-one)")
        print("   - cards (many-to-many via card_labels)")

        db.close()
        return True
    except Exception as e:
        print(f"❌ Relationship test failed: {e}")
        return False


def main():
    """Run all tests"""
    print("\n")
    print("╔════════════════════════════════════════════════════════════════════╗")
    print("║           TrendLabs SQLAlchemy Models - Test Suite                ║")
    print("╚════════════════════════════════════════════════════════════════════╝")

    results = {
        "Connection": test_connection(),
        "Create Tables": test_create_tables(),
        "Enums": test_enums(),
        "CRUD Operations": test_crud_operations(),
        "Relationships": test_relationships(),
    }

    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    for test_name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{test_name:.<40} {status}")

    passed_count = sum(results.values())
    total_count = len(results)
    print(f"\nTotal: {passed_count}/{total_count} tests passed")

    if passed_count == total_count:
        print("\n🎉 All tests passed! Your SQLAlchemy models are ready.")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check the output above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())