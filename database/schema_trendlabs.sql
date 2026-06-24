-- ============================================================================
-- 1. DATABASE CREATION
-- ============================================================================

DROP DATABASE IF EXISTS trendlabs_db;

CREATE DATABASE trendlabs_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE trendlabs_db;

-- ============================================================================
-- 2. TRELLO TABLES — DATA SYNCHRONIZED FROM TRELLO
-- ============================================================================

/**
 * TABLE: boards
 * Role: Catalog of Trello boards to synchronize
 * Each board = a squad or project
 */
CREATE TABLE boards (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal auto-incremented ID',
  trello_id VARCHAR(50) UNIQUE NOT NULL COMMENT 'Trello board ID (from API)',
  name VARCHAR(255) NOT NULL COMMENT 'Board name (ex: Team AFH - Squad 1)',
  last_sync_at DATETIME NULL COMMENT 'Timestamp of last successful sync',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Date added to DB',

  INDEX idx_trello_id (trello_id),
  INDEX idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Catalog of configured Trello boards';

/**
 * TABLE: lists
 * Role: Trello lists (columns) of each board
 * The workflow_stage field normalizes varied list names
 */
CREATE TABLE lists (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal ID',
  trello_id VARCHAR(50) UNIQUE NOT NULL COMMENT 'Trello list ID',
  board_id INT NOT NULL COMMENT 'Parent board',
  name VARCHAR(255) NOT NULL COMMENT 'Exact Trello name (ex: In Progress, Done this Sprint)',
  workflow_stage ENUM(
    'product_backlog',
    'sprint_backlog',
    'in_progress',
    'waiting_validation',
    'done_sprint',
    'done_preprod',
    'in_prod',
    'waiting',
    'feedback',
    'retrospective',
    'other'
  ) NOT NULL COMMENT 'Normalized workflow stage',
  position FLOAT NULL COMMENT 'List position in board',
  is_archived BOOLEAN DEFAULT FALSE COMMENT 'TRUE if archived in Trello',

  FOREIGN KEY (board_id) REFERENCES boards(id) ON DELETE CASCADE,
  INDEX idx_board_id (board_id),
  INDEX idx_workflow_stage (workflow_stage),
  INDEX idx_trello_id (trello_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Trello lists (columns)';

/**
 * TABLE: members
 * Role: Catalog of developers/contributors on boards
 */
CREATE TABLE members (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal ID',
  trello_id VARCHAR(50) UNIQUE NOT NULL COMMENT 'Trello member ID',
  full_name VARCHAR(255) NULL COMMENT 'Full name (ex: Negra Mohamed)',
  username VARCHAR(100) NULL COMMENT 'Trello short username (ex: NM)',
  avatar_url VARCHAR(500) NULL COMMENT 'Trello avatar URL',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Date added to DB',

  INDEX idx_trello_id (trello_id),
  INDEX idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Assignable Trello members';

/**
 * TABLE: labels
 * Role: Catalog of Trello labels
 * Classified by type (project, module, work_type, status, sprint, other)
 */
CREATE TABLE labels (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal ID',
  trello_id VARCHAR(50) UNIQUE NOT NULL COMMENT 'Trello label ID',
  board_id INT NOT NULL COMMENT 'Parent board',
  name VARCHAR(100) NOT NULL COMMENT 'Exact Trello name (ex: CAP, Bug, Sprint 32)',
  color VARCHAR(50) NULL COMMENT 'Trello color (red, sky, lime, etc)',
  label_type ENUM(
    'project',
    'module',
    'work_type',
    'status',
    'sprint',
    'other'
  ) NOT NULL DEFAULT 'other' COMMENT 'Type calculated at sync',
  sprint_number INT NULL COMMENT 'Number extracted by regex if type=sprint',

  FOREIGN KEY (board_id) REFERENCES boards(id) ON DELETE CASCADE,
  INDEX idx_board_id (board_id),
  INDEX idx_label_type (label_type),
  INDEX idx_sprint_number (sprint_number),
  INDEX idx_trello_id (trello_id),

  CONSTRAINT chk_sprint_number CHECK (
    (label_type = 'sprint' AND sprint_number IS NOT NULL) OR
    (label_type != 'sprint' AND sprint_number IS NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Classified Trello labels (tags)';

/**
 * TABLE: cards
 * Role: CENTRAL TABLE — Each Trello ticket
 * Dates (created_at, started_at, completed_at) and duration are calculated from card_history
 */
CREATE TABLE cards (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal ID',
  trello_id VARCHAR(50) UNIQUE NOT NULL COMMENT 'Trello card ID',
  board_id INT NOT NULL COMMENT 'Parent board (denormalized for performance)',
  list_id INT NOT NULL COMMENT 'Current card list',
  name TEXT NOT NULL COMMENT 'Card title',
  description TEXT NULL COMMENT 'Card body/description',
  due_date DATETIME NULL COMMENT 'Due date (NULL = missing date anomaly)',
  due_complete BOOLEAN DEFAULT FALSE COMMENT 'Marked as complete by member',
  date_last_activity DATETIME NOT NULL COMMENT 'Last modification timestamp',
  is_closed BOOLEAN DEFAULT FALSE COMMENT 'TRUE if archived',
  comments_count INT DEFAULT 0 COMMENT 'Comment counter',

  created_at DATETIME NULL COMMENT 'Real creation date (from createCard action)',
  started_at DATETIME NULL COMMENT 'First move to in_progress (REPORT START DATE)',
  completed_at DATETIME NULL COMMENT 'Last move to done_* or in_prod (REPORT END DATE)',
  duration_working_days INT NULL COMMENT 'Working days (Mon-Fri) between started_at and completed_at',

  synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Last sync timestamp',

  FOREIGN KEY (board_id) REFERENCES boards(id) ON DELETE CASCADE,
  FOREIGN KEY (list_id) REFERENCES lists(id),
  INDEX idx_board_id (board_id),
  INDEX idx_list_id (list_id),
  INDEX idx_trello_id (trello_id),
  INDEX idx_is_closed (is_closed),
  INDEX idx_started_at (started_at),
  INDEX idx_completed_at (completed_at),
  INDEX idx_cards_board_started_completed (board_id, started_at, completed_at),
  FULLTEXT INDEX ft_name_description (name, description),

  CONSTRAINT chk_dates_order CHECK (
    (started_at IS NULL OR completed_at IS NULL) OR
    (completed_at >= started_at)
  ),

  CONSTRAINT chk_duration_positive CHECK (
    duration_working_days IS NULL OR
    duration_working_days > 0
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Trello cards (tickets) — CENTRAL TABLE';

-- ============================================================================
-- 3. JUNCTION TABLES (N:N)
-- ============================================================================

/**
 * TABLE: card_members
 * Role: N:N junction between cards and members
 * One card can have multiple assignees
 */
CREATE TABLE card_members (
  card_id INT NOT NULL COMMENT 'Card reference',
  member_id INT NOT NULL COMMENT 'Assigned member reference',

  PRIMARY KEY (card_id, member_id),
  FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE,
  FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE,
  INDEX idx_member_id (member_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Assignments: which developers work on which cards';

/**
 * TABLE: card_labels
 * Role: N:N junction between cards and labels
 * One card can have multiple labels (project, module, work_type, status, sprint)
 */
CREATE TABLE card_labels (
  card_id INT NOT NULL COMMENT 'Card reference',
  label_id INT NOT NULL COMMENT 'Applied label reference',

  PRIMARY KEY (card_id, label_id),
  FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE,
  FOREIGN KEY (label_id) REFERENCES labels(id) ON DELETE CASCADE,
  INDEX idx_card_labels_search (label_id, card_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Tags: which labels are applied to which cards';

-- ============================================================================
-- 4. HISTORY TABLE — ESSENTIAL FOR CALCULATING DURATIONS AND DATES
-- ============================================================================

/**
 * TABLE: card_history
 * Role: CRITICAL — Complete traceability of card movements
 * Required to calculate created_at, started_at, completed_at, duration
 *
 * action_trello_id is UNIQUE to guarantee idempotence (replaying a sync does not add duplicates)
 */
CREATE TABLE card_history (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal ID',
  card_id INT NOT NULL COMMENT 'Card affected by this event',
  from_list_id INT NULL COMMENT 'Source list (NULL if creation)',
  to_list_id INT NOT NULL COMMENT 'Destination list after movement',
  moved_at DATETIME NOT NULL COMMENT 'Exact event timestamp',
  action_trello_id VARCHAR(50) UNIQUE NOT NULL COMMENT 'Unique Trello action ID (idempotence key)',
  action_type ENUM('createCard', 'updateCard_list') NOT NULL COMMENT 'Action type',

  FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE,
  FOREIGN KEY (from_list_id) REFERENCES lists(id) ON DELETE SET NULL,
  FOREIGN KEY (to_list_id) REFERENCES lists(id),
  INDEX idx_card_id (card_id),
  INDEX idx_moved_at (moved_at),
  INDEX idx_action_type (action_type),
  INDEX idx_action_trello_id (action_trello_id),
  INDEX idx_card_history_card_date (card_id, moved_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Card movement history (creation, list changes)';

-- ============================================================================
-- 5. INFRASTRUCTURE TABLES
-- ============================================================================

/**
 * TABLE: sync_logs
 * Role: Audit and traceability of each daily synchronization
 * Allows diagnosing errors and verifying temporal coverage
 */
CREATE TABLE sync_logs (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal ID',
  board_id INT NOT NULL COMMENT 'Synchronized board',
  sync_date DATE NOT NULL COMMENT 'Date of processed day (ex: 2026-06-19)',
  sync_start DATETIME NOT NULL COMMENT 'Execution start timestamp',
  sync_end DATETIME NULL COMMENT 'Execution end timestamp (NULL if in progress or failed)',
  since_datetime DATETIME NOT NULL COMMENT 'Lower bound for Trello query (06:00)',
  before_datetime DATETIME NOT NULL COMMENT 'Upper bound for Trello query (23:59)',
  actions_fetched INT DEFAULT 0 COMMENT 'Total actions fetched from API',
  cards_updated INT DEFAULT 0 COMMENT 'Cards created or updated in DB',
  status ENUM('running', 'success', 'error') NOT NULL DEFAULT 'running' COMMENT 'Sync state',
  error_message TEXT NULL COMMENT 'Python error message if failed',

  FOREIGN KEY (board_id) REFERENCES boards(id) ON DELETE CASCADE,
  INDEX idx_board_id (board_id),
  INDEX idx_sync_date (sync_date),
  INDEX idx_status (status),

  CONSTRAINT chk_sync_times CHECK (
    sync_end IS NULL OR
    sync_end >= sync_start
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Daily synchronization log';

/**
 * TABLE: users
 * Role: Web application user accounts
 * Independent from Trello members — for web authentication
 */
CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal ID',
  email VARCHAR(255) UNIQUE NOT NULL COMMENT 'Login email',
  hashed_password VARCHAR(255) NOT NULL COMMENT 'Bcrypt hashed password',
  full_name VARCHAR(255) NULL COMMENT 'Display name',
  is_active BOOLEAN DEFAULT TRUE COMMENT 'FALSE = account disabled',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Account creation date',

  INDEX idx_email (email),
  INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Web application user accounts';

/**
 * TABLE: report_runs
 * Role: History of each monthly Excel report generation
 * Allows accessing and regenerating previous reports
 */
CREATE TABLE report_runs (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal ID',
  board_id INT NOT NULL COMMENT 'Board this report concerns',
  report_month TINYINT NOT NULL COMMENT 'Report month (1-12)',
  report_year SMALLINT NOT NULL COMMENT 'Report year (ex: 2026)',
  status ENUM('pending', 'running', 'done', 'error') NOT NULL DEFAULT 'pending' COMMENT 'Generation state',
  file_path VARCHAR(500) NULL COMMENT 'Path to generated .xlsx file',
  generated_at DATETIME NULL COMMENT 'Generation completion timestamp',
  generated_by INT NULL COMMENT 'User who triggered generation (NULL if automatic)',
  error_message TEXT NULL COMMENT 'Error message if status=error',

  FOREIGN KEY (board_id) REFERENCES boards(id) ON DELETE CASCADE,
  FOREIGN KEY (generated_by) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_board_id (board_id),
  INDEX idx_report_month_year (report_year, report_month),
  INDEX idx_status (status),
  UNIQUE KEY uk_board_month_year (board_id, report_month, report_year),

  CONSTRAINT chk_report_month CHECK (
    report_month >= 1 AND report_month <= 12
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Excel report generation history';

/**
 * TABLE: report_snapshots
 * Role: JSON snapshot of Trello data at generation moment
 * Allows regenerating past report identically even if Trello changed
 */
CREATE TABLE report_snapshots (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'Internal ID',
  report_run_id INT NOT NULL COMMENT 'Report this snapshot belongs to',
  snapshot_data JSON NOT NULL COMMENT 'Complete Trello data (cards, members, labels, history)',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Snapshot timestamp',

  FOREIGN KEY (report_run_id) REFERENCES report_runs(id) ON DELETE CASCADE,
  INDEX idx_report_run_id (report_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='JSON snapshots of Trello data for report regeneration';

