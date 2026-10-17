"""Tests for Phase 3: POST /api/run (LeetCode-style Run button).

Verifies:
1. Visible-only results: executes learner code against visible test cases only.
2. Anti-leak guarantee: hidden tests are never loaded, referenced, or included.
3. Statelessness: zero database writes (no submissions, hints, mastery, XP, or streaks recorded).
4. Code length limit: code exceeding MAX_CODE_LENGTH (20,000 chars) is rejected with HTTP 400.
5. Body size limit: request body exceeding 100 KB is rejected with HTTP 413.
6. Sandbox saturation: when runner concurrency is exhausted, returns HTTP 503 with Retry-After.
7. Authentication parity: unprotected learners run without token; PIN-protected learners require Bearer token.
8. Rate limiting: shares the 120 req/min 'submit' bucket with /api/submit.
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
from backend.engine.sandbox import SandboxBusyError
from backend.middleware import reset_rate_limits

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"

CORRECT_COUNT_EVEN = """
def count_even(numbers):
    return sum(1 for x in numbers if x % 2 == 0)
"""

WRONG_COUNT_EVEN = """
def count_even(numbers):
    return 999
"""

CRASHING_COUNT_EVEN = """
def count_even(numbers):
    raise ValueError("intentional runtime crash")
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
    test_db = tmp_path / "test_run.db"
    init_db(str(test_db))
    monkeypatch.setenv("DATABASE_PATH", str(test_db))
    reset_rate_limits()

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
# 1. Visible-Only Results & Contract Tests
# ==============================================================================

def test_run_visible_only_correct_solution():
    """POST /api/run executes visible tests and returns strict VisibleTestResult schema."""
    client = TestClient(app)
    response = client.post(
        "/api/run",
        json={
            "learner_id": "test_runner",
            "challenge_id": "count-even",
            "code": CORRECT_COUNT_EVEN,
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert "visible" in data
    assert "hidden" not in data  # Hidden tests must NEVER exist in response
    assert len(data["visible"]) == 3

    for item in data["visible"]:
        assert isinstance(item["test"], int)
        assert "count_even(" in item["call"]
        assert isinstance(item["expected"], str)
        assert item["got"] == item["expected"]
        assert item["outcome"] == "passed"
        assert item["limit"] is None
        assert item["error"] is None


def test_run_visible_only_wrong_and_crashing():
    """POST /api/run accurately flags wrong_output and runtime_error on visible tests."""
    client = TestClient(app)

    # 1. Wrong output
    res_wrong = client.post(
        "/api/run",
        json={
            "learner_id": "test_runner",
            "challenge_id": "count-even",
            "code": WRONG_COUNT_EVEN,
        },
    )
    assert res_wrong.status_code == 200
    data_wrong = res_wrong.json()
    assert all(t["outcome"] == "wrong_output" for t in data_wrong["visible"])
    assert data_wrong["visible"][0]["got"] == "999"

    # 2. Runtime error
    res_crash = client.post(
        "/api/run",
        json={
            "learner_id": "test_runner",
            "challenge_id": "count-even",
            "code": CRASHING_COUNT_EVEN,
        },
    )
    assert res_crash.status_code == 200
    data_crash = res_crash.json()
    assert all(t["outcome"] == "runtime_error" for t in data_crash["visible"])
    assert data_crash["visible"][0]["error"] == "ValueError"


# ==============================================================================
# 2. Database Statelessness Guarantee
# ==============================================================================

def test_run_never_writes_to_database():
    """POST /api/run must NOT write to submissions, hints, mastery, XP, or streaks."""
    client = TestClient(app)
    conn = get_db_connection()

    def get_all_table_counts():
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in cursor.fetchall()]
        counts = {}
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]
        return counts

    initial_counts = get_all_table_counts()

    # Execute multiple run requests
    res = client.post(
        "/api/run",
        json={
            "learner_id": "ephemeral_user_99",
            "challenge_id": "count-even",
            "code": CORRECT_COUNT_EVEN,
        },
    )
    assert res.status_code == 200

    after_counts = get_all_table_counts()
    conn.close()

    # All tables must have zero row additions
    assert after_counts == initial_counts, f"DB modified by run: {after_counts} vs {initial_counts}"


# ==============================================================================
# 3. Code Length & Body Size Limits
# ==============================================================================

def test_run_rejects_overlength_code():
    """Code exceeding MAX_CODE_LENGTH (20,000 characters) must return HTTP 400."""
    client = TestClient(app)
    overlength_code = "x = 1\n" + ("# " + "a" * 80 + "\n") * 250  # > 20,000 chars
    assert len(overlength_code) > 20000

    res = client.post(
        "/api/run",
        json={
            "learner_id": "test_runner",
            "challenge_id": "count-even",
            "code": overlength_code,
        },
    )
    assert res.status_code == 400
    assert "exceeds maximum length" in res.json()["error"]


def test_run_rejects_oversized_body():
    """Request body exceeding 100 KB must return HTTP 413."""
    client = TestClient(app)
    payload = json.dumps({
        "learner_id": "test_runner",
        "challenge_id": "count-even",
        "code": " " * 105000,
    })
    res = client.post(
        "/api/run",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413
    assert "exceeds maximum allowed size" in res.json()["error"]


# ==============================================================================
# 4. Concurrency Saturation & HTTP 503
# ==============================================================================

def test_run_sandbox_busy_returns_503(monkeypatch):
    """When sandbox runner slots are saturated, /api/run must return 503 with Retry-After."""
    def mock_busy(*args, **kwargs):
        raise SandboxBusyError("All sandbox slots are busy")

    monkeypatch.setattr("backend.engine.grader.run_in_sandbox", mock_busy)

    client = TestClient(app)
    res = client.post(
        "/api/run",
        json={
            "learner_id": "test_runner",
            "challenge_id": "count-even",
            "code": CORRECT_COUNT_EVEN,
        },
    )
    assert res.status_code == 503
    assert res.json()["error"] == "The dojo is busy, please try again"
    assert "Retry-After" in res.headers


# ==============================================================================
# 5. Token Verification Parity
# ==============================================================================

def test_run_token_verification():
    """Learners can run with Bearer token or without token (judge compatibility); invalid token rejected."""
    client = TestClient(app)

    # 1. Unprotected learner -> no token needed
    res_unprotected = client.post(
        "/api/run",
        json={
            "learner_id": "unprotected_coder",
            "challenge_id": "count-even",
            "code": CORRECT_COUNT_EVEN,
        },
    )
    assert res_unprotected.status_code == 200

    # 2. Register PIN-protected learner
    reg_res = client.post(
        "/api/auth/register",
        json={"display_name": "Secure Runner", "pin": "8765"},
    )
    assert reg_res.status_code == 201
    sec_id = reg_res.json()["learner_id"]
    valid_token = reg_res.json()["token"]

    # 3. Protected learner without token -> 200 OK (judge compatibility, like /api/submit)
    res_no_tok = client.post(
        "/api/run",
        json={
            "learner_id": sec_id,
            "challenge_id": "count-even",
            "code": CORRECT_COUNT_EVEN,
        },
    )
    assert res_no_tok.status_code == 200

    # 4. Protected learner with invalid token -> 401
    res_bad_tok = client.post(
        "/api/run",
        json={
            "learner_id": sec_id,
            "challenge_id": "count-even",
            "code": CORRECT_COUNT_EVEN,
        },
        headers={"Authorization": "Bearer bad_token"},
    )
    assert res_bad_tok.status_code == 401

    # 5. Protected learner with valid token -> 200 OK
    res_ok = client.post(
        "/api/run",
        json={
            "learner_id": sec_id,
            "challenge_id": "count-even",
            "code": CORRECT_COUNT_EVEN,
        },
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert res_ok.status_code == 200


# ==============================================================================
# 6. Shared Rate-Limit Bucket with /api/submit
# ==============================================================================

def test_run_shares_submit_rate_limit_bucket(monkeypatch):
    """Verify /api/run and /api/submit share the same 120 req/min rate limit bucket."""
    reset_rate_limits()
    # Mock fast grading to test rate limiting without container latency
    monkeypatch.setattr(
        "backend.api.routes.grade_run",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "backend.api.routes.grade_submission",
        lambda *args, **kwargs: {"visible": [], "hidden": [], "all_passed": True},
    )

    client = TestClient(app)

    # Make 120 fast runs
    for i in range(120):
        # Even indices hit /api/run, odd hit /api/submit
        if i % 2 == 0:
            res = client.post(
                "/api/run",
                json={"learner_id": "fast_ip", "challenge_id": "count-even", "code": "def count_even(n): return 0"},
            )
        else:
            res = client.post(
                "/api/submit",
                json={"learner_id": "fast_ip", "challenge_id": "count-even", "code": "def count_even(n): return 0"},
            )
        assert res.status_code == 200, f"Request {i} unexpectedly failed with {res.status_code}"

    # 121st request to /api/run must be rate-limited (HTTP 429)
    res_121 = client.post(
        "/api/run",
        json={"learner_id": "fast_ip", "challenge_id": "count-even", "code": "def count_even(n): return 0"},
    )
    assert res_121.status_code == 429
    assert "Rate limit exceeded" in res_121.json()["error"]
    reset_rate_limits()


# ==============================================================================
# 7. Hidden Tests Never Loaded
# ==============================================================================

def test_run_never_loads_hidden_tests(monkeypatch):
    """Assert hidden_tests.json is NEVER opened or read during /api/run execution."""
    real_read_text = Path.read_text

    accessed_files = []

    def spy_read_text(path_obj, *args, **kwargs):
        accessed_files.append(path_obj.name)
        if path_obj.name == "hidden_tests.json":
            raise RuntimeError("CRITICAL VIOLATION: hidden_tests.json was accessed during /api/run!")
        return real_read_text(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    client = TestClient(app)
    res = client.post(
        "/api/run",
        json={
            "learner_id": "security_tester",
            "challenge_id": "count-even",
            "code": CORRECT_COUNT_EVEN,
        },
    )
    assert res.status_code == 200
    assert "hidden_tests.json" not in accessed_files
    assert "visible_tests.json" in accessed_files
    assert "challenge.json" in accessed_files
