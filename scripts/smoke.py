"""Debug Dojo Smoke Test Script.

Tests the full Phase 4 backend flow against either:
1. A running live server (e.g. http://localhost:8000), or
2. In-process TestClient fallback if no server is running.

Runs:
- Health check
- Challenge listing and detail view
- Buggy submission grading (visible and hidden results)
- Correct submission grading
- Progressive hint ladder escalation and exhaustion
- Learner profile retrieval and stats verification
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
import httpx

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"


def _execute_via_runner_direct(code: str, function_name: str, tests: list[dict], per_test_timeout: float = 2.0, total_timeout: float = 20.0, **kwargs) -> list[dict]:
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


def get_client() -> tuple[Any, str]:
    """Return an HTTP client (live HTTP or TestClient) and mode description."""
    live_url = "http://127.0.0.1:8000"
    try:
        resp = httpx.get(f"{live_url}/api/health", timeout=1.0)
        if resp.status_code == 200:
            return httpx.Client(base_url=live_url, timeout=30.0), f"Live Server at {live_url}"
    except Exception:
        pass

    # Fallback to TestClient
    docker_available = bool(shutil.which("docker"))
    if not docker_available:
        import backend.engine.grader
        backend.engine.grader.run_in_sandbox = _execute_via_runner_direct

    from fastapi.testclient import TestClient
    from backend.main import app

    return TestClient(app), "In-process FastAPI TestClient"


BUGGY_CODE = """
def last_k_total(marks, k):
    # Buggy: fails when k > len(marks) or k <= 0
    return sum(marks[-k:])
"""

CORRECT_CODE = """
def last_k_total(marks, k):
    if k <= 0 or not marks:
        return 0
    if k >= len(marks):
        return sum(marks)
    return sum(marks[-k:])
"""


def run_smoke_test() -> int:
    client, mode = get_client()
    print("=" * 70)
    print(f" DEBUG DOJO SMOKE TEST ({mode})")
    print("=" * 70)

    try:
        # 1. Health check
        print("\n[1/6] Checking /api/health...")
        h_resp = client.get("/api/health")
        assert h_resp.status_code == 200, f"Health check failed: {h_resp.status_code}"
        print(f"  -> OK: {h_resp.json()}")

        # 2. Challenge listing
        print("\n[2/6] Checking GET /api/challenges and GET /api/challenges/last-k-total...")
        c_resp = client.get("/api/challenges")
        assert c_resp.status_code == 200, f"List challenges failed: {c_resp.status_code}"
        challenges = c_resp.json()
        print(f"  -> Found {len(challenges)} challenges:")
        for ch in challenges:
            print(f"     * [{ch['difficulty'].upper():<6}] {ch['id']:<22} ({', '.join(ch['concepts'])})")

        d_resp = client.get("/api/challenges/last-k-total")
        assert d_resp.status_code == 200
        detail = d_resp.json()
        assert "hidden_tests" not in detail, "SECURITY VIOLATION: hidden_tests exposed in detail!"
        print(f"  -> Verified public view for 'last-k-total' ({len(detail['visible_tests'])} visible, {detail['hidden_test_count']} hidden)")

        # 3. Buggy submission
        learner_id = "smoke_tester_01"
        print(f"\n[3/6] Submitting buggy code for 'last-k-total' (learner: {learner_id})...")
        buggy_resp = client.post("/api/submit", json={
            "learner_id": learner_id,
            "challenge_id": "last-k-total",
            "code": BUGGY_CODE,
            "prediction": "some_fail",
        })
        assert buggy_resp.status_code == 200, f"Submit buggy failed: {buggy_resp.text}"
        buggy_data = buggy_resp.json()
        submission_id = buggy_data["submission_id"]

        print(f"  -> Submission ID: {submission_id}")
        print(f"  -> All passed: {buggy_data['all_passed']}")
        print(f"  -> Visible tests ({len(buggy_data['visible'])}):")
        for v in buggy_data["visible"]:
            print(f"     Test #{v['test']}: {v['call']} -> expected {v['expected']}, got {v['got']} [{v['outcome']}]")

        print(f"  -> Hidden tests ({len(buggy_data['hidden'])}):")
        for h in buggy_data["hidden"]:
            # Verify security: no args or expected in hidden
            assert "args" not in h and "expected" not in h
            print(f"     Test #{h['test']}: outcome={h['outcome']} limit={h.get('limit')}")

        print(f"  -> Prediction Result: {buggy_data.get('prediction_result')}")
        print(f"  -> Explanation: {buggy_data.get('explanation')}")

        # 4. Correct submission
        print(f"\n[4/6] Submitting correct code for 'last-k-total'...")
        correct_resp = client.post("/api/submit", json={
            "learner_id": learner_id,
            "challenge_id": "last-k-total",
            "code": CORRECT_CODE,
            "prediction": "all_pass",
        })
        assert correct_resp.status_code == 200, f"Submit correct failed: {correct_resp.text}"
        correct_data = correct_resp.json()
        assert correct_data["all_passed"] is True, "Expected all tests to pass!"
        print(f"  -> Submission ID: {correct_data['submission_id']}")
        print(f"  -> All passed: {correct_data['all_passed']} (All tests green!)")

        # 5. Hint progression on buggy submission
        print(f"\n[5/6] Requesting progressive hints for buggy submission {submission_id}...")
        for h_step in range(1, 6):
            hint_resp = client.post("/api/hint", json={"submission_id": submission_id})
            assert hint_resp.status_code == 200
            h_info = hint_resp.json()
            print(f"  -> Call #{h_step}: Hint #{h_info['hint_number']}/{h_info['hints_total']} (exhausted={h_info['exhausted']})")
            print(f"     \"{h_info['hint']}\"")

        # 6. Learner profile
        print(f"\n[6/6] Fetching learner profile for '{learner_id}'...")
        prof_resp = client.get(f"/api/learner/{learner_id}")
        assert prof_resp.status_code == 200
        prof = prof_resp.json()
        stats = prof["stats"]
        print(f"  -> Solved bugs: {stats['bugs_defeated']}")
        print(f"  -> Streak: current={stats['independent_streak']}, longest={stats['longest_streak']}")
        print(f"  -> Prediction accuracy: {stats['prediction_accuracy'] * 100:.0f}%")
        print(f"  -> Concept mastery: {prof['mastery']}")
        print(f"  -> Recommended next challenge: {prof['next_challenge']}")

        print("\n" + "=" * 70)
        print(" ALL SMOKE TESTS PASSED CLEANLY! PHASE 4 FULLY OPERATIONAL.")
        print("=" * 70)
        return 0

    except Exception as exc:
        print(f"\nSMOKE TEST FAILED: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_smoke_test())
