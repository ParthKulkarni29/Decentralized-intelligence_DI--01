"""Table-driven test suite for the deterministic mistake taxonomy engine.

Tests all mandatory and secondary mistake tags:
1. print_not_return (at least 3 realistic buggy snippets across different challenges)
2. mutates_input (at least 3 realistic buggy snippets across different challenges)
3. unhandled_short_input (at least 3 realistic buggy snippets across different challenges)
4. boundary_comparison (at least 3 realistic buggy snippets across different challenges)
5. off_by_one (at least 3 realistic buggy snippets across different challenges)
6. 3 snippets that receive NO mandatory tag (receiving weaker/additional tags)
7. 1 snippet that is wrong in an unrelated way (receiving [] / no tag forced)
8. Fully passing solution receives []
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

from backend.engine.challenges import get_full_challenge
from backend.engine.grader import grade_submission
from backend.engine.mistakes import detect_mistakes, MISTAKE_PRIORITY

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"

MANDATORY_TAGS = {
    "print_not_return",
    "mutates_input",
    "unhandled_short_input",
    "boundary_comparison",
    "off_by_one",
}


def _execute_via_runner_direct(code: str, function_name: str, tests: list[dict], per_test_timeout: float = 2.0, total_timeout: float = 20.0, **kwargs) -> list[dict]:
    """Execute docker/runner/runner.py directly in an isolated subprocess when Docker daemon is not active."""
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
def setup_sandbox_environment(monkeypatch):
    """Bridge sandbox execution to isolated runner subprocess when Docker CLI is not installed."""
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
            return _execute_via_runner_direct(code, function_name, tests, per_test_timeout, total_timeout)

        monkeypatch.setattr("backend.engine.grader.run_in_sandbox", sandbox_bridge)


# Table of 19 test cases
MISTAKE_TEST_CASES = [
    # -------------------------------------------------------------------------
    # 1. print_not_return (3 realistic snippets across different challenges)
    # -------------------------------------------------------------------------
    (
        "print_not_return_last_k_total",
        "last-k-total",
        """
def last_k_total(marks, k):
    ans = sum(marks[-k:]) if k <= len(marks) else sum(marks)
    print(ans)
""",
        "print_not_return",
    ),
    (
        "print_not_return_count_passes",
        "count-passes",
        """
def count_passes(marks):
    count = sum(1 for m in marks if m >= 40)
    print(f"Passed count: {count}")
""",
        "print_not_return",
    ),
    (
        "print_not_return_first_n_squares",
        "first-n-squares",
        """
def first_n_squares(n):
    squares = [i * i for i in range(1, n + 1)]
    print(squares)
""",
        "print_not_return",
    ),

    # -------------------------------------------------------------------------
    # 2. mutates_input (3 realistic snippets across different challenges)
    # -------------------------------------------------------------------------
    (
        "mutates_input_remove_duplicates",
        "remove-duplicates",
        """
def remove_duplicates(items):
    unique = []
    for x in items:
        if x not in unique:
            unique.append(x)
    items.sort()
    return unique
""",
        "mutates_input",
    ),
    (
        "mutates_input_running_total",
        "running-total",
        """
def running_total(numbers):
    for i in range(1, len(numbers)):
        numbers[i] += numbers[i - 1]
    return numbers
""",
        "mutates_input",
    ),
    (
        "mutates_input_rotate_list",
        "rotate-list",
        """
def rotate_list(items, k):
    if not items:
        return []
    k = k % len(items)
    for _ in range(k):
        items.insert(0, items.pop())
    return items
""",
        "mutates_input",
    ),

    # -------------------------------------------------------------------------
    # 3. unhandled_short_input (3 realistic snippets across different challenges)
    # -------------------------------------------------------------------------
    (
        "unhandled_short_input_last_k_total",
        "last-k-total",
        """
def last_k_total(marks, k):
    if k > len(marks):
        return marks[k]  # crashes with IndexError when k > len(marks)
    return sum(marks[-k:])
""",
        "unhandled_short_input",
    ),
    (
        "unhandled_short_input_middle_three",
        "middle-three",
        """
def middle_three(items):
    mid = len(items) // 2
    return [items[mid - 1], items[mid], items[mid + 1]]
""",
        "unhandled_short_input",
    ),
    (
        "unhandled_short_input_safe_first_element",
        "safe-first-element",
        """
def safe_first_element(items, default=None):
    return items[0]
""",
        "unhandled_short_input",
    ),

    # -------------------------------------------------------------------------
    # 4. boundary_comparison (3 realistic snippets across different challenges)
    # -------------------------------------------------------------------------
    (
        "boundary_comparison_count_passes_strict",
        "count-passes",
        """
def count_passes(marks):
    return sum(1 for m in marks if m > 40)
""",
        "boundary_comparison",
    ),
    (
        "boundary_comparison_group_passing_marks",
        "group-passing-marks",
        """
def group_passing_marks(marks):
    p = [m for m in marks if m > 40]
    f = [m for m in marks if m <= 40]
    return {"pass": p, "fail": f}
""",
        "boundary_comparison",
    ),
    (
        "boundary_comparison_count_passes_loop",
        "count-passes",
        """
def count_passes(marks):
    c = 0
    for m in marks:
        if m > 40:
            c += 1
    return c
""",
        "boundary_comparison",
    ),

    # -------------------------------------------------------------------------
    # 5. off_by_one (3 realistic snippets across different challenges)
    # -------------------------------------------------------------------------
    (
        "off_by_one_first_n_squares",
        "first-n-squares",
        """
def first_n_squares(n):
    return [i * i for i in range(1, n)]
""",
        "off_by_one",
    ),
    (
        "off_by_one_count_even",
        "count-even",
        """
def count_even(numbers):
    return sum(1 for x in numbers if x % 2 == 0) + 1
""",
        "off_by_one",
    ),
    (
        "off_by_one_running_total",
        "running-total",
        """
def running_total(numbers):
    if not numbers:
        return []
    res = []
    total = 0
    for i in range(len(numbers) - 1):
        total += numbers[i]
        res.append(total)
    return res
""",
        "off_by_one",
    ),

    # -------------------------------------------------------------------------
    # 6. Snippets that should get NO mandatory tag (receive additional/weaker tags)
    # -------------------------------------------------------------------------
    (
        "no_mandatory_wrong_initialization",
        "running-total",
        """
def running_total(numbers):
    res = []
    total = 10
    for x in numbers:
        total += x
        res.append(total)
    return res
""",
        "NO_MANDATORY",
    ),
    (
        "no_mandatory_incorrect_condition",
        "count-even",
        """
def count_even(numbers):
    return sum(1 for x in numbers if x % 2 == 1)
""",
        "NO_MANDATORY",
    ),
    (
        "no_mandatory_wrong_return_type",
        "count-passes",
        """
def count_passes(marks):
    return [m for m in marks if m >= 40]
""",
        "NO_MANDATORY",
    ),

    # -------------------------------------------------------------------------
    # 7. Snippet that is simply wrong in an unrelated way (gets [] / no tag forced)
    # -------------------------------------------------------------------------
    (
        "unrelated_arbitrary_constant",
        "last-k-total",
        """
def last_k_total(marks, k):
    return 999999
""",
        "UNRELATED",
    ),
]


@pytest.mark.parametrize("test_name, challenge_id, code, expected", MISTAKE_TEST_CASES)
def test_mistake_taxonomy_detection(test_name: str, challenge_id: str, code: str, expected: str):
    """Run buggy snippet through real sandbox and grader, asserting deterministic mistake tags."""
    challenge = get_full_challenge(challenge_id)
    result = grade_submission(challenge, code)

    assert result["all_passed"] is False, f"Buggy code {test_name} unexpectedly passed all tests!"
    tags = detect_mistakes(challenge, code, result)

    assert len(tags) <= 2, f"Returned more than 2 tags: {tags}"

    if expected in MANDATORY_TAGS:
        assert expected in tags, f"Expected mandatory tag '{expected}' in {tags} for {test_name}"
    elif expected == "NO_MANDATORY":
        # None of the 5 mandatory tags should be present
        found_mandatory = MANDATORY_TAGS.intersection(set(tags))
        assert not found_mandatory, f"Expected NO mandatory tag, but found {found_mandatory} in {tags} for {test_name}"
        assert len(tags) >= 1, f"Expected a secondary tag for {test_name}, got empty list"
    elif expected == "UNRELATED":
        assert tags == [], f"Expected empty tags [] for unrelated slip {test_name}, got {tags}"


def test_fully_passing_solution_gets_no_tags():
    """Verify that a fully correct submission receives an empty mistake tag list."""
    challenge = get_full_challenge("last-k-total")
    code = """
def last_k_total(marks, k):
    if k <= 0 or not marks:
        return 0
    if k >= len(marks):
        return sum(marks)
    return sum(marks[-k:])
"""
    result = grade_submission(challenge, code)
    assert result["all_passed"] is True
    tags = detect_mistakes(challenge, code, result)
    assert tags == []


def test_priority_ordering_enforced():
    """Verify that higher priority tags always appear before lower priority tags."""
    challenge = get_full_challenge("last-k-total")
    # Code prints output AND mutates marks
    code = """
def last_k_total(marks, k):
    marks.clear()
    total = 0
    print(total)
"""
    result = grade_submission(challenge, code)
    tags = detect_mistakes(challenge, code, result)

    assert len(tags) <= 2
    if len(tags) == 2:
        idx0 = MISTAKE_PRIORITY.index(tags[0])
        idx1 = MISTAKE_PRIORITY.index(tags[1])
        assert idx0 < idx1, f"Priority order violated: {tags[0]} came before {tags[1]}"
