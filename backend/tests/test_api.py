"""End-to-end tests for the Phase 4 API endpoints.

Tests:
- GET /api/challenges
- GET /api/challenges/{id}
- POST /api/submit (validation, execution, database persistence, anti-leak guarantee)
- POST /api/hint (progressive escalation, exhaustion, solved submissions)
- GET /api/learner/{learner_id} (profile stats, mastery, streak tracking)
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import get_db_connection, init_db
from backend.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"

BUGGY_LAST_K_TOTAL = """
def last_k_total(marks, k):
    # Buggy: fails when k > len(marks) or k <= 0
    return sum(marks[-k:])
"""

CORRECT_LAST_K_TOTAL = """
def last_k_total(marks, k):
    if k <= 0 or not marks:
        return 0
    if k >= len(marks):
        return sum(marks)
    return sum(marks[-k:])
"""


def _execute_via_runner_direct(code: str, function_name: str, tests: list[dict], per_test_timeout: float = 2.0) -> list[dict]:
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


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path: Path, monkeypatch):
    """Set up isolated SQLite DB and sandbox execution bridge for every test."""
    test_db = tmp_path / "test_api.db"
    init_db(str(test_db))
    monkeypatch.setenv("DATABASE_PATH", str(test_db))

    # Docker bridge fallback
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


# ==============================================================================
# Challenge Endpoints Tests
# ==============================================================================

def test_get_challenges():
    """Verify GET /api/challenges lists all 10 challenges with required summary fields."""
    client = TestClient(app)
    response = client.get("/api/challenges")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 10

    for item in data:
        assert "id" in item
        assert "title" in item
        assert item["difficulty"] in ("easy", "medium", "hard")
        assert isinstance(item["concepts"], list)
        assert item["status"] in ("new", "attempted", "solved")


def test_get_challenge_detail_public_view():
    """Verify GET /api/challenges/{id} returns details without exposing hidden tests or hints."""
    client = TestClient(app)
    response = client.get("/api/challenges/last-k-total")
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == "last-k-total"
    assert data["title"] == "Last K Total"
    assert data["difficulty"] == "easy"
    assert "description" in data
    assert "starter_code" in data
    assert "arg_names" in data
    assert data["arg_names"] == ["marks", "k"]
    assert len(data["visible_tests"]) >= 3
    assert data["hidden_test_count"] >= 6

    # SECURITY ASSERTIONS
    assert "hidden_tests" not in data
    assert "hints" not in data
    assert "reference" not in data
    assert "reference_code" not in data


def test_get_challenge_detail_404():
    """Verify GET /api/challenges/{id} returns 404 for unknown challenge."""
    client = TestClient(app)
    response = client.get("/api/challenges/non-existent-challenge-id")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data


# ==============================================================================
# Submission Endpoint Tests
# ==============================================================================

def test_submit_validation_errors():
    """Verify POST /api/submit input validation."""
    client = TestClient(app)

    # 1. Invalid learner_id format (spaces, special symbols, length)
    bad_learners = ["rohan kumar", "bad@learner", "", "a" * 41]
    for bad_id in bad_learners:
        resp = client.post("/api/submit", json={
            "learner_id": bad_id,
            "challenge_id": "last-k-total",
            "code": "def last_k_total(marks, k): return 0",
        })
        assert resp.status_code == 400
        assert "error" in resp.json()

    # 2. Non-existent challenge
    resp = client.post("/api/submit", json={
        "learner_id": "rohan",
        "challenge_id": "non-existent-challenge",
        "code": "def last_k_total(marks, k): return 0",
    })
    assert resp.status_code == 404
    assert "error" in resp.json()

    # 3. Code length exceeds 20,000 characters
    oversized_code = "def last_k_total(marks, k):\n" + "    # filler\n" * 2000
    resp = client.post("/api/submit", json={
        "learner_id": "rohan",
        "challenge_id": "last-k-total",
        "code": oversized_code,
    })
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_submit_auto_creates_learner():
    """Verify submitting with an unregistered learner_id succeeds without auth and creates learner row."""
    client = TestClient(app)
    new_learner = "fresh_judge_user_99"

    resp = client.post("/api/submit", json={
        "learner_id": new_learner,
        "challenge_id": "last-k-total",
        "code": CORRECT_LAST_K_TOTAL,
    })
    assert resp.status_code == 200

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, display_name FROM learners WHERE id = ?;", (new_learner,))
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row["id"] == new_learner


def test_submit_buggy_solution():
    """Verify POST /api/submit for buggy code returns proper visible/hidden outcomes and all_passed=False."""
    client = TestClient(app)
    resp = client.post("/api/submit", json={
        "learner_id": "rohan",
        "challenge_id": "last-k-total",
        "code": BUGGY_LAST_K_TOTAL,
        "prediction": "some_fail",
    })
    assert resp.status_code == 200
    data = resp.json()

    assert data["submission_id"].startswith("s_")
    assert data["learner_id"] == "rohan"
    assert data["challenge_id"] == "last-k-total"
    assert data["all_passed"] is False
    assert len(data["visible"]) >= 3
    assert len(data["hidden"]) >= 6
    assert data["hints_available"] == 3
    assert "explanation" in data
    assert isinstance(data["mistake_tags"], list)

    # Check prediction result
    assert data["prediction_result"] is not None
    assert data["prediction_result"]["predicted"] == "some_fail"
    assert data["prediction_result"]["correct"] is True

    # Hidden tests must strictly contain only test, outcome, and limit
    for h in data["hidden"]:
        assert set(h.keys()).issubset({"test", "outcome", "limit"})
        assert h["outcome"] in ("passed", "wrong_output", "runtime_error", "timeout")


def test_submit_correct_solution():
    """Verify POST /api/submit for correct code passes all tests and flags all_passed=True."""
    client = TestClient(app)
    resp = client.post("/api/submit", json={
        "learner_id": "rohan",
        "challenge_id": "last-k-total",
        "code": CORRECT_LAST_K_TOTAL,
        "prediction": "all_pass",
    })
    assert resp.status_code == 200
    data = resp.json()

    assert data["all_passed"] is True
    assert all(v["outcome"] == "passed" for v in data["visible"])
    assert all(h["outcome"] == "passed" for h in data["hidden"])
    assert data["prediction_result"]["correct"] is True
    assert "passed" in data["explanation"].lower()


def test_anti_leak_guarantee():
    """Verify hidden test inputs, expected values, and internal categories never appear in response."""
    hidden_file = PROJECT_ROOT / "backend" / "challenges" / "last-k-total" / "hidden_tests.json"
    hidden_tests = json.loads(hidden_file.read_text(encoding="utf-8"))

    client = TestClient(app)
    resp = client.post("/api/submit", json={
        "learner_id": "anti_leak_tester",
        "challenge_id": "last-k-total",
        "code": BUGGY_LAST_K_TOTAL,
    })
    assert resp.status_code == 200
    data = resp.json()

    # Hidden array must NOT include args, expected, or got
    for h in data["hidden"]:
        assert "args" not in h
        assert "expected" not in h
        assert "got" not in h
        assert "category" not in h

    # Check database test_results table for hidden entries
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM test_results WHERE submission_id = ? AND kind = 'hidden';",
        (data["submission_id"],),
    )
    rows = cur.fetchall()
    conn.close()

    for r in rows:
        assert "args" not in r.keys()
        assert "expected" not in r.keys()
        assert "got" not in r.keys()


# ==============================================================================
# Hint Endpoint Tests
# ==============================================================================

def test_hint_escalation_and_exhaustion():
    """Verify requesting hints returns 1..4 in order, followed by exhaustion."""
    client = TestClient(app)

    # 1. Create a buggy submission
    sub_resp = client.post("/api/submit", json={
        "learner_id": "hint_seeker",
        "challenge_id": "last-k-total",
        "code": BUGGY_LAST_K_TOTAL,
    })
    submission_id = sub_resp.json()["submission_id"]

    # Load expected authored hints (tag-specific ladder matching mistake tag, or generic fallback)
    hints_file = PROJECT_ROOT / "backend" / "challenges" / "last-k-total" / "hints.json"
    hints_data = json.loads(hints_file.read_text(encoding="utf-8"))
    mistake_tags = sub_resp.json().get("mistake_tags", [])
    expected_ladder = hints_data.get(mistake_tags[0]) if mistake_tags and mistake_tags[0] in hints_data else hints_data["generic"]

    # Request hints 1 through 3
    for step in range(1, 4):
        h_resp = client.post("/api/hint", json={
            "submission_id": submission_id,
            "learner_id": "hint_seeker",
        })
        assert h_resp.status_code == 200
        h_data = h_resp.json()
        assert h_data["hint_number"] == step
        assert h_data["hint"] == expected_ladder[step - 1]
        assert h_data["text"] == expected_ladder[step - 1]
        assert h_data["hints_total"] == 3
        assert h_data["hints_remaining"] == (3 - step)
        if step < 3:
            assert h_data["exhausted"] is False
        else:
            assert h_data["exhausted"] is True

    # 4th request: exhausted ladder repeats last hint per contract and sets exhausted: True
    exhausted_resp = client.post("/api/hint", json={
        "submission_id": submission_id,
    })
    assert exhausted_resp.status_code == 200
    ex_data = exhausted_resp.json()
    assert ex_data["hint_number"] == 3
    assert ex_data["hint"] == expected_ladder[-1]
    assert ex_data["text"] == "No more hints available for this attempt."
    assert ex_data["hints_remaining"] == 0
    assert ex_data["exhausted"] is True


def test_hint_for_all_passed_submission():
    """Verify requesting a hint for an already-passed submission returns no hint needed."""
    client = TestClient(app)
    sub_resp = client.post("/api/submit", json={
        "learner_id": "master_coder",
        "challenge_id": "last-k-total",
        "code": CORRECT_LAST_K_TOTAL,
    })
    submission_id = sub_resp.json()["submission_id"]

    h_resp = client.post("/api/hint", json={"submission_id": submission_id})
    assert h_resp.status_code == 200
    h_data = h_resp.json()

    assert h_data["hint_number"] == 0
    assert "no hint needed" in h_data["hint"].lower()
    assert h_data["exhausted"] is True


def test_hint_submission_not_found():
    """Verify requesting a hint for an unknown submission_id returns 404."""
    client = TestClient(app)
    resp = client.post("/api/hint", json={"submission_id": "s_99999"})
    assert resp.status_code == 404
    assert "error" in resp.json()


# ==============================================================================
# Learner Profile Endpoint Tests
# ==============================================================================

def test_get_learner_profile():
    """Verify GET /api/learner/{learner_id} computes stats, streaks, mastery, and next challenge."""
    client = TestClient(app)
    learner_id = "streak_tester"

    # Step 1: Submit correct solution (first solve, no hints)
    client.post("/api/submit", json={
        "learner_id": learner_id,
        "challenge_id": "last-k-total",
        "code": CORRECT_LAST_K_TOTAL,
        "prediction": "all_pass",
    })

    # Step 2: Query profile
    p_resp = client.get(f"/api/learner/{learner_id}")
    assert p_resp.status_code == 200
    profile = p_resp.json()

    assert profile["learner_id"] == learner_id
    stats = profile["stats"]
    assert stats["bugs_defeated"] == 1
    assert stats["independent_streak"] == 1
    assert stats["longest_streak"] == 1
    assert stats["prediction_accuracy"] == 1.0

    # Mastery checks
    mastery = profile["mastery"]
    expected_concepts = {"loops", "lists", "indexing", "conditions", "edge_cases", "functions"}
    assert set(mastery.keys()) == expected_concepts
    for val in mastery.values():
        assert 0.0 <= val <= 1.0

    # Next challenge recommendation
    next_ch = profile["next_challenge"]
    assert next_ch is not None
    assert next_ch["id"] != "last-k-total"  # Solved challenge is not recommended
    assert "title" in next_ch
    assert "reason" in next_ch
