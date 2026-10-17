"""Tests for SQLite database schema initialization and connection settings."""

from pathlib import Path
import sqlite3
import pytest
from backend.database import get_db_connection, init_db


EXPECTED_TABLES = {
    "learners",
    "sessions",
    "challenges",
    "submissions",
    "test_results",
    "hints",
    "mistakes",
    "mastery",
    "predictions",
    "counterexamples",
    "explanations",
    "ai_logs",
    "drafts",
    "achievements",
    "xp_events",
}


def test_init_db_creates_all_tables(tmp_path: Path):
    """Verify init_db initializes all required tables without error."""
    test_db = tmp_path / "test.db"
    init_db(str(test_db))

    conn = get_db_connection(str(test_db))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row["name"] for row in cursor.fetchall()}
    conn.close()

    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables: {missing}"


def test_pragmas_enabled(tmp_path: Path):
    """Verify WAL journal mode and foreign keys are enabled."""
    test_db = tmp_path / "test_pragmas.db"
    conn = get_db_connection(str(test_db))

    # Check foreign keys
    fk_result = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
    assert fk_result == 1

    # Check journal mode (WAL)
    journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert journal_mode.lower() == "wal"

    conn.close()


def test_test_results_outcome_check_constraint(tmp_path: Path):
    """Verify test_results table strictly rejects invalid outcome strings."""
    test_db = tmp_path / "test_constraints.db"
    init_db(str(test_db))

    conn = get_db_connection(str(test_db))
    # Insert required parent records
    conn.execute(
        "INSERT INTO learners (id, display_name, created_at) VALUES (?, ?, ?);",
        ("learner1", "Alice", "2026-10-16T12:00:00Z"),
    )
    conn.execute(
        "INSERT INTO challenges (id, title, difficulty, concepts_json, sort_order) VALUES (?, ?, ?, ?, ?);",
        ("ch1", "Test Challenge", "easy", "[]", 1),
    )
    conn.execute(
        "INSERT INTO submissions (id, seq, learner_id, challenge_id, code, all_passed, hints_used, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
        ("sub1", 1, "learner1", "ch1", "pass", 0, 0, "2026-10-16T12:00:00Z"),
    )

    # Valid outcome should succeed
    conn.execute(
        "INSERT INTO test_results (submission_id, kind, test_no, outcome) VALUES (?, ?, ?, ?);",
        ("sub1", "visible", 1, "passed"),
    )

    # Invalid outcome should fail check constraint
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO test_results (submission_id, kind, test_no, outcome) VALUES (?, ?, ?, ?);",
            ("sub1", "visible", 2, "invalid_outcome"),
        )

    conn.close()
