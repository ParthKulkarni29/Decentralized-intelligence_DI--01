"""AI Smoke Test Script for Debug Dojo.

Runs an end-to-end call through the AI layer:
1. Submits the buggy last-k-total snippet.
2. Retrieves and prints the generated explanation.
3. Retrieves and prints the 4 progressive personalized hints.

SECURITY GUARANTEE:
Never prints, logs, or displays the GEMINI_API_KEY or any authorization secrets.
"""

from pathlib import Path
import shutil
import subprocess
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import get_settings
from backend.main import app

RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"

BUGGY_LAST_K_TOTAL = """
def last_k_total(marks, k):
    return sum(marks[-k:])
"""


def _execute_via_runner_direct(code: str, function_name: str, tests: list[dict], per_test_timeout: float = 2.0, **kwargs) -> list[dict]:
    """Execute runner directly in isolated subprocess when Docker daemon is not active."""
    import json
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


def main() -> int:
    settings = get_settings()

    # Fallback to runner direct if Docker is not available
    docker_available = bool(shutil.which("docker"))
    if not docker_available:
        import backend.engine.grader
        backend.engine.grader.run_in_sandbox = _execute_via_runner_direct

    from fastapi.testclient import TestClient
    client = TestClient(app)

    # 1. Submit buggy code
    sub_res = client.post("/api/submit", json={
        "learner_id": "ai_smoke_runner",
        "challenge_id": "last-k-total",
        "code": BUGGY_LAST_K_TOTAL,
    })
    if sub_res.status_code != 200:
        print(f"Submission failed: {sub_res.status_code}", file=sys.stderr)
        return 1

    sub_data = sub_res.json()
    submission_id = sub_data["submission_id"]
    explanation = sub_data["explanation"]

    # 2. Print Explanation
    print("EXPLANATION:")
    print(explanation)
    print()

    # 3. Retrieve and print 4 hints
    print("HINTS:")
    for h_no in range(1, 5):
        h_res = client.post("/api/hint", json={"submission_id": submission_id})
        if h_res.status_code != 200:
            print(f"Failed to get hint #{h_no}: {h_res.text}", file=sys.stderr)
            return 1
        h_data = h_res.json()
        print(f"Hint {h_no}:")
        print(h_data["hint"])
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
