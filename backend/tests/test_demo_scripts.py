"""Tests for demo scripts seed_demo.py and reset_demo.py (Phase 10, Step 1)."""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import init_db, get_db_connection
from scripts.seed_demo import get_api_client, seed_rohan
from scripts.reset_demo import reset_rohan


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_seed_demo_creates_consistent_rohan_profile():
    """Verify seed_rohan creates learner 'rohan' with 1 recurring mistake and proper Bug DNA."""
    client, _ = get_api_client("http://127.0.0.1:8000")
    profile = seed_rohan(client)


    assert profile["learner_id"] == "rohan"
    assert len(profile["recurring_mistakes"]) == 1
    rec_tag = profile["recurring_mistakes"][0]
    assert rec_tag["tag"] == "unhandled_short_input"
    assert set(rec_tag["challenges"]) == {"last-k-total", "middle-three"}

    # Bug DNA contains the 4 tags
    dna_tags = {item["tag"] for item in profile["bug_dna"]}
    assert "unhandled_short_input" in dna_tags
    assert "boundary_comparison" in dna_tags
    assert "off_by_one" in dna_tags
    assert "print_not_return" in dna_tags

    # Next recommendation targets recurring mistake
    next_ch = profile.get("next_challenge")
    assert next_ch is not None
    assert "unhandled short input" in next_ch["reason"].lower()


def test_reset_demo_preserves_other_learners():
    """Verify reset_rohan deletes ONLY 'rohan' and leaves other learners untouched."""
    conn = get_db_connection()
    with conn:
        conn.execute("INSERT OR IGNORE INTO learners (id, display_name, created_at) VALUES ('judge_alpha', 'Judge Alpha', '2026-10-17');")
    conn.close()

    client, _ = get_api_client("http://127.0.0.1:8000")
    reset_rohan(client)

    # Verify judge_alpha is still in the database
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM learners WHERE id = 'judge_alpha'")
    judge_row = cur.fetchone()
    cur.execute("SELECT id FROM learners WHERE id = 'rohan'")
    rohan_row = cur.fetchone()
    conn.close()

    assert judge_row is not None, "Other learner was deleted!"
    assert rohan_row is not None, "Rohan was not recreated!"
