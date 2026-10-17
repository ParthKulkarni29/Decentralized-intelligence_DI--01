"""Tests for concept mastery tracking, mistake analysis, and challenge recommendations.

Phase 5, Step 2 verification:
- Pass raises concept mastery (0 hints -> target 1.0, weight 0.30).
- Hints raise mastery less (discounted by hint count down to 0.25 floor).
- Repeated failures lower mastery and cap decay after 3 failures per challenge.
- Cross-concept edge_cases update triggers on unhandled_short_input or boundary_comparison.
- Recurring mistake requires occurrence across at least 2 different challenges.
- Bug DNA tallies all mistake occurrences sorted descending.
- Recommender 4-tier strategy produces exact expected challenge and natural language reasons.
- Bug Habit Broken achievement is awarded on a clean zero-hint pass of a targeted challenge.
- Full API integration via /api/submit and /api/learner/{id}.
"""

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import init_db, get_db_connection
from backend.engine.challenges import FullChallenge

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"


def _execute_via_runner_direct(code: str, function_name: str, tests: list[dict], per_test_timeout: float = 2.0, **kwargs) -> list[dict]:
    """Execute docker/runner/runner.py directly in isolated subprocess when Docker daemon is not active."""
    payload = json.dumps({
        "code": code,
        "function_name": function_name,
        "tests": tests,
        "per_test_timeout": per_test_timeout,
    })
    proc = subprocess.run(
        [sys.executable, "-u", str(RUNNER_SCRIPT)],
        input=payload,
        text=True,
        capture_output=True,
        timeout=25.0,
    )
    if proc.returncode != 0:
        return [
            {"id": t["id"], "status": "error", "value": None, "exc_type": "RuntimeError", "limit": None, "mutated": False}
            for t in tests
        ]
    try:
        data = json.loads(proc.stdout)
        return data.get("results", [])
    except Exception:
        return [
            {"id": t["id"], "status": "error", "value": None, "exc_type": "RuntimeError", "limit": None, "mutated": False}
            for t in tests
        ]

from backend.engine.mastery import (
    ALL_CONCEPTS,
    MISTAKE_DESCRIPTIONS,
    get_learner_mastery,
    update_learner_mastery,
    get_recurring_mistakes,
    get_bug_dna,
    check_and_award_bug_habit_broken,
)
from backend.engine.recommender import recommend_next_challenge


@pytest.fixture
def db_conn(tmp_path: Path):
    """Create a temporary initialized SQLite database."""
    db_file = tmp_path / "test_mastery.db"
    init_db(str(db_file))
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


def _make_dummy_challenge(
    ch_id: str = "ch_dummy",
    concepts: list[str] = None,
    targets_mistakes: list[str] = None,
    difficulty: str = "easy",
    sort_order: int = 1,
) -> FullChallenge:
    """Helper to create a FullChallenge instance for unit tests."""
    return FullChallenge(
        id=ch_id,
        title=f"Title {ch_id}",
        difficulty=difficulty,
        concepts=concepts or ["loops", "lists"],
        description="Dummy challenge for testing.",
        starter_code="def fn(): pass",
        function_name="fn",
        arg_names=[],
        targets_mistakes=targets_mistakes or [],
        boundary_values=[],
        sort_order=sort_order,
        visible_tests=[],
        hidden_tests=[],
        hints={"generic": ["hint1", "hint2"]},
        reference_code="def fn(): pass",
        reference_func=lambda *a: None,
    )


def _seed_learner(conn: sqlite3.Connection, learner_id: str) -> None:
    """Insert learner row to satisfy foreign key constraints."""
    conn.execute(
        "INSERT OR IGNORE INTO learners (id, display_name, created_at) VALUES (?, ?, '2026-01-01T00:00:00Z');",
        (learner_id, learner_id),
    )
    conn.commit()


def _seed_challenge(conn: sqlite3.Connection, ch: FullChallenge) -> None:
    """Insert challenge row to satisfy foreign key constraints."""
    conn.execute(
        """
        INSERT OR IGNORE INTO challenges (id, title, difficulty, concepts_json, targets_mistakes_json, sort_order)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (ch.id, ch.title, ch.difficulty, json.dumps(ch.concepts), json.dumps(ch.targets_mistakes), ch.sort_order),
    )
    conn.commit()


def _seed_submission(
    conn: sqlite3.Connection,
    sub_id: str,
    learner_id: str,
    ch_id: str,
    all_passed: int = 0,
    hints_used: int = 0,
) -> None:
    """Insert submission row to satisfy foreign key constraints."""
    _seed_learner(conn, learner_id)
    conn.execute(
        """
        INSERT OR IGNORE INTO submissions (
            id, seq, learner_id, challenge_id, code, all_passed, hints_used, created_at
        ) VALUES (?, 1, ?, ?, 'code', ?, ?, '2026-01-01T00:00:00Z');
        """,
        (sub_id, learner_id, ch_id, all_passed, hints_used),
    )
    conn.commit()


# ==============================================================================
# Mastery EMA Unit Tests
# ==============================================================================

def test_initial_mastery_is_half(db_conn: sqlite3.Connection):
    """Verify fresh learner starts with 0.5000 across all 6 concepts."""
    mastery = get_learner_mastery(db_conn, "learner_new")
    assert len(mastery) == 6
    for c in ALL_CONCEPTS:
        assert mastery[c] == 0.5


def test_pass_with_zero_hints_raises_mastery(db_conn: sqlite3.Connection):
    """Pass with 0 hints: target 1.0, weight 0.30 -> 0.5 + 0.3*(1 - 0.5) = 0.6500."""
    ch = _make_dummy_challenge(concepts=["loops", "lists"])
    learner = "learner_pass"
    _seed_learner(db_conn, learner)
    _seed_challenge(db_conn, ch)

    updated = update_learner_mastery(
        conn=db_conn,
        learner_id=learner,
        challenge=ch,
        all_passed=True,
        hints_used=0,
        mistake_tags=[],
    )
    assert updated["loops"] == 0.6500
    assert updated["lists"] == 0.6500
    # Other concepts remain untouched at baseline
    assert updated["indexing"] == 0.5000
    assert updated["functions"] == 0.5000


def test_hints_raise_mastery_less(db_conn: sqlite3.Connection):
    """Verify that using hints reduces the mastery gain proportionally."""
    ch = _make_dummy_challenge(concepts=["loops"])
    _seed_challenge(db_conn, ch)

    # 0 hints: weight 0.30 -> 0.6500
    _seed_learner(db_conn, "l0")
    m0 = update_learner_mastery(db_conn, "l0", ch, all_passed=True, hints_used=0, mistake_tags=[])
    # 1 hint: weight 0.30 * 0.75 = 0.225 -> 0.5 + 0.225 * 0.5 = 0.6125
    _seed_learner(db_conn, "l1")
    m1 = update_learner_mastery(db_conn, "l1", ch, all_passed=True, hints_used=1, mistake_tags=[])
    # 2 hints: weight 0.30 * 0.50 = 0.150 -> 0.5 + 0.150 * 0.5 = 0.5750
    _seed_learner(db_conn, "l2")
    m2 = update_learner_mastery(db_conn, "l2", ch, all_passed=True, hints_used=2, mistake_tags=[])
    # 3 hints: weight 0.30 * 0.25 = 0.075 -> 0.5 + 0.075 * 0.5 = 0.5375
    _seed_learner(db_conn, "l3")
    m3 = update_learner_mastery(db_conn, "l3", ch, all_passed=True, hints_used=3, mistake_tags=[])
    # 4 hints: weight 0.30 * 0.25 = 0.075 -> 0.5375 (floored at 0.25)
    _seed_learner(db_conn, "l4")
    m4 = update_learner_mastery(db_conn, "l4", ch, all_passed=True, hints_used=4, mistake_tags=[])

    assert m0["loops"] == 0.6500
    assert m1["loops"] == 0.6125
    assert m2["loops"] == 0.5750
    assert m3["loops"] == 0.5375
    assert m4["loops"] == 0.5375
    assert m0["loops"] > m1["loops"] > m2["loops"] > m3["loops"]


def test_repeated_failures_decay_and_morale_cap_at_three(db_conn: sqlite3.Connection):
    """Verify failure decays mastery (weight 0.10) and stops decaying after 3 prior failures."""
    ch = _make_dummy_challenge(concepts=["conditions"])
    learner = "learner_fail_test"
    _seed_learner(db_conn, learner)
    _seed_challenge(db_conn, ch)

    cur = db_conn.cursor()

    # Attempt 1: 0 prior failures -> weight 0.10 -> 0.5 + 0.1*(0 - 0.5) = 0.4500
    m1 = update_learner_mastery(db_conn, learner, ch, all_passed=False, hints_used=0, mistake_tags=[])
    assert m1["conditions"] == 0.4500
    cur.execute(
        "INSERT INTO submissions (id, seq, learner_id, challenge_id, code, all_passed, hints_used, created_at) VALUES ('s1', 1, ?, ?, 'x', 0, 0, '2026-01-01T00:00:00Z');",
        (learner, ch.id),
    )
    db_conn.commit()

    # Attempt 2: 1 prior failure -> weight 0.10 -> 0.45 + 0.1*(0 - 0.45) = 0.4050
    m2 = update_learner_mastery(db_conn, learner, ch, all_passed=False, hints_used=0, mistake_tags=[])
    assert m2["conditions"] == 0.4050
    cur.execute(
        "INSERT INTO submissions (id, seq, learner_id, challenge_id, code, all_passed, hints_used, created_at) VALUES ('s2', 2, ?, ?, 'x', 0, 0, '2026-01-01T00:01:00Z');",
        (learner, ch.id),
    )
    db_conn.commit()

    # Attempt 3: 2 prior failures -> weight 0.10 -> 0.405 + 0.1*(0 - 0.405) = 0.3645
    m3 = update_learner_mastery(db_conn, learner, ch, all_passed=False, hints_used=0, mistake_tags=[])
    assert m3["conditions"] == 0.3645
    cur.execute(
        "INSERT INTO submissions (id, seq, learner_id, challenge_id, code, all_passed, hints_used, created_at) VALUES ('s3', 3, ?, ?, 'x', 0, 0, '2026-01-01T00:02:00Z');",
        (learner, ch.id),
    )
    db_conn.commit()

    # Attempt 4: 3 prior failures -> fail_count = 3 <= 3 applies 0.10 -> 0.3645 * 0.9 = 0.3281
    m4 = update_learner_mastery(db_conn, learner, ch, all_passed=False, hints_used=0, mistake_tags=[])
    assert m4["conditions"] == 0.3281
    cur.execute(
        "INSERT INTO submissions (id, seq, learner_id, challenge_id, code, all_passed, hints_used, created_at) VALUES ('s4', 4, ?, ?, 'x', 0, 0, '2026-01-01T00:03:00Z');",
        (learner, ch.id),
    )
    db_conn.commit()

    # Attempt 5: 4 prior failures (fail_count = 4 > 3) -> weight 0.0 -> score unchanged!
    m5 = update_learner_mastery(db_conn, learner, ch, all_passed=False, hints_used=0, mistake_tags=[])
    assert m5["conditions"] == 0.3281

    # Attempt 6: 5 prior failures -> still unchanged!
    cur.execute(
        "INSERT INTO submissions (id, seq, learner_id, challenge_id, code, all_passed, hints_used, created_at) VALUES ('s5', 5, ?, ?, 'x', 0, 0, '2026-01-01T00:04:00Z');",
        (learner, ch.id),
    )
    db_conn.commit()
    m6 = update_learner_mastery(db_conn, learner, ch, all_passed=False, hints_used=0, mistake_tags=[])
    assert m6["conditions"] == 0.3281


def test_cross_concept_edge_cases_failure_update(db_conn: sqlite3.Connection):
    """If unhandled_short_input or boundary_comparison triggers on non-edge-cases challenge, update edge_cases."""
    ch = _make_dummy_challenge(concepts=["loops", "lists"])  # does NOT contain 'edge_cases'
    learner = "learner_edge"
    _seed_learner(db_conn, learner)
    _seed_challenge(db_conn, ch)

    # Trigger unhandled_short_input
    m = update_learner_mastery(
        conn=db_conn,
        learner_id=learner,
        challenge=ch,
        all_passed=False,
        hints_used=0,
        mistake_tags=["unhandled_short_input"],
    )
    assert m["loops"] == 0.4500
    assert m["lists"] == 0.4500
    # edge_cases was also penalized despite not being in challenge.concepts
    assert m["edge_cases"] == 0.4500
    # other concepts unaffected
    assert m["functions"] == 0.5000


# ==============================================================================
# Recurring Mistakes and Bug DNA Tests
# ==============================================================================

def test_recurring_mistake_requires_two_different_challenges(db_conn: sqlite3.Connection):
    """A mistake tag is recurring only if seen on >= 2 distinct challenges."""
    cur = db_conn.cursor()
    learner = "learner_recurring"
    _seed_learner(db_conn, learner)

    ch_a = _make_dummy_challenge("ch_A")
    ch_b = _make_dummy_challenge("ch_B")
    _seed_challenge(db_conn, ch_a)
    _seed_challenge(db_conn, ch_b)

    _seed_submission(db_conn, "s1", learner, "ch_A")
    _seed_submission(db_conn, "s2", learner, "ch_A")
    _seed_submission(db_conn, "s3", learner, "ch_B")

    # Same challenge twice -> NOT recurring
    cur.execute(
        "INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'ch_A', 's1', 'unhandled_short_input', '2026-01-01T00:00:00Z');",
        (learner,),
    )
    cur.execute(
        "INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'ch_A', 's2', 'unhandled_short_input', '2026-01-01T00:01:00Z');",
        (learner,),
    )
    db_conn.commit()

    rec1 = get_recurring_mistakes(db_conn, learner)
    assert rec1 == []

    # Second distinct challenge -> IS recurring
    cur.execute(
        "INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'ch_B', 's3', 'unhandled_short_input', '2026-01-01T00:02:00Z');",
        (learner,),
    )
    db_conn.commit()

    rec2 = get_recurring_mistakes(db_conn, learner)
    assert len(rec2) == 1
    assert rec2[0]["tag"] == "unhandled_short_input"
    assert rec2[0]["challenges"] == ["ch_A", "ch_B"]
    assert rec2[0]["count"] == 2
    assert rec2[0]["description"] == MISTAKE_DESCRIPTIONS["unhandled_short_input"]


def test_bug_dna_aggregation_and_ordering(db_conn: sqlite3.Connection):
    """Bug DNA aggregates all mistake tags, ordered descending by frequency."""
    cur = db_conn.cursor()
    learner = "learner_dna"
    _seed_learner(db_conn, learner)

    for i in range(1, 6):
        ch_id = f"c{i}"
        ch = _make_dummy_challenge(ch_id)
        _seed_challenge(db_conn, ch)
        _seed_submission(db_conn, f"s{i}", learner, ch_id)

    cur.execute("INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'c1', 's1', 'mutates_input', '2026-01-01T00:00:00Z');", (learner,))
    cur.execute("INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'c2', 's2', 'off_by_one', '2026-01-01T00:01:00Z');", (learner,))
    cur.execute("INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'c3', 's3', 'off_by_one', '2026-01-01T00:02:00Z');", (learner,))
    cur.execute("INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'c4', 's4', 'off_by_one', '2026-01-01T00:03:00Z');", (learner,))
    cur.execute("INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'c5', 's5', 'mutates_input', '2026-01-01T00:04:00Z');", (learner,))
    db_conn.commit()

    dna = get_bug_dna(db_conn, learner)
    assert len(dna) == 2
    assert dna[0] == {"tag": "off_by_one", "count": 3}
    assert dna[1] == {"tag": "mutates_input", "count": 2}


# ==============================================================================
# Recommender Strategy Tests
# ==============================================================================

def test_recommender_strategy_1_recurring_mistake(db_conn: sqlite3.Connection):
    """Recommender Priority 1: Targets top recurring mistake in plain language."""
    cur = db_conn.cursor()
    learner = "learner_rec_1"
    _seed_learner(db_conn, learner)

    ch_last_k = _make_dummy_challenge("last-k-total")
    ch_mid_3 = _make_dummy_challenge("middle-three")
    _seed_challenge(db_conn, ch_last_k)
    _seed_challenge(db_conn, ch_mid_3)

    _seed_submission(db_conn, "s1", learner, "last-k-total")
    _seed_submission(db_conn, "s2", learner, "middle-three")

    # Setup mistakes in 2 distinct challenges
    cur.execute("INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'last-k-total', 's1', 'unhandled_short_input', '2026-01-01T00:00:00Z');", (learner,))
    cur.execute("INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'middle-three', 's2', 'unhandled_short_input', '2026-01-01T00:01:00Z');", (learner,))
    db_conn.commit()

    all_ch = [
        _make_dummy_challenge("ch1", targets_mistakes=["off_by_one"], sort_order=1),
        _make_dummy_challenge("ch_practice", targets_mistakes=["unhandled_short_input"], difficulty="easy", sort_order=2),
    ]

    rec = recommend_next_challenge(db_conn, learner, all_challenges=all_ch)
    assert rec["id"] == "ch_practice"
    assert rec["reason"] == "You made the same mistake (unhandled short input) in last-k-total and middle-three. This challenge gives you another chance to practise it."


def test_recommender_strategy_2_lowest_mastery_concept(db_conn: sqlite3.Connection):
    """Recommender Priority 2: Suggests easiest unsolved challenge for lowest mastery concept."""
    learner = "learner_rec_2"
    _seed_learner(db_conn, learner)
    cur = db_conn.cursor()

    # Set mastery: indexing is lowest at 0.35, loops is higher at 0.80
    cur.execute("INSERT INTO mastery (learner_id, concept, score, updated_at) VALUES (?, 'indexing', 0.35, '2026-01-01T00:00:00Z');", (learner,))
    cur.execute("INSERT INTO mastery (learner_id, concept, score, updated_at) VALUES (?, 'loops', 0.80, '2026-01-01T00:00:00Z');", (learner,))
    db_conn.commit()

    all_ch = [
        _make_dummy_challenge("ch_loops", concepts=["loops"], difficulty="easy", sort_order=1),
        _make_dummy_challenge("ch_idx_med", concepts=["indexing"], difficulty="medium", sort_order=2),
        _make_dummy_challenge("ch_idx_easy", concepts=["indexing"], difficulty="easy", sort_order=3),
    ]

    rec = recommend_next_challenge(db_conn, learner, all_challenges=all_ch)
    assert rec["id"] == "ch_idx_easy"  # easiest unsolved challenge teaching indexing
    assert rec["reason"] == "Recommended to help you strengthen your indexing skills (current mastery: 35%)."


def test_recommender_strategy_3_next_unsolved_by_sort_order(db_conn: sqlite3.Connection):
    """Recommender Priority 3: When masteries equal and no recurring mistake, pick by sort_order."""
    learner = "learner_rec_3"
    _seed_learner(db_conn, learner)
    ch1 = _make_dummy_challenge("ch1", sort_order=1)
    _seed_challenge(db_conn, ch1)
    _seed_submission(db_conn, "s1", learner, "ch1", all_passed=1, hints_used=0)

    all_ch = [
        _make_dummy_challenge("ch1", sort_order=1),
        _make_dummy_challenge("ch2", sort_order=2),
        _make_dummy_challenge("ch3", sort_order=3),
    ]

    rec = recommend_next_challenge(db_conn, learner, all_challenges=all_ch)
    assert rec["id"] == "ch2"
    assert rec["reason"] == "Next challenge to practice your debugging skills."


def test_recommender_strategy_4_all_solved_hardest_repeat(db_conn: sqlite3.Connection):
    """Recommender Priority 4: When all challenges solved, pick hardest to repeat without hints."""
    learner = "learner_rec_4"
    _seed_learner(db_conn, learner)

    all_ch = [
        _make_dummy_challenge("ch1", difficulty="easy", sort_order=1),
        _make_dummy_challenge("ch2", difficulty="hard", sort_order=2),
    ]

    for ch in all_ch:
        _seed_challenge(db_conn, ch)
        _seed_submission(db_conn, f"s_{ch.id}", learner, ch.id, all_passed=1, hints_used=0)

    rec = recommend_next_challenge(db_conn, learner, all_challenges=all_ch)
    assert rec["id"] == "ch2"
    assert "Try repeating Title ch2 without any hints to master it." in rec["reason"]


# ==============================================================================
# Habit Breaking Achievement Tests
# ==============================================================================

def test_habit_broken_awarded_on_clean_zero_hint_pass(db_conn: sqlite3.Connection):
    """When learner passes a challenge targeting a recurring mistake with 0 hints, award achievement."""
    learner = "learner_habit"
    _seed_learner(db_conn, learner)

    ch_a = _make_dummy_challenge("ch_A")
    ch_b = _make_dummy_challenge("ch_B")
    _seed_challenge(db_conn, ch_a)
    _seed_challenge(db_conn, ch_b)
    _seed_submission(db_conn, "s1", learner, "ch_A")
    _seed_submission(db_conn, "s2", learner, "ch_B")

    cur = db_conn.cursor()
    # 1. Establish recurring mistake
    cur.execute("INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'ch_A', 's1', 'unhandled_short_input', '2026-01-01T00:00:00Z');", (learner,))
    cur.execute("INSERT INTO mistakes (learner_id, challenge_id, submission_id, tag, created_at) VALUES (?, 'ch_B', 's2', 'unhandled_short_input', '2026-01-01T00:01:00Z');", (learner,))
    db_conn.commit()

    ch_target = _make_dummy_challenge("ch_target", targets_mistakes=["unhandled_short_input"])
    _seed_challenge(db_conn, ch_target)

    # 2. Pass with hints -> does NOT award achievement
    awarded_hints = check_and_award_bug_habit_broken(
        conn=db_conn,
        learner_id=learner,
        challenge=ch_target,
        all_passed=True,
        hints_used=1,
        mistake_tags=[],
    )
    assert awarded_hints == []

    # 3. Clean pass with zero hints -> AWARDS achievement
    awarded_clean = check_and_award_bug_habit_broken(
        conn=db_conn,
        learner_id=learner,
        challenge=ch_target,
        all_passed=True,
        hints_used=0,
        mistake_tags=[],
    )
    assert awarded_clean == ["Bug Habit Broken: Unhandled Short Input"]

    # Verify achievement exists in database
    cur.execute("SELECT key FROM achievements WHERE learner_id = ?;", (learner,))
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["key"] == "Bug Habit Broken: Unhandled Short Input"

    # 4. Repeating the clean solve does not insert duplicate achievement
    repeat_award = check_and_award_bug_habit_broken(
        conn=db_conn,
        learner_id=learner,
        challenge=ch_target,
        all_passed=True,
        hints_used=0,
        mistake_tags=[],
    )
    assert repeat_award == []


# ==============================================================================
# End-to-End API Integration Tests
# ==============================================================================

def test_mastery_and_profile_api_integration(tmp_path: Path, monkeypatch):
    """End-to-end test verifying /api/submit updates profile mastery, Bug DNA, and next recommendation."""
    test_db = tmp_path / "test_api_mastery.db"
    init_db(str(test_db))
    monkeypatch.setenv("DATABASE_PATH", str(test_db))

    docker_available = bool(shutil.which("docker"))
    if docker_available:
        try:
            res = subprocess.run(["docker", "info"], capture_output=True, timeout=2.0)
            if res.returncode != 0:
                docker_available = False
        except Exception:
            docker_available = False

    if not docker_available:
        def sandbox_bridge(code, function_name, tests, per_test_timeout=2.0, total_timeout=20.0):
            return _execute_via_runner_direct(code, function_name, tests, per_test_timeout)

        monkeypatch.setattr("backend.engine.grader.run_in_sandbox", sandbox_bridge)

    client = TestClient(app)
    learner_id = "test_learner_e2e"

    # 1. Fetch initial profile
    res1 = client.get(f"/api/learner/{learner_id}")
    assert res1.status_code == 200
    p1 = res1.json()
    assert p1["mastery"]["loops"] == 0.5000
    assert p1["bug_dna"] == []
    assert p1["recurring_mistakes"] == []
    assert p1["next_challenge"]["id"] == "last-k-total"

    # 2. Submit buggy code to last-k-total triggering unhandled_short_input
    buggy_code_1 = "def last_k_total(marks, k):\n    return sum(marks[-k:])\n"
    res2 = client.post("/api/submit", json={
        "learner_id": learner_id,
        "challenge_id": "last-k-total",
        "code": buggy_code_1,
    })
    assert res2.status_code == 200
    sub2 = res2.json()
    assert not sub2["all_passed"]
    assert "unhandled_short_input" in sub2["mistake_tags"]

    # Profile after first failure
    res3 = client.get(f"/api/learner/{learner_id}")
    p3 = res3.json()
    assert p3["bug_dna"] == [{"tag": "unhandled_short_input", "count": 1}]
    assert p3["recurring_mistakes"] == []  # Not yet recurring (only 1 challenge)

    # 3. Submit buggy code to middle-three also triggering unhandled_short_input
    buggy_code_2 = "def middle_three(items):\n    mid = len(items) // 2\n    return [items[mid - 1], items[mid], items[mid + 1]]\n"
    res4 = client.post("/api/submit", json={
        "learner_id": learner_id,
        "challenge_id": "middle-three",
        "code": buggy_code_2,
    })
    assert res4.status_code == 200

    # Profile after second distinct challenge failure -> becomes RECURRING!
    res5 = client.get(f"/api/learner/{learner_id}")
    p5 = res5.json()
    assert len(p5["recurring_mistakes"]) == 1
    assert p5["recurring_mistakes"][0]["tag"] == "unhandled_short_input"
    assert set(p5["recurring_mistakes"][0]["challenges"]) == {"last-k-total", "middle-three"}
    # Next recommendation should now use Priority 1 (remediating unhandled_short_input)
    assert "You made the same mistake (unhandled short input)" in p5["next_challenge"]["reason"]

    # 4. Submit correct clean code to safe-first-element (which targets unhandled_short_input)
    clean_code_safe = "def safe_first_element(items, default=None):\n    if not items:\n        return default\n    return items[0]\n"
    res6 = client.post("/api/submit", json={
        "learner_id": learner_id,
        "challenge_id": "safe-first-element",
        "code": clean_code_safe,
    })
    assert res6.status_code == 200
    sub6 = res6.json()
    assert sub6["all_passed"]

    # Check profile for Bug Habit Broken achievement!
    res7 = client.get(f"/api/learner/{learner_id}")
    p7 = res7.json()
    assert "Bug Habit Broken: Unhandled Short Input" in p7["achievements"]
    assert p7["stats"]["bugs_defeated"] == 1
