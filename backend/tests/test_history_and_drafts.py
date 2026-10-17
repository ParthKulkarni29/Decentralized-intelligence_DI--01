"""Unit tests for History, Submission Detail, Drafts, and Bug Arena (Phase 7 Features B, C, D, H).

Tests:
1. Bug Arena: submit response includes 'arena' with human-readable 'defeated' and 'remaining' categories.
2. History endpoint: GET /api/learner/{id}/history returns submission_id, challenge_id, counts, mistake_tags, hints_used, prediction_accuracy.
3. Submission detail: GET /api/submissions/{id} returns learner code, visible results, and outcome-only hidden results.
4. Drafts endpoint: GET returns challenge starter_code initially; PUT saves draft; subsequent GET returns saved draft.
5. Draft length limit: >20,000 chars rejected with 400 Bad Request.
6. Authentication: Protected pinned learners require valid Bearer token for history and drafts.
"""

from pathlib import Path
import json
import shutil
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import init_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"

BUGGY_LAST_K_TOTAL = """
def last_k_total(marks, k):
    return sum(marks[-k:])
"""


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


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path: Path, monkeypatch):
    """Set up isolated DB and execution bridge."""
    test_db = tmp_path / "test_history.db"
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


def test_arena_and_history_and_submission_detail():
    """Verify Arena categories, history listing, and submission detail inspection."""
    client = TestClient(app)
    learner_id = "history_tester"

    # 1. Submit buggy code with prediction
    sub_res = client.post("/api/submit", json={
        "learner_id": learner_id,
        "challenge_id": "last-k-total",
        "code": BUGGY_LAST_K_TOTAL,
        "prediction": "some_fail",
    })
    assert sub_res.status_code == 200
    sub_data = sub_res.json()
    submission_id = sub_data["submission_id"]

    # Assert Arena is populated
    arena = sub_data.get("arena")
    assert arena is not None
    assert "defeated" in arena
    assert "remaining" in arena
    assert isinstance(arena["defeated"], list)
    assert isinstance(arena["remaining"], list)
    assert "Example inputs" in arena["defeated"]
    assert len(arena["remaining"]) >= 1

    # 2. Query History endpoint
    hist_res = client.get(f"/api/learner/{learner_id}/history")
    assert hist_res.status_code == 200
    history = hist_res.json()
    assert len(history) == 1
    item = history[0]
    assert item["submission_id"] == submission_id
    assert item["challenge_id"] == "last-k-total"
    assert item["all_passed"] is False
    assert item["counts"]["passed"] >= 3
    assert item["counts"]["failed"] >= 1
    assert "unhandled_short_input" in item["mistake_tags"]
    assert item["prediction"] == "some_fail"
    assert item["prediction_accuracy"] is not None

    # 3. Query Submission Detail endpoint
    detail_res = client.get(f"/api/submissions/{submission_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["submission_id"] == submission_id
    assert detail["learner_id"] == learner_id
    assert "def last_k_total" in detail["code"]
    assert detail["all_passed"] is False
    assert len(detail["visible"]) == 3
    assert "call" in detail["visible"][0]
    assert "expected" in detail["visible"][0]
    assert "got" in detail["visible"][0]

    # SECURITY ANTI-LEAK ASSERTION: Hidden tests must NEVER have inputs or expected/got outputs
    for h in detail["hidden"]:
        assert "call" not in h
        assert "expected" not in h
        assert "got" not in h
        assert "args" not in h
        assert h["outcome"] in ("passed", "wrong_output", "runtime_error", "timeout")


def test_drafts_get_and_put_lifecycle():
    """Verify draft persistence, starter code fallback, and length validation."""
    client = TestClient(app)
    learner_id = "draft_coder"

    # 1. GET before draft saved -> returns challenge starter_code
    get1 = client.get(f"/api/drafts/{learner_id}/last-k-total")
    assert get1.status_code == 200
    data1 = get1.json()
    assert data1["learner_id"] == learner_id
    assert data1["challenge_id"] == "last-k-total"
    assert "def last_k_total(marks, k):" in data1["code"]

    # 2. PUT updated draft
    my_draft = "def last_k_total(marks, k):\n    # working on solution\n    return sum(marks)\n"
    put_res = client.put(f"/api/drafts/{learner_id}/last-k-total", json={"code": my_draft})
    assert put_res.status_code == 200
    put_data = put_res.json()
    assert put_data["code"] == my_draft

    # 3. GET after saving -> returns updated draft
    get2 = client.get(f"/api/drafts/{learner_id}/last-k-total")
    assert get2.status_code == 200
    assert get2.json()["code"] == my_draft

    # 4. Exceed maximum code length (> 20,000 chars)
    giant_code = "x = 1\n" * 4000  # > 24,000 chars
    giant_res = client.put(f"/api/drafts/{learner_id}/last-k-total", json={"code": giant_code})
    assert giant_res.status_code == 400


def test_protected_learner_history_and_draft_auth():
    """Verify PIN-protected learners require token for history and drafts."""
    client = TestClient(app)
    reg_res = client.post("/api/auth/register", json={"display_name": "Vault Master", "pin": "7777"})
    learner_id = reg_res.json()["learner_id"]
    token = reg_res.json()["token"]

    # History without token -> 401
    assert client.get(f"/api/learner/{learner_id}/history").status_code == 401
    # History with token -> 200
    assert client.get(
        f"/api/learner/{learner_id}/history",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 200

    # Draft without token -> 401
    assert client.get(f"/api/drafts/{learner_id}/last-k-total").status_code == 401
    assert client.put(f"/api/drafts/{learner_id}/last-k-total", json={"code": "pass"}).status_code == 401

    # Draft with token -> 200
    assert client.get(
        f"/api/drafts/{learner_id}/last-k-total",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 200
    assert client.put(
        f"/api/drafts/{learner_id}/last-k-total",
        json={"code": "def last_k_total(marks, k):\n    pass\n"},
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 200
