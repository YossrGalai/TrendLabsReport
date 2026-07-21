from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float,
    ForeignKey, UniqueConstraint, Index, CheckConstraint,
    Enum, JSON
)
from sqlalchemy.orm import relationship
from app.database.db import Base


# ============================================================================
# 1. PYTHON ENUMS (map to MySQL ENUM types)
# ============================================================================

class WorkflowStageEnum(str, PyEnum):
    PRODUCT_BACKLOG = "PRODUCT_BACKLOG"
    SPRINT_BACKLOG = "SPRINT_BACKLOG"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_QA = "WAITING_QA"
    WAITING_VALIDATION = "WAITING_VALIDATION"
    DONE_SPRINT = "DONE_SPRINT"
    DONE_PREPROD = "DONE_PREPROD"
    IN_PROD = "IN_PROD"
    WAITING = "WAITING"
    FEEDBACK = "FEEDBACK"
    RETROSPECTIVE = "RETROSPECTIVE"
    OTHER = "OTHER"


class LabelTypeEnum(str, PyEnum):
    PROJECT = "project"
    MODULE = "module"
    WORK_TYPE = "work_type"
    STATUS = "status"
    SPRINT = "sprint"
    OTHER = "other"


class ActionTypeEnum(str, PyEnum):
    CREATE_CARD = "createCard"
    UPDATE_CARD_LIST = "updateCard_list"


class SyncStatusEnum(str, PyEnum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class ReportStatusEnum(str, PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


# ============================================================================
# 2. TRELLO SYNC TABLES
# ============================================================================

class Board(Base):
    __tablename__ = "boards"

    id = Column(Integer, primary_key=True, index=True, comment="Internal auto-incremented ID")
    trello_id = Column(String(50), unique=True, nullable=False, index=True, comment="Trello board ID")
    name = Column(String(255), nullable=False, index=True, comment="Board name")
    last_sync_at = Column(DateTime, nullable=True, comment="Last successful sync timestamp")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="Date added to DB")

    # Relationships
    lists = relationship("List", back_populates="board", cascade="all, delete-orphan")
    labels = relationship("Label", back_populates="board", cascade="all, delete-orphan")
    cards = relationship("Card", back_populates="board", cascade="all, delete-orphan")
    sync_logs = relationship("SyncLog", back_populates="board", cascade="all, delete-orphan")
    report_runs = relationship("ReportRun", back_populates="board", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Board(id={self.id}, name='{self.name}', trello_id='{self.trello_id}')>"


class List(Base):
    __tablename__ = "lists"

    id = Column(Integer, primary_key=True, index=True, comment="Internal ID")
    trello_id = Column(String(50), unique=True, nullable=False, index=True, comment="Trello list ID")
    board_id = Column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True, comment="Parent board")
    name = Column(String(255), nullable=False, comment="Exact Trello name")
    workflow_stage = Column(Enum(WorkflowStageEnum, values_callable=lambda obj: [e.value for e in obj]),nullable=False, index=True, comment="Normalized workflow stage")
    position = Column(Float, nullable=True, comment="List position in board")
    is_archived = Column(Boolean, default=False, comment="TRUE if archived in Trello")

    # Relationships
    board = relationship("Board", back_populates="lists")
    cards = relationship("Card", back_populates="list")
    card_history_from = relationship(
        "CardHistory",
        foreign_keys="CardHistory.from_list_id",
        back_populates="from_list"
    )
    card_history_to = relationship(
        "CardHistory",
        foreign_keys="CardHistory.to_list_id",
        back_populates="to_list"
    )

    def __repr__(self):
        return f"<List(id={self.id}, name='{self.name}', workflow_stage={self.workflow_stage})>"


class Member(Base):
    __tablename__ = "members"

    id = Column(Integer, primary_key=True, index=True, comment="Internal ID")
    trello_id = Column(String(50), unique=True, nullable=False, index=True, comment="Trello member ID")
    full_name = Column(String(255), nullable=True, comment="Full name")
    username = Column(String(100), nullable=True, index=True, comment="Trello short username")
    avatar_url = Column(String(500), nullable=True, comment="Trello avatar URL")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="Date added to DB")

    # Relationships
    cards = relationship("Card", secondary="card_members", back_populates="members")

    def __repr__(self):
        return f"<Member(id={self.id}, username='{self.username}', full_name='{self.full_name}')>"


class Label(Base):
    __tablename__ = "labels"

    id = Column(Integer, primary_key=True, index=True, comment="Internal ID")
    trello_id = Column(String(50), unique=True, nullable=False, index=True, comment="Trello label ID")
    board_id = Column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True, comment="Parent board")
    name = Column(String(100), nullable=False, comment="Exact Trello name")
    color = Column(String(50), nullable=True, comment="Trello color")
    label_type = Column(Enum(LabelTypeEnum, values_callable=lambda obj: [e.value for e in obj]),default=LabelTypeEnum.OTHER, nullable=False, index=True, comment="Type calculated at sync")
    sprint_number = Column(Integer, nullable=True, index=True, comment="Sprint number if type=sprint")

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "(label_type = 'sprint' AND sprint_number IS NOT NULL) OR (label_type != 'sprint' AND sprint_number IS NULL)",
            name="chk_sprint_number"
        ),
        Index("idx_board_id", "board_id"),
        Index("idx_label_type", "label_type"),
        Index("idx_sprint_number", "sprint_number"),
        Index("idx_trello_id", "trello_id"),
    )

    # Relationships
    board = relationship("Board", back_populates="labels")
    cards = relationship("Card", secondary="card_labels", back_populates="labels")
    # Rapports générés pour ce label PROJECT (n'a de sens que si label_type == PROJECT,
    # mais rien n'empêche au niveau SQL un autre type — la validation se fait côté service,
    # cf. ExcelReportService.generate_monthly_report qui lève une ValueError sinon).
    report_runs = relationship("ReportRun", back_populates="project_label")

    def __repr__(self):
        return f"<Label(id={self.id}, name='{self.name}', label_type={self.label_type})>"


class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True, index=True, comment="Internal ID")
    trello_id = Column(String(50), unique=True, nullable=False, index=True, comment="Trello card ID")
    board_id = Column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True, comment="Parent board (denormalized)")
    list_id = Column(Integer, ForeignKey("lists.id"), nullable=False, index=True, comment="Current card list")
    name = Column(Text, nullable=False, comment="Card title")
    description = Column(Text, nullable=True, comment="Card body/description")
    due_date = Column(DateTime, nullable=True, comment="Due date (NULL = missing date anomaly)")
    due_complete = Column(Boolean, default=False, comment="Marked as complete by member")
    date_last_activity = Column(DateTime, nullable=False, comment="Last modification timestamp")
    is_closed = Column(Boolean, default=False, index=True, comment="TRUE if archived")
    comments_count = Column(Integer, default=0, comment="Comment counter")

    # Calculated from history
    created_at = Column(DateTime, nullable=True, comment="Real creation date (from createCard action)")
    started_at = Column(DateTime, nullable=True, index=True, comment="First move to in_progress (REPORT START)")
    completed_at = Column(DateTime, nullable=True, index=True, comment="Last move to done_* (REPORT END), overridden by last_pr_comment_at when present")
    duration_working_days = Column(Integer, nullable=True, comment="Working days (Mon-Fri) between started_at and completed_at")
    duration_real_seconds = Column(Integer, nullable=True, comment="Durée réelle (secondes) entre 1er passage en cours et passage en terminé, par les seuls propriétaires")

    # Calculated from card_history commentCard actions (cf. _sync_card_history)
    last_pr_comment_at = Column(DateTime, nullable=True, comment="Date of the last 'PR #.. created by ..' comment found on the card. When set, takes priority over the list-move-based completed_at, since it reflects the actual developer's work regardless of who later moves the card across lists (often the PO).")

    synced_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="Last sync timestamp")

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "(started_at IS NULL OR completed_at IS NULL) OR (completed_at >= started_at)",
            name="chk_dates_order"
        ),
        CheckConstraint(
            "duration_working_days IS NULL OR duration_working_days > 0",
            name="chk_duration_positive"
        ),
        Index("idx_board_id", "board_id"),
        Index("idx_list_id", "list_id"),
        Index("idx_is_closed", "is_closed"),
        Index("idx_started_at", "started_at"),
        Index("idx_completed_at", "completed_at"),
        Index("idx_cards_board_started_completed", "board_id", "started_at", "completed_at"),
    )

    # Relationships
    board = relationship("Board", back_populates="cards")
    list = relationship("List", back_populates="cards")
    members = relationship("Member", secondary="card_members", back_populates="cards")
    labels = relationship("Label", secondary="card_labels", back_populates="cards")
    history = relationship("CardHistory", back_populates="card", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Card(id={self.id}, name='{self.name[:30]}...', trello_id='{self.trello_id}')>"


# ============================================================================
# 3. JUNCTION TABLES (N:N relationships)
# ============================================================================

class CardMember(Base):
    __tablename__ = "card_members"

    card_id = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True, comment="Card reference")
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), primary_key=True, comment="Member reference")

    def __repr__(self):
        return f"<CardMember(card_id={self.card_id}, member_id={self.member_id})>"


class CardLabel(Base):
    __tablename__ = "card_labels"

    card_id = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True, comment="Card reference")
    label_id = Column(Integer, ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True, comment="Label reference")

    def __repr__(self):
        return f"<CardLabel(card_id={self.card_id}, label_id={self.label_id})>"


# ============================================================================
# 4. HISTORY TABLE (CRITICAL for duration calculations)
# ============================================================================

class CardHistory(Base):
    __tablename__ = "card_history"

    id = Column(Integer, primary_key=True, index=True, comment="Internal ID")
    card_id = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True, comment="Card affected")
    from_list_id = Column(Integer, ForeignKey("lists.id", ondelete="SET NULL"), nullable=True, comment="Source list")
    to_list_id = Column(Integer, ForeignKey("lists.id"), nullable=False, comment="Destination list")
    moved_at = Column(DateTime, nullable=False, index=True, comment="Exact event timestamp")
    action_trello_id = Column(String(50), unique=True, nullable=False, index=True, comment="Trello action ID (idempotence key)")
    action_type = Column(Enum(ActionTypeEnum, values_callable=lambda obj: [e.value for e in obj]),nullable=False, index=True, comment="Action type")
    actor_trello_id = Column(String(50), nullable=True, index=True, comment="Trello ID du memberCreator de l'action")
    actor_full_name = Column(String(255), nullable=True, comment="Nom affiché de l'auteur (debug/audit)")

    __table_args__ = (
        Index("idx_card_id", "card_id"),
        Index("idx_moved_at", "moved_at"),
        Index("idx_action_type", "action_type"),
        Index("idx_action_trello_id", "action_trello_id"),
        Index("idx_card_history_card_date", "card_id", "moved_at"),
    )

    # Relationships
    card = relationship("Card", back_populates="history")
    from_list = relationship(
        "List",
        foreign_keys=[from_list_id],
        back_populates="card_history_from"
    )
    to_list = relationship(
        "List",
        foreign_keys=[to_list_id],
        back_populates="card_history_to"
    )

    def __repr__(self):
        return f"<CardHistory(id={self.id}, card_id={self.card_id}, action={self.action_type})>"


# ============================================================================
# 5. INFRASTRUCTURE TABLES
# ============================================================================

class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, index=True, comment="Internal ID")
    board_id = Column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True, comment="Synchronized board")
    sync_date = Column(DateTime, nullable=False, index=True, comment="Date of processed day")
    sync_start = Column(DateTime, nullable=False, comment="Execution start timestamp")
    sync_end = Column(DateTime, nullable=True, comment="Execution end timestamp (NULL if running/failed)")
    since_datetime = Column(DateTime, nullable=False, comment="Lower bound for Trello query")
    before_datetime = Column(DateTime, nullable=False, comment="Upper bound for Trello query")
    actions_fetched = Column(Integer, default=0, comment="Total actions fetched from API")
    cards_updated = Column(Integer, default=0, comment="Cards created or updated in DB")
    status = Column(Enum(SyncStatusEnum, values_callable=lambda obj: [e.value for e in obj]), default=SyncStatusEnum.RUNNING, nullable=False, index=True, comment="Sync state")
    error_message = Column(Text, nullable=True, comment="Python error message if failed")

    __table_args__ = (
        CheckConstraint(
            "sync_end IS NULL OR sync_end >= sync_start",
            name="chk_sync_times"
        ),
        Index("idx_board_id", "board_id"),
        Index("idx_sync_date", "sync_date"),
        Index("idx_status", "status"),
    )

    # Relationships
    board = relationship("Board", back_populates="sync_logs")

    def __repr__(self):
        return f"<SyncLog(id={self.id}, board_id={self.board_id}, status={self.status})>"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, comment="Internal ID")
    email = Column(String(255), unique=True, nullable=False, index=True, comment="Login email")
    hashed_password = Column(String(255), nullable=False, comment="Bcrypt hashed password")
    full_name = Column(String(255), nullable=True, comment="Display name")
    is_active = Column(Boolean, default=True, index=True, comment="FALSE = account disabled")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="Account creation date")

    # Relationships
    report_runs = relationship("ReportRun", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}', full_name='{self.full_name}')>"


class ReportRun(Base):
    __tablename__ = "report_runs"

    id = Column(Integer, primary_key=True, index=True, comment="Internal ID")
    board_id = Column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True, comment="Board this report concerns")
    # Un rapport = un projet (label Trello de type PROJECT) au sein du board, plus tout le
    # board — cf. ExcelReportService.generate_monthly_report(project_label_id=...). RESTRICT
    # (et non CASCADE) pour ne pas perdre l'historique des rapports si le label est supprimé.
    project_label_id = Column(Integer, ForeignKey("labels.id", ondelete="RESTRICT"), nullable=False, index=True, comment="PROJECT-type label this report concerns")
    report_month = Column(Integer, nullable=False, comment="Report month (1-12) — étiquette de classement, cf. sprint_numbers pour le contenu réel")
    report_year = Column(Integer, nullable=False, comment="Report year")
    # Les 4 numéros de sprint couvrant ce mois de reporting (ex: [31, 32, 33, 34]).
    # Le filtrage réel des cartes se fait sur ces numéros (label SPRINT), pas sur des bornes
    # de dates calendaires — cf. ExcelReportService._fetch_cards_for_sprints().
    sprint_numbers = Column(JSON, nullable=False, comment="Les 4 numéros de sprint composant ce rapport")
    status = Column(Enum(ReportStatusEnum, values_callable=lambda obj: [e.value for e in obj]), default=ReportStatusEnum.PENDING, nullable=False, index=True, comment="Generation state")
    file_path = Column(String(500), nullable=True, comment="Path to generated .xlsx file")
    generated_at = Column(DateTime, nullable=True, comment="Generation completion timestamp")
    generated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, comment="User who triggered generation")
    error_message = Column(Text, nullable=True, comment="Error message if status=error")

    __table_args__ = (
        CheckConstraint(
            "report_month >= 1 AND report_month <= 12",
            name="chk_report_month"
        ),
        # Recalculée sur (board, PROJET, mois, année) : un même board/mois peut désormais
        # avoir plusieurs rapports (un par projet), donc l'ancienne contrainte
        # (board_id, month, year) seule empêcherait de générer le 2e projet du mois.
        UniqueConstraint("board_id", "project_label_id", "report_month", "report_year", name="uk_board_project_month_year"),
        Index("idx_board_id", "board_id"),
        Index("idx_project_label_id", "project_label_id"),
        Index("idx_report_month_year", "report_year", "report_month"),
        Index("idx_status", "status"),
    )

    # Relationships
    board = relationship("Board", back_populates="report_runs")
    project_label = relationship("Label", back_populates="report_runs")
    user = relationship("User", back_populates="report_runs")
    snapshots = relationship("ReportSnapshot", back_populates="report_run", cascade="all, delete-orphan")

    def __repr__(self):
        return (
            f"<ReportRun(id={self.id}, board_id={self.board_id}, "
            f"project_label_id={self.project_label_id}, "
            f"month={self.report_month}/{self.report_year}, status={self.status})>"
        )


class ReportSnapshot(Base):
    __tablename__ = "report_snapshots"

    id = Column(Integer, primary_key=True, index=True, comment="Internal ID")
    report_run_id = Column(Integer, ForeignKey("report_runs.id", ondelete="CASCADE"), nullable=False, index=True, comment="Report this snapshot belongs to")
    snapshot_data = Column(JSON, nullable=False, comment="Complete Trello data snapshot")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="Snapshot timestamp")

    __table_args__ = (
        Index("idx_report_run_id", "report_run_id"),
    )

    # Relationships
    report_run = relationship("ReportRun", back_populates="snapshots")

    def __repr__(self):
        return f"<ReportSnapshot(id={self.id}, report_run_id={self.report_run_id})>"