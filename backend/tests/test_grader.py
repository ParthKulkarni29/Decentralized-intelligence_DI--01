"""Unit tests for submission grading, type strictness, and API secrecy stripping."""

import pytest
from backend.engine.challenges import get_full_challenge
from backend.engine.grader import grade_submission, strip_for_api, strict_value_equals


def test_strict_value_equals_boolean_vs_integer():
    """Verify booleans are never equal to integers (True != 1, False != 0)."""
    assert strict_value_equals(True, True) is True
    assert strict_value_equals(False, False) is True
    assert strict_value_equals(1, 1) is True
    assert strict_value_equals(0, 0) is True

    # Critical anti-bug test
    assert strict_value_equals(True, 1) is False
    assert strict_value_equals(1, True) is False
    assert strict_value_equals(False, 0) is False
    assert strict_value_equals(0, False) is False
    assert strict_value_equals([True], [1]) is False
    assert strict_value_equals({"a": True}, {"a": 1}) is False


def test_strict_value_equals_nested_collections():
    """Verify recursive list and dictionary comparisons."""
    assert strict_value_equals([1, 2, [3, 4]], [1, 2, [3, 4]]) is True
    assert strict_value_equals([1, 2, [3, 4]], [1, 2, [3, 5]]) is False
    assert strict_value_equals({"pass": [40], "fail": [20]}, {"pass": [40], "fail": [20]}) is True
    assert strict_value_equals({"pass": [40]}, {"pass": [40], "fail": []}) is False


def test_strip_for_api_enforces_hidden_test_secrecy():
    """SECURITY TEST: Verify strip_for_api completely removes category and internal fields."""
    mock_graded = {
        "visible": [
            {
                "test": 1,
                "call": "last_k_total([10, 20], 1)",
                "expected": "20",
                "got": "20",
                "outcome": "passed",
                "limit": None,
                "error": None,
                "stdout": "debug print",
            }
        ],
        "hidden": [
            {
                "test": 1,
                "outcome": "passed",
                "limit": None,
                "category": "INTERNAL_SECRET_CATEGORY_NEVER_LEAK",
            },
            {
                "test": 2,
                "outcome": "runtime_error",
                "limit": "memory",
                "category": "INTERNAL_OOM_CATEGORY",
            },
        ],
        "all_passed": False,
        "any_mutated": False,
    }

    clean = strip_for_api(mock_graded)

    assert clean["all_passed"] is False

    # Check hidden tests strictly contain ONLY test, outcome, limit
    for h in clean["hidden"]:
        assert set(h.keys()) == {"test", "outcome", "limit"}
        assert "category" not in h

    # Check visible tests do not leak internal debug stdout
    for v in clean["visible"]:
        assert "stdout" not in v


def test_grade_submission_with_mocked_sandbox(monkeypatch):
    """Test full grade_submission flow across passed, wrong_output, runtime_error, and timeout."""
    challenge = get_full_challenge("last-k-total")
    total_tests = len(challenge.visible_tests) + len(challenge.hidden_tests)

    # Simulate sandbox results:
    # Test 1: passed
    # Test 2: wrong_output
    # Test 3: runtime_error
    # Hidden 1: timeout
    # Rest: passed
    mock_results = []
    # Test 1: visible correct
    mock_results.append({"id": 1, "status": "ok", "value": challenge.visible_tests[0]["expected"], "mutated": False})
    # Test 2: visible wrong
    mock_results.append({"id": 2, "status": "ok", "value": -999, "mutated": False})
    # Test 3: visible error
    mock_results.append({"id": 3, "status": "error", "value": None, "exc_type": "IndexError", "limit": None, "mutated": False})

    # Hidden tests
    hidden_start = len(challenge.visible_tests) + 1
    # Hidden 1: timeout
    mock_results.append({"id": hidden_start, "status": "timeout", "value": None, "limit": "time", "mutated": False})
    # Rest of hidden: ok
    for i in range(1, len(challenge.hidden_tests)):
        mock_results.append({
            "id": hidden_start + i,
            "status": "ok",
            "value": challenge.hidden_tests[i]["expected"],
            "mutated": False,
        })

    def mock_run_in_sandbox(*args, **kwargs):
        return mock_results

    monkeypatch.setattr("backend.engine.grader.run_in_sandbox", mock_run_in_sandbox)

    graded = grade_submission(challenge, "learner code")

    assert graded["all_passed"] is False

    # Check visible outcomes
    assert graded["visible"][0]["outcome"] == "passed"
    assert graded["visible"][1]["outcome"] == "wrong_output"
    assert graded["visible"][2]["outcome"] == "runtime_error"
    assert graded["visible"][2]["error"] == "IndexError"

    # Check hidden outcomes
    assert graded["hidden"][0]["outcome"] == "timeout"
    assert graded["hidden"][1]["outcome"] == "passed"
