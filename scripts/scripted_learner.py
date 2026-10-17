"""Scripted learner benchmark for Debug Dojo Phase 5.

Plays ~11 realistic submissions over judge challenges for a fresh learner:
- Plants TWO recurring mistakes:
    1. unhandled_short_input (on last-k-total and middle-three)
    2. boundary_comparison (on count-passes and group-passing-marks)
- Plants ONE one-off slip:
    1. off_by_one (on first-n-squares)
- Submits clean correct passes for first-n-squares and count-passes.

Then queries GET /api/learner/{id} and reports:
1. Recurring mistakes detected.
2. Verification that the one-off slip is NOT listed in recurring mistakes.
3. Recommended next challenge and natural language explanation.
4. Concept mastery table.

Exits with code 1 if:
- Either planted recurring mistake is missing.
- The one-off slip is listed in recurring mistakes.
- The recommended next challenge does not target a recurring mistake.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
import httpx

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"

# ==============================================================================
# Learner Code Snippets
# ==============================================================================

# 1. last-k-total: unhandled_short_input (crashes when k > len(marks) or k <= 0)
BUGGY_LAST_K_TOTAL = """
def last_k_total(marks, k):
    return sum(marks[-k:])
"""

# 2. middle-three: unhandled_short_input (crashes when len(items) < 3)
BUGGY_MIDDLE_THREE = """
def middle_three(items):
    mid = len(items) // 2
    return [items[mid - 1], items[mid], items[mid + 1]]
"""

# 3. count-passes: boundary_comparison (uses strict > 40 instead of >= 40)
BUGGY_COUNT_PASSES = """
def count_passes(marks):
    return sum(1 for m in marks if m > 40)
"""

# 4. group-passing-marks: boundary_comparison (uses strict > 40 instead of >= 40)
BUGGY_GROUP_PASSING = """
def group_passing_marks(marks):
    p = [m for m in marks if m > 40]
    f = [m for m in marks if m <= 40]
    return {"pass": p, "fail": f}
"""

# 5. first-n-squares: off_by_one (range(1, n) misses n*n)
BUGGY_FIRST_N_SQUARES_OFF_BY_ONE = """
def first_n_squares(n):
    return [i * i for i in range(1, n)]
"""

# Correct solutions
CORRECT_LAST_K_TOTAL = """
def last_k_total(marks, k):
    if k <= 0 or not marks:
        return 0
    if k >= len(marks):
        return sum(marks)
    return sum(marks[-k:])
"""

CORRECT_COUNT_PASSES = """
def count_passes(marks):
    return sum(1 for m in marks if m >= 40)
"""

CORRECT_FIRST_N_SQUARES = """
def first_n_squares(n):
    return [i * i for i in range(1, n + 1)]
"""

CORRECT_MIDDLE_THREE = """
def middle_three(items):
    if len(items) < 3:
        return list(items)
    mid = len(items) // 2
    return items[mid - 1: mid + 2]
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


def get_client(base_url: str) -> tuple[Any, str]:
    """Connect to a running API server, or fallback to in-process TestClient if not reachable."""
    try:
        resp = httpx.get(f"{base_url}/api/health", timeout=1.0)
        if resp.status_code == 200:
            return httpx.Client(base_url=base_url, timeout=30.0), f"Live API Server at {base_url}"
    except Exception:
        pass

    # In-process TestClient fallback
    docker_available = bool(shutil.which("docker"))
    if not docker_available:
        import backend.engine.grader
        backend.engine.grader.run_in_sandbox = _execute_via_runner_direct

    from fastapi.testclient import TestClient
    from backend.main import app

    return TestClient(app), "In-process FastAPI TestClient (Server not active on port 8000)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Scripted learner runner for Debug Dojo.")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Base URL of running API")
    parser.add_argument("--learner-id", default=None, help="Learner ID to use")
    args = parser.parse_args()

    client, client_desc = get_client(args.url)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    learner_id = args.learner_id or f"scripted_{timestamp}"

    print("=" * 80)
    print(f" DEBUG DOJO - SCRIPTED LEARNER RECURRENCE & RECOMMENDATION RUN")
    print(f" Mode:       {client_desc}")
    print(f" Learner ID: {learner_id}")
    print("=" * 80)

    # Sequence of 11 realistic submissions
    submissions = [
        # Submissions 1-3: Establish unhandled_short_input as recurring (last-k-total & middle-three)
        {
            "num": 1,
            "challenge_id": "last-k-total",
            "code": BUGGY_LAST_K_TOTAL,
            "desc": "last-k-total: unhandled_short_input (fails on k > len and k <= 0)",
            "expected_tag": "unhandled_short_input",
            "expect_pass": False,
        },
        {
            "num": 2,
            "challenge_id": "last-k-total",
            "code": BUGGY_LAST_K_TOTAL,
            "desc": "last-k-total: repeated unhandled_short_input (same challenge repeat)",
            "expected_tag": "unhandled_short_input",
            "expect_pass": False,
        },
        {
            "num": 3,
            "challenge_id": "middle-three",
            "code": BUGGY_MIDDLE_THREE,
            "desc": "middle-three: unhandled_short_input -> [RECURRING #1 ESTABLISHED]",
            "expected_tag": "unhandled_short_input",
            "expect_pass": False,
        },
        # Submissions 4-6: Establish boundary_comparison as recurring (count-passes & group-passing-marks)
        {
            "num": 4,
            "challenge_id": "count-passes",
            "code": BUGGY_COUNT_PASSES,
            "desc": "count-passes: boundary_comparison (strict > 40 instead of >= 40)",
            "expected_tag": "boundary_comparison",
            "expect_pass": False,
        },
        {
            "num": 5,
            "challenge_id": "count-passes",
            "code": BUGGY_COUNT_PASSES,
            "desc": "count-passes: repeated boundary_comparison (same challenge repeat)",
            "expected_tag": "boundary_comparison",
            "expect_pass": False,
        },
        {
            "num": 6,
            "challenge_id": "group-passing-marks",
            "code": BUGGY_GROUP_PASSING,
            "desc": "group-passing-marks: boundary_comparison -> [RECURRING #2 ESTABLISHED]",
            "expected_tag": "boundary_comparison",
            "expect_pass": False,
        },
        # Submission 7: One-off slip (off_by_one on first-n-squares)
        {
            "num": 7,
            "challenge_id": "first-n-squares",
            "code": BUGGY_FIRST_N_SQUARES_OFF_BY_ONE,
            "desc": "first-n-squares: off_by_one -> [ONE-OFF SLIP]",
            "expected_tag": "off_by_one",
            "expect_pass": False,
        },
        # Submissions 8-9: Correct passes for first-n-squares and count-passes
        {
            "num": 8,
            "challenge_id": "first-n-squares",
            "code": CORRECT_FIRST_N_SQUARES,
            "desc": "first-n-squares: clean zero-hint pass -> [SOLVED]",
            "expected_tag": None,
            "expect_pass": True,
        },
        {
            "num": 9,
            "challenge_id": "count-passes",
            "code": CORRECT_COUNT_PASSES,
            "desc": "count-passes: clean zero-hint pass -> [SOLVED]",
            "expected_tag": None,
            "expect_pass": True,
        },
        # Submissions 10-11: Extra practice attempts on unhandled short input
        {
            "num": 10,
            "challenge_id": "last-k-total",
            "code": BUGGY_LAST_K_TOTAL,
            "desc": "last-k-total: retry attempt with short input bug",
            "expected_tag": "unhandled_short_input",
            "expect_pass": False,
        },
        {
            "num": 11,
            "challenge_id": "middle-three",
            "code": BUGGY_MIDDLE_THREE,
            "desc": "middle-three: retry attempt with short input bug",
            "expected_tag": "unhandled_short_input",
            "expect_pass": False,
        },
    ]

    print("\n--- [1/2] PLAYING 11 SCRIPTED SUBMISSIONS ---")
    for s in submissions:
        sub_resp = client.post("/api/submit", json={
            "learner_id": learner_id,
            "challenge_id": s["challenge_id"],
            "code": s["code"],
        })
        if sub_resp.status_code != 200:
            print(f"[FAIL] Submission #{s['num']} failed with HTTP {sub_resp.status_code}: {sub_resp.text}", file=sys.stderr)
            return 1

        data = sub_resp.json()
        sub_id = data["submission_id"]
        all_passed = data["all_passed"]
        mistake_tags = data.get("mistake_tags", [])
        status_symbol = "PASS" if all_passed else "FAIL"

        print(f"[{s['num']:02d}/11] {sub_id} | {s['challenge_id']:<20} | {status_symbol:<4} | Tags: {mistake_tags!s:<40} | {s['desc']}")

        if s["expect_pass"] != all_passed:
            print(f"[ERROR] Expected pass={s['expect_pass']} but got {all_passed} on submission #{s['num']}", file=sys.stderr)
            return 1
        if s["expected_tag"] and s["expected_tag"] not in mistake_tags:
            print(f"[ERROR] Expected tag {s['expected_tag']} not found in {mistake_tags} on submission #{s['num']}", file=sys.stderr)
            return 1

    # Query GET /api/learner/{id}
    print("\n--- [2/2] RETRIEVING LEARNER PROFILE ---")
    prof_resp = client.get(f"/api/learner/{learner_id}")
    if prof_resp.status_code != 200:
        print(f"[FAIL] GET /api/learner/{learner_id} failed with HTTP {prof_resp.status_code}: {prof_resp.text}", file=sys.stderr)
        return 1

    profile = prof_resp.json()
    recurring_mistakes = profile.get("recurring_mistakes", [])
    bug_dna = profile.get("bug_dna", [])
    mastery = profile.get("mastery", {})
    next_challenge = profile.get("next_challenge", {})
    stats = profile.get("stats", {})

    # 1. Print Recurring Mistakes
    print("\n================================================================================")
    print(" RECURRING MISTAKES (Mistakes seen across >= 2 DIFFERENT challenges)")
    print("================================================================================")
    if not recurring_mistakes:
        print("  None detected.")
    else:
        for r in recurring_mistakes:
            ch_str = ", ".join(r["challenges"])
            print(f"  * Tag:         {r['tag']}")
            print(f"    Occurrences: {r['count']} distinct challenges ({ch_str})")
            print(f"    Description: {r['description']}")
            print()

    # 2. Check One-Off Slip
    print("================================================================================")
    print(" ONE-OFF SLIP CHECK ('off_by_one')")
    print("================================================================================")
    recurring_tags = [r["tag"] for r in recurring_mistakes]
    one_off_tag = "off_by_one"
    one_off_in_dna = any(b["tag"] == one_off_tag for b in bug_dna)
    one_off_in_recurring = one_off_tag in recurring_tags

    print(f"  * One-off tag '{one_off_tag}' recorded in Bug DNA:  {one_off_in_dna}")
    print(f"  * One-off tag '{one_off_tag}' in recurring mistakes: {one_off_in_recurring}")
    if not one_off_in_recurring:
        print(f"  [PASS] Verified: One-off slip '{one_off_tag}' is correctly EXCLUDED from recurring mistakes.")
    else:
        print(f"  [FAIL] Rule violation: One-off slip '{one_off_tag}' was incorrectly marked as recurring!", file=sys.stderr)

    # 3. Print Next Challenge and Reason
    print("\n================================================================================")
    print(" RECOMMENDED NEXT CHALLENGE")
    print("================================================================================")
    next_id = next_challenge.get("id")
    next_title = next_challenge.get("title")
    next_reason = next_challenge.get("reason")
    print(f"  * ID:     {next_id}")
    print(f"  * Title:  {next_title}")
    print(f"  * Reason: \"{next_reason}\"")

    # Fetch next challenge detail to check target mistake tags
    ch_detail_resp = client.get(f"/api/challenges/{next_id}")
    if ch_detail_resp.status_code != 200:
        print(f"[FAIL] Failed to fetch challenge details for '{next_id}'", file=sys.stderr)
        return 1

    # Load FullChallenge to inspect targets_mistakes
    from backend.engine.challenges import get_full_challenge
    try:
        next_full_challenge = get_full_challenge(next_id)
        next_targets = next_full_challenge.targets_mistakes
    except Exception:
        next_targets = []

    print(f"  * Targets Mistakes: {next_targets}")

    # Check whether next challenge targets a recurring mistake
    targets_recurring_mistake = any(tag in next_targets for tag in recurring_tags)
    if targets_recurring_mistake:
        matching_tags = [tag for tag in recurring_tags if tag in next_targets]
        print(f"  [PASS] Verified: Recommended challenge '{next_id}' targets recurring mistake(s): {matching_tags}")
    else:
        print(f"  [FAIL] Recommended challenge '{next_id}' does NOT target any recurring mistake in {recurring_tags}!", file=sys.stderr)

    # 4. Print Mastery Table
    print("\n================================================================================")
    print(" CONCEPT MASTERY TABLE")
    print("================================================================================")
    print(f"  {'Concept':<15} | {'Score':<8} | {'Percent':<8} | {'Progress Bar'}")
    print("  " + "-" * 60)
    for concept, score in mastery.items():
        pct = int(score * 100)
        bar_len = int(score * 30)
        bar = "#" * bar_len + "-" * (30 - bar_len)
        print(f"  {concept:<15} | {score:<8.4f} | {pct:>6}% | [{bar}]")

    print("\n================================================================================")
    print(f" SUMMARY STATS: Solved={stats.get('bugs_defeated')}, XP={stats.get('xp')}, Streak={stats.get('independent_streak')}")
    print("================================================================================")

    # ==============================================================================
    # Rigorous Validation & Exit Code Enforcement
    # ==============================================================================
    # Condition 1: Either planted recurring mistake is missing
    required_recurring = {"unhandled_short_input", "boundary_comparison"}
    missing_recurring = required_recurring - set(recurring_tags)
    if missing_recurring:
        print(f"\n[FATAL] Missing planted recurring mistake(s): {missing_recurring}", file=sys.stderr)
        return 1

    # Condition 2: One-off slip is listed in recurring mistakes
    if one_off_in_recurring:
        print(f"\n[FATAL] One-off slip '{one_off_tag}' must not appear in recurring mistakes!", file=sys.stderr)
        return 1

    # Condition 3: Next challenge does not target a recurring mistake
    if not targets_recurring_mistake:
        print(f"\n[FATAL] Next challenge '{next_id}' does not target a recurring mistake!", file=sys.stderr)
        return 1

    print("\n>>> ALL CHECKS PASSED PERFECTLY! SCRIPT EXITED WITH CODE 0. <<<\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
