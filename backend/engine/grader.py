"""Host-side submission grader and test outcome evaluator.

Executes tests inside the container runner without exposing expected values,
then deterministically grades return values on the host.
"""

import json
from typing import Any, Optional
from backend.engine.challenges import FullChallenge, format_call, format_expected
from backend.engine.sandbox import run_in_sandbox

VALID_OUTCOMES = {"passed", "wrong_output", "runtime_error", "timeout"}
VALID_LIMITS = {"memory", "output", "disk"}


def strict_value_equals(actual: Any, expected: Any) -> bool:
    """Compare learner return value to expected value on the host.

    TYPE RULES:
    1. Booleans are NOT equal to integers: True != 1 and False != 0.
       In Python, bool is a subclass of int (isinstance(True, int) is True).
       We explicitly enforce type identity for booleans.
    2. Floats and ints: standard numeric equality is allowed (e.g. 5.0 == 5)
       only if neither operand is a boolean.
    3. Lists: compared recursively by element index and length.
    4. Dictionaries: compared by key set and recursive value matching.
    5. JSON round-trip: Ensures types match the JSON serialization spec.
    """
    # Reject boolean vs non-boolean numeric equality (e.g. True == 1 or False == 0)
    if isinstance(actual, bool) or isinstance(expected, bool):
        if type(actual) is not type(expected):
            return False
        return bool(actual) == bool(expected)

    # Recursive list comparison
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            return False
        return all(strict_value_equals(a, e) for a, e in zip(actual, expected))

    # Recursive dict comparison
    if isinstance(actual, dict) and isinstance(expected, dict):
        if set(actual.keys()) != set(expected.keys()):
            return False
        return all(strict_value_equals(actual[k], expected[k]) for k in actual)

    # Standard JSON-compatible equality
    try:
        # JSON round-trip to normalize tuples vs lists, etc.
        a_norm = json.loads(json.dumps(actual))
        e_norm = json.loads(json.dumps(expected))
        if isinstance(a_norm, bool) or isinstance(e_norm, bool):
            if type(a_norm) is not type(e_norm):
                return False
        return a_norm == e_norm
    except Exception:
        return actual == expected


def _detect_disk_limit(exc_type: Optional[str], limit_val: Optional[str]) -> Optional[str]:
    """Identify disk flood or file size limit violations where detectable.

    WHY:
    - POSIX SIGXFSZ or OSError with ENOSPC indicates write attempts exceeding sandbox disk/tmpfs.
    - If limit was flagged as 'output' or 'disk', map to 'disk' when related to file systems.
    """
    if limit_val == "disk":
        return "disk"
    if exc_type in ("OSError", "IOError") and limit_val in ("disk", "output"):
        return "disk"
    if limit_val in VALID_LIMITS:
        return limit_val
    return None


def grade_visible_test_cases(
    function_name: str,
    visible_tests: list[dict[str, Any]],
    result_by_id: dict[int, Any],
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Grade visible tests against sandbox runner outputs.

    Returns (graded_visible, all_visible_passed, any_mutated).
    """
    graded_visible = []
    all_passed = True
    any_mutated = False

    for idx, test in enumerate(visible_tests):
        t_id = idx + 1
        raw = result_by_id.get(t_id, {})
        status = raw.get("status", "error")
        value = raw.get("value")
        exc_type = raw.get("exc_type")
        raw_limit = raw.get("limit")
        mutated = bool(raw.get("mutated", False))
        if mutated:
            any_mutated = True

        limit = _detect_disk_limit(exc_type, raw_limit)

        if status == "timeout":
            outcome = "timeout"
            all_passed = False
        elif status == "error":
            outcome = "runtime_error"
            all_passed = False
        elif status == "ok":
            if mutated:
                outcome = "wrong_output"
                all_passed = False
            elif strict_value_equals(value, test["expected"]):
                outcome = "passed"
            else:
                outcome = "wrong_output"
                all_passed = False
        else:
            outcome = "runtime_error"
            all_passed = False

        # Format got string, truncated to 200 characters
        got_str = None
        if status == "ok":
            got_str = format_expected(value)[:200]

        graded_visible.append({
            "test": t_id,
            "call": format_call(function_name, test["args"]),
            "expected": format_expected(test["expected"]),
            "got": got_str,
            "outcome": outcome,
            "limit": limit,
            "error": exc_type if outcome == "runtime_error" else None,
            "stdout": raw.get("stdout"),
            "category": test.get("category"),
            "raw_value": value,
            "expected_value": test["expected"],
            "mutated": mutated,
            "args": test["args"],
        })

    return graded_visible, all_passed, any_mutated


def grade_run(function_name: str, visible_tests: list[dict[str, Any]], code: str) -> list[dict[str, Any]]:
    """Execute and grade learner code against VISIBLE tests only in Docker sandbox.

    Guarantees:
    - Only visible test inputs enter the sandbox runner.
    - Reuses the identical sandbox execution and strict value evaluation.
    - Hidden tests are never referenced, loaded, or evaluated.
    """
    runner_tests = [
        {
            "id": idx + 1,
            "args": test["args"],
            "capture_stdout": True,
        }
        for idx, test in enumerate(visible_tests)
    ]

    sandbox_results = run_in_sandbox(
        code=code,
        function_name=function_name,
        tests=runner_tests,
        per_test_timeout=2.0,
        total_timeout=20.0,
    )

    result_by_id = {r["id"]: r for r in sandbox_results}
    graded_visible, _, _ = grade_visible_test_cases(
        function_name=function_name,
        visible_tests=visible_tests,
        result_by_id=result_by_id,
    )

    # Return clean visible results matching VisibleTestResult schema
    clean_visible = []
    for v in graded_visible:
        clean_visible.append({
            "test": v["test"],
            "call": v["call"],
            "expected": v["expected"],
            "got": v["got"],
            "outcome": v["outcome"],
            "limit": v.get("limit"),
            "error": v.get("error"),
        })

    return clean_visible


def grade_submission(challenge: FullChallenge, code: str) -> dict[str, Any]:
    """Grade learner code against visible and hidden challenge tests.

    Hidden test inputs are provided to the runner, but expected values
    are kept exclusively on the host.
    """
    # 1. Assemble single batch test payload for the runner
    runner_tests = []
    # Visible tests (capture_stdout: True)
    for idx, test in enumerate(challenge.visible_tests):
        runner_tests.append({
            "id": idx + 1,
            "args": test["args"],
            "capture_stdout": True,
        })

    # Hidden tests (capture_stdout: False)
    hidden_offset = len(challenge.visible_tests)
    for idx, test in enumerate(challenge.hidden_tests):
        runner_tests.append({
            "id": hidden_offset + idx + 1,
            "args": test["args"],
            "capture_stdout": False,
        })

    # 2. Execute all tests in ONE container call
    sandbox_results = run_in_sandbox(
        code=code,
        function_name=challenge.function_name,
        tests=runner_tests,
        per_test_timeout=2.0,
        total_timeout=20.0,
    )

    result_by_id = {r["id"]: r for r in sandbox_results}

    # 3. Grade Visible Tests using shared evaluator
    graded_visible, all_vis_passed, any_mutated = grade_visible_test_cases(
        function_name=challenge.function_name,
        visible_tests=challenge.visible_tests,
        result_by_id=result_by_id,
    )
    all_passed = all_vis_passed

    # 4. Grade Hidden Tests
    graded_hidden = []
    for idx, test in enumerate(challenge.hidden_tests):
        t_id = hidden_offset + idx + 1
        raw = result_by_id.get(t_id, {})
        status = raw.get("status", "error")
        value = raw.get("value")
        exc_type = raw.get("exc_type")
        raw_limit = raw.get("limit")
        mutated = bool(raw.get("mutated", False))
        if mutated:
            any_mutated = True

        limit = _detect_disk_limit(exc_type, raw_limit)

        if status == "timeout":
            outcome = "timeout"
            all_passed = False
        elif status == "error":
            outcome = "runtime_error"
            all_passed = False
        elif status == "ok":
            if mutated:
                outcome = "wrong_output"
                all_passed = False
            elif strict_value_equals(value, test["expected"]):
                outcome = "passed"
            else:
                outcome = "wrong_output"
                all_passed = False
        else:
            outcome = "runtime_error"
            all_passed = False

        # Internal category and values preserved for host analysis; stripped before API presentation
        graded_hidden.append({
            "test": idx + 1,
            "outcome": outcome,
            "limit": limit,
            "category": test["category"],
            "raw_value": value,
            "expected_value": test["expected"],
            "mutated": mutated,
            "args": test["args"],
        })

    return {
        "visible": graded_visible,
        "hidden": graded_hidden,
        "all_passed": all_passed,
        "any_mutated": any_mutated,
    }


def strip_for_api(graded_submission: dict[str, Any]) -> dict[str, Any]:
    """Strip internal fields from hidden test results before sending to the client.

    SECURITY RULE:
    Hidden test inputs, expected values, outputs, categories, and exceptions
    must NEVER be exposed to the frontend or learner API responses.
    Returns strictly: test, outcome, limit.
    """
    clean_hidden = []
    for h in graded_submission.get("hidden", []):
        clean_hidden.append({
            "test": h["test"],
            "outcome": h["outcome"],
            "limit": h.get("limit"),
        })

    # Visible tests: remove internal helper fields like stdout if present
    clean_visible = []
    for v in graded_submission.get("visible", []):
        clean_item = {
            "test": v["test"],
            "call": v["call"],
            "expected": v["expected"],
            "got": v["got"],
            "outcome": v["outcome"],
            "limit": v.get("limit"),
            "error": v.get("error"),
        }
        clean_visible.append(clean_item)

    return {
        "visible": clean_visible,
        "hidden": clean_hidden,
        "all_passed": graded_submission["all_passed"],
    }
