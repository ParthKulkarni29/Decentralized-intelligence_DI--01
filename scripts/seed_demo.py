"""Seed realistic demo data for learner 'rohan' via the live API.

Phase 10, Step 1:
- Sends REAL submissions through the API (/api/submit, /api/hint)
- Plants 1 recurring mistake: unhandled_short_input in last-k-total and middle-three
- Shows improved habits: boundary_comparison on count-passes and off_by_one on first-n-squares corrected to pass
- Generates realistic Bug DNA, mastery trajectory, and next-challenge recommendation
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"


def _execute_via_runner_direct(code: str, function_name: str, tests: list[dict], per_test_timeout: float = 2.0, **kwargs) -> list[dict]:
    """Execute runner script in isolated subprocess when Docker daemon is not active."""
    payload = json.dumps({
        "code": code,
        "function_name": function_name,
        "tests": tests,
        "per_test_timeout": per_test_timeout,
    })
    try:
        proc = subprocess.run(
            [sys.executable, "-u", str(RUNNER_SCRIPT)],
            input=payload,
            text=True,
            capture_output=True,
            timeout=25.0,
        )
    except subprocess.TimeoutExpired:
        return [
            {"id": t["id"], "status": "timeout", "value": None, "exc_type": None, "limit": None, "mutated": False}
            for t in tests
        ]
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


def get_api_client(base_url: str) -> tuple[Any, str]:
    """Connect to live API server or fallback to FastAPI TestClient."""
    from backend.database import init_db
    init_db()

    try:
        resp = httpx.get(f"{base_url}/api/health", timeout=1.0)
        if resp.status_code == 200:
            return httpx.Client(base_url=base_url, timeout=35.0), f"Live API Server at {base_url}"
    except Exception:
        pass

    if not shutil.which("docker"):
        import backend.engine.grader
        backend.engine.grader.run_in_sandbox = _execute_via_runner_direct

    from fastapi.testclient import TestClient
    from backend.main import app

    return TestClient(app), "In-process FastAPI TestClient"


def seed_rohan(client: Any) -> dict[str, Any]:
    """Execute 8 submissions across 5 challenges for learner 'rohan'."""
    learner_id = "rohan"

    demo_steps = [
        # 1. count-passes (Attempt 1: Fail with boundary_comparison)
        {
            "challenge_id": "count-passes",
            "code": "def count_passes(marks):\n    return sum(1 for m in marks if m > 40)\n",
            "hints_to_request": 2,
            "desc": "Fails threshold check with '>' instead of '>=' (boundary_comparison)",
        },
        # 2. count-passes (Attempt 2: Pass - Habit Improved!)
        {
            "challenge_id": "count-passes",
            "code": "def count_passes(marks):\n    return sum(1 for m in marks if m >= 40)\n",
            "hints_to_request": 0,
            "desc": "Corrects threshold check to '>=' (All tests pass!)",
        },
        # 3. last-k-total (Attempt 1: Fail with unhandled_short_input - Recurring Mistake #1)
        {
            "challenge_id": "last-k-total",
            "code": "def last_k_total(marks, k):\n    return sum(marks[-k:])\n",
            "hints_to_request": 1,
            "desc": "Fails empty list & k > len(marks) (unhandled_short_input #1)",
        },
        # 4. first-n-squares (Attempt 1: Fail with off_by_one)
        {
            "challenge_id": "first-n-squares",
            "code": "def first_n_squares(n):\n    return [i * i for i in range(1, n)]\n",
            "hints_to_request": 1,
            "desc": "Fails range upper boundary with range(1, n) (off_by_one)",
        },
        # 5. first-n-squares (Attempt 2: Pass - Habit Improved!)
        {
            "challenge_id": "first-n-squares",
            "code": "def first_n_squares(n):\n    return [i * i for i in range(1, n + 1)]\n",
            "hints_to_request": 0,
            "desc": "Fixes upper boundary to range(1, n + 1) (All tests pass!)",
        },
        # 6. middle-three (Attempt 1: Fail with unhandled_short_input - Recurring Mistake #2)
        {
            "challenge_id": "middle-three",
            "code": "def middle_three(items):\n    mid = len(items) // 2\n    return [items[mid - 1], items[mid], items[mid + 1]]\n",
            "hints_to_request": 1,
            "desc": "Fails short input < 3 elements (unhandled_short_input #2 -> RECURRING!)",
        },
        # 7. count-even (Attempt 1: Fail with print_not_return)
        {
            "challenge_id": "count-even",
            "code": "def count_even(numbers):\n    count = sum(1 for n in numbers if n % 2 == 0)\n    print(count)\n",
            "hints_to_request": 1,
            "desc": "Prints result instead of returning it (print_not_return)",
        },
        # 8. count-even (Attempt 2: Pass - Habit Improved!)
        {
            "challenge_id": "count-even",
            "code": "def count_even(numbers):\n    return sum(1 for n in numbers if n % 2 == 0)\n",
            "hints_to_request": 0,
            "desc": "Replaces print with return (All tests pass!)",
        },
    ]

    print("=" * 80)
    print(" SEEDING DEMO LEARNER 'rohan' THROUGH REAL API CALLS")
    print("=" * 80)

    for idx, step in enumerate(demo_steps, 1):
        ch_id = step["challenge_id"]
        code = step["code"]
        desc = step["desc"]
        hints_needed = step["hints_to_request"]

        print(f"\n[{idx}/8] Submitting to '{ch_id}'...")
        print(f"      Intent: {desc}")

        sub_resp = client.post("/api/submit", json={
            "learner_id": learner_id,
            "challenge_id": ch_id,
            "code": code,
        })

        if sub_resp.status_code != 200:
            print(f"      [ERROR] Submit failed: {sub_resp.status_code} - {sub_resp.text}")
            sys.exit(1)

        sub_data = sub_resp.json()
        sub_id = sub_data["submission_id"]
        all_passed = sub_data["all_passed"]
        tags = sub_data["mistake_tags"]

        status_str = "PASS" if all_passed else "FAIL"
        print(f"      Result: {status_str} (submission_id={sub_id})")
        if tags:
            print(f"      Mistake Tags: {tags}")

        # Request hints if planned
        for h_num in range(hints_needed):
            h_resp = client.post("/api/hint", json={"submission_id": sub_id})
            if h_resp.status_code == 200:
                h_data = h_resp.json()
                print(f"      Requested Hint #{h_data['hint_number']}: \"{h_data['text'][:60]}...\"")

    # Retrieve profile
    prof_resp = client.get(f"/api/learner/{learner_id}")
    if prof_resp.status_code != 200:
        print(f"\n[ERROR] Failed to fetch learner profile: {prof_resp.status_code}")
        sys.exit(1)

    profile = prof_resp.json()
    print("\n" + "=" * 80)
    print(" DEMO LEARNER 'rohan' PROFILE SUMMARY")
    print("=" * 80)
    print(f" Learner ID:          {profile['learner_id']}")
    print(f" Recurring Mistakes:  {[r['tag'] for r in profile['recurring_mistakes']]}")
    print(f" Bug DNA (All Tags):  {profile['bug_dna']}")
    print(" Mastery Radar:")
    for concept, score in profile["mastery"].items():
        bar = "#" * int(score * 20)
        print(f"   - {concept:12s}: {score:.2f} [{bar:<20s}]")

    next_ch = profile.get("next_challenge")
    if next_ch:
        print(f" Next Recommendation: {next_ch.get('title')} ({next_ch.get('id')})")
        print(f" Reason:              {next_ch.get('reason')}")

    print("=" * 80)
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo data for learner 'rohan'")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Base URL of API server")
    args = parser.parse_args()

    client, desc = get_api_client(args.url)
    print(f"Client: {desc}")
    seed_rohan(client)


if __name__ == "__main__":
    main()
