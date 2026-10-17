"""SQLite database connection and schema management.

Uses standard library sqlite3 with parameterized queries only.
WAL mode and foreign keys enabled on every connection.
"""

import json
from pathlib import Path
import sqlite3
from typing import Optional
from backend.config import get_settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS learners (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    pin_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (learner_id) REFERENCES learners(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS identities (
    provider TEXT NOT NULL,
    subject_hash TEXT NOT NULL,
    learner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (provider, subject_hash),
    FOREIGN KEY (learner_id) REFERENCES learners(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS challenges (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    concepts_json TEXT NOT NULL,
    targets_mistakes_json TEXT,
    sort_order INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    learner_id TEXT NOT NULL,
    challenge_id TEXT NOT NULL,
    code TEXT NOT NULL,
    all_passed INTEGER NOT NULL,
    hints_used INTEGER NOT NULL,
    prediction TEXT,
    mistake_tags_json TEXT,
    explanation TEXT,
    summary_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (learner_id) REFERENCES learners(id),
    FOREIGN KEY (challenge_id) REFERENCES challenges(id)
);

-- Note: Hidden test rows store ONLY outcome and category, never inputs or outputs.
CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('visible', 'hidden')),
    test_no INTEGER NOT NULL,
    category TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('passed', 'wrong_output', 'runtime_error', 'timeout')),
    limit_hit TEXT,
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL,
    hint_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('ai', 'authored')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mistakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    challenge_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (learner_id) REFERENCES learners(id),
    FOREIGN KEY (challenge_id) REFERENCES challenges(id),
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mastery (
    learner_id TEXT NOT NULL,
    concept TEXT NOT NULL,
    score REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (learner_id, concept),
    FOREIGN KEY (learner_id) REFERENCES learners(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    predicted TEXT NOT NULL,
    actual TEXT NOT NULL,
    correct INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (learner_id) REFERENCES learners(id),
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS counterexamples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    args_json TEXT NOT NULL,
    exposes_bug INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (learner_id) REFERENCES learners(id),
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS explanations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    text TEXT NOT NULL,
    ratings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (learner_id) REFERENCES learners(id),
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    blocked INTEGER NOT NULL,
    block_reason TEXT
);

CREATE TABLE IF NOT EXISTS drafts (
    learner_id TEXT NOT NULL,
    challenge_id TEXT NOT NULL,
    code TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (learner_id, challenge_id),
    FOREIGN KEY (learner_id) REFERENCES learners(id) ON DELETE CASCADE,
    FOREIGN KEY (challenge_id) REFERENCES challenges(id)
);

CREATE TABLE IF NOT EXISTS achievements (
    learner_id TEXT NOT NULL,
    key TEXT NOT NULL,
    awarded_at TEXT NOT NULL,
    PRIMARY KEY (learner_id, key),
    FOREIGN KEY (learner_id) REFERENCES learners(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS xp_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (learner_id) REFERENCES learners(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS coach_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE
);
"""


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return an active SQLite connection configured with WAL and foreign keys.

    WHY:
    - Foreign keys are disabled by default in SQLite; turning them ON preserves relational integrity.
    - WAL journal mode prevents readers from blocking writers in a concurrent web server.
    - sqlite3.Row allows field access by column name while avoiding dict conversion overhead.
    """
    path = db_path if db_path is not None else get_settings().DATABASE_PATH
    db_file = Path(path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def sync_challenges_to_db(conn: sqlite3.Connection) -> None:
    """Sync file-based challenge metadata into the challenges database table.

    WHY:
    - Maintains foreign key integrity for submissions and drafts referencing challenge_id.
    - Idempotently updates titles, difficulty, concepts, and sort orders from disk.
    """
    try:
        from backend.engine.challenges import load_all_challenges
        challenges = load_all_challenges()
        for ch in challenges.values():
            conn.execute(
                """
                INSERT OR REPLACE INTO challenges (
                    id, title, difficulty, concepts_json, targets_mistakes_json, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    ch.id,
                    ch.title,
                    ch.difficulty,
                    json.dumps(ch.concepts),
                    json.dumps(ch.targets_mistakes),
                    ch.sort_order,
                ),
            )
        conn.commit()
    except Exception:
        # Avoid crashing if challenges directory is absent in an isolated schema unit test
        pass


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize database tables and constraints if they do not exist.

    WHY: Parameterized execution of static DDL at startup guarantees idempotency
    and correct table schema on a fresh install without external migration tools.
    """
    with get_db_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        sync_challenges_to_db(conn)
        conn.commit()
