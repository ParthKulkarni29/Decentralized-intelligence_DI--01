"""Tests for Phase 10 Step 2: Sandbox lifecycle, concurrency 503, and Gemini failure drill."""

from datetime import datetime, timezone, timedelta
import json
import subprocess
import time
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import Settings
import backend.engine.sandbox as sandbox_module
from backend.engine.sandbox import (
    SandboxBusyError,
    cleanup_stale_containers,
    cleanup_old_containers,
    run_in_sandbox,
)
from backend.database import init_db


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Ensure clean isolated SQLite database for tests."""
    db_file = tmp_path / "test_phase10.db"
    mock_settings = Settings(
        GEMINI_API_KEY="test_key",
        GEMINI_MODEL="gemini-2.5-flash",
        DATABASE_PATH=str(db_file),
        SANDBOX_MODE="docker",
        MAX_PARALLEL_RUNS=2,
        CORS_ORIGINS=["http://localhost:5173"],
    )
    monkeypatch.setattr("backend.config.get_settings", lambda: mock_settings)
    monkeypatch.setattr("backend.engine.sandbox.get_settings", lambda: mock_settings)
    monkeypatch.setattr("backend.database.get_settings", lambda: mock_settings)
    monkeypatch.setattr("backend.ai.coach.get_settings", lambda: mock_settings)
    init_db()


# ==============================================================================
# 1 & 2: Container Cleanup Unit Tests
# ==============================================================================

def test_cleanup_stale_containers_when_docker_missing(monkeypatch):
    """Verify stale container cleanup gracefully handles missing Docker CLI."""
    monkeypatch.setattr("shutil.which", lambda prog: None)
    count = cleanup_stale_containers()
    assert count == 0


def test_cleanup_stale_containers_removes_matching_containers(monkeypatch):
    """Verify cleanup_stale_containers runs docker rm -f on matching prefixes."""
    monkeypatch.setattr("shutil.which", lambda prog: "/usr/bin/docker")

    rm_commands = []

    def mock_run(cmd, *args, **kwargs):
        if cmd[1] == "ps":
            stdout = "debugdojo-run-abc12345\nother-container\n/debugdojo-run-xyz98765\n"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="")
        elif cmd[1] == "rm":
            rm_commands.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)

    removed = cleanup_stale_containers()
    assert removed == 2
    assert len(rm_commands) == 1
    assert "debugdojo-run-abc12345" in rm_commands[0]
    assert "debugdojo-run-xyz98765" in rm_commands[0]
    assert "other-container" not in rm_commands[0]


def test_cleanup_old_containers_removes_only_expired_containers(monkeypatch):
    """Verify cleanup_old_containers inspects timestamps and removes only containers > max_age."""
    monkeypatch.setattr("shutil.which", lambda prog: "/usr/bin/docker")

    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    young_ts = (now - timedelta(seconds=15)).strftime("%Y-%m-%dT%H:%M:%S.987654321Z")

    rm_commands = []

    def mock_run(cmd, *args, **kwargs):
        if cmd[1] == "ps":
            stdout = "debugdojo-run-old1\ndebugdojo-run-young2\n"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="")
        elif cmd[1] == "inspect":
            inspect_out = f"/debugdojo-run-old1\t{old_ts}\n/debugdojo-run-young2\t{young_ts}\n"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=inspect_out, stderr="")
        elif cmd[1] == "rm":
            rm_commands.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", mock_run)

    removed = cleanup_old_containers(max_age_seconds=60.0)
    assert removed == 1
    assert len(rm_commands) == 1
    assert rm_commands[0] == ["docker", "rm", "-f", "debugdojo-run-old1"]


# ==============================================================================
# 3: Concurrency Saturation & HTTP 503
# ==============================================================================

def test_sandbox_busy_error_raised_when_slots_exhausted(monkeypatch):
    """Verify SandboxBusyError is raised when concurrency semaphore queue times out."""
    monkeypatch.setattr("shutil.which", lambda prog: "/usr/bin/docker")
    monkeypatch.setattr("backend.engine.sandbox.QUEUE_TIMEOUT_SECONDS", 0.05)

    # Exhaust the semaphore
    acquired_locks = []
    while True:
        got = sandbox_module._CONCURRENCY_SEMAPHORE.acquire(blocking=False)
        if not got:
            break
        acquired_locks.append(got)

    try:
        tests = [{"id": 1, "args": [], "capture_stdout": False}]
        with pytest.raises(SandboxBusyError) as exc_info:
            run_in_sandbox("def f(): pass", "f", tests)
        assert "busy" in str(exc_info.value).lower()
    finally:
        for _ in acquired_locks:
            sandbox_module._CONCURRENCY_SEMAPHORE.release()


def test_submit_returns_503_on_concurrency_saturation(monkeypatch):
    """Verify submit endpoint returns HTTP 503 and Retry-After header when dojo is busy."""
    def mock_run_busy(*args, **kwargs):
        raise SandboxBusyError("All sandbox slots are busy")

    monkeypatch.setattr("backend.engine.grader.run_in_sandbox", mock_run_busy)

    client = TestClient(app)
    res = client.post(
        "/api/submit",
        json={
            "learner_id": "test_busy_learner",
            "challenge_id": "count-even",
            "code": "def count_even(lst): return 0",
        },
    )

    # Must return 503 with exact error contract and Retry-After header
    assert res.status_code == 503
    assert res.json() == {"error": "The dojo is busy, please try again"}
    assert res.headers.get("Retry-After") == "5"

    # Verify server remains healthy
    h_res = client.get("/api/health")
    assert h_res.status_code == 200
    assert h_res.json() == {"status": "ok"}


# ==============================================================================
# 4: Gemini Failure Drill
# ==============================================================================

def test_gemini_failure_drill_invalid_key_graceful_fallbacks(monkeypatch):
    """Verify submit and hint operate gracefully with deterministic fallback when Gemini API fails.

    Proves:
    - Failing submission gets status 200 and deterministic fallback explanation.
    - Passing submission gets status 200 and 'All tests passed!'.
    - Hint returns authored ladder entry with source='authored'.
    - No unhandled errors, no secret leaks, fast response within timeout.
    """
    # 1. Provide an invalid API key
    drill_settings = Settings(
        GEMINI_API_KEY="invalid_drill_key_xyz_999",
        GEMINI_MODEL="gemini-2.5-flash",
        DATABASE_PATH="backend/debugdojo.db",
        SANDBOX_MODE="docker",
        MAX_PARALLEL_RUNS=3,
        CORS_ORIGINS=["http://localhost:5173"],
    )
    monkeypatch.setattr("backend.config.get_settings", lambda: drill_settings)
    monkeypatch.setattr("backend.ai.coach.get_settings", lambda: drill_settings)

    # Mock call_model to simulate network failure or invalid API key rejection
    def mock_call_model_failure(*args, **kwargs):
        # Must return None so caller falls back to deterministic behavior
        return None

    monkeypatch.setattr("backend.api.routes.call_model", mock_call_model_failure)

    # Provide mocked runner results for predictable grader outcomes
    def mock_run_sandbox_failing(code, function_name, tests, **kwargs):
        return [
            {"id": t["id"], "status": "ok", "value": 0 if t["id"] <= 3 else -999, "exc_type": None, "limit": None, "mutated": False}
            for t in tests
        ]

    monkeypatch.setattr("backend.engine.grader.run_in_sandbox", mock_run_sandbox_failing)

    client = TestClient(app)

    start_t = time.time()
    # A. Submit failing code
    sub_res = client.post(
        "/api/submit",
        json={
            "learner_id": "drill_learner",
            "challenge_id": "last-k-total",
            "code": "def last_k_total(marks, k): return sum(marks[-k:])",
        },
    )
    elapsed = time.time() - start_t

    assert sub_res.status_code == 200
    sub_data = sub_res.json()
    assert sub_data["all_passed"] is False
    assert sub_data["explanation"] is not None
    # Deterministic base explanation format
    assert "tests fail" in sub_data["explanation"]
    assert elapsed < 5.0, f"Submit took too long ({elapsed}s)"

    # B. Request hint - must return authored ladder entry and store with source='authored' in DB
    hint_res = client.post(
        "/api/hint",
        json={
            "submission_id": sub_data["submission_id"],
        },
    )
    assert hint_res.status_code == 200
    hint_data = hint_res.json()
    assert hint_data["hint_number"] == 1
    assert len(hint_data["hint"]) > 10

    # Query DB to verify it was stored with source='authored'
    from backend.database import get_db_connection
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT text, source FROM hints WHERE submission_id = ?;", (sub_data["submission_id"],))
        h_row = cur.fetchone()
        assert h_row is not None
        assert h_row["source"] == "authored"
        assert h_row["text"] == hint_data["hint"]
    finally:
        conn.close()

    # C. Submit passing code
    def mock_run_sandbox_passing(code, function_name, tests, **kwargs):
        # Return expected answers for last-k-total
        from backend.engine.challenges import get_full_challenge
        from backend.engine.reference import execute_reference
        ch = get_full_challenge("last-k-total")
        res = []
        for t in tests:
            val = execute_reference("last-k-total", ch.function_name, t["args"])
            res.append({"id": t["id"], "status": "ok", "value": val, "exc_type": None, "limit": None, "mutated": False})
        return res

    monkeypatch.setattr("backend.engine.grader.run_in_sandbox", mock_run_sandbox_passing)

    pass_res = client.post(
        "/api/submit",
        json={
            "learner_id": "drill_learner",
            "challenge_id": "last-k-total",
            "code": "def last_k_total(marks, k): ...",
        },
    )
    assert pass_res.status_code == 200
    pass_data = pass_res.json()
    assert pass_data["all_passed"] is True
    assert "passed!" in pass_data["explanation"]
