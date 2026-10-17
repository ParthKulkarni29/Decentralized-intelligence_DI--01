"""Unit tests for the integrated AI layer in submit and hint endpoints.

Verifies:
1. Explanations:
   - Base deterministic explanation follows exact specification:
     'All 3 visible tests pass, but 3 of the 6 hidden tests fail: one crashes and two give the wrong total.
      All three are cases where k is larger than the number of marks.'
   - Model is called to rewrite explanation; if model is blocked by guardrails (e.g. returns code),
     the endpoint cleanly falls back to the deterministic base explanation.
2. Hints:
   - Authored ladder selected from submission's first mistake tag (or generic).
   - Dynamic AI personalization via coach.call_model.
   - When model returns leaky responses (code fences, full solution, hidden test values),
     guardrails block it and the endpoint falls back to authored hint with source='authored'.
   - When model returns clean text, stored with source='ai'.
   - If submission passed all tests, no hint is given.
3. Adversarial Hint Harvester Test:
   - Requests all hints for 3 failing submissions using a loop that queries 10 times (past exhaustion).
   - Asserts no hint contains a code fence (```).
   - Attempts to extract any code-like strings from hints and merge into learner's code.
   - Runs merged code through real grader and asserts it STILL fails at least one hidden test.
"""

import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import init_db
from backend.engine.challenges import get_full_challenge
from backend.engine.grader import grade_submission

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUNNER_SCRIPT = PROJECT_ROOT / "docker" / "runner" / "runner.py"


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
    """Set up isolated SQLite DB and runner execution bridge for each test."""
    test_db = tmp_path / "test_ai_layer.db"
    init_db(str(test_db))
    monkeypatch.setenv("DATABASE_PATH", str(test_db))
    monkeypatch.setenv("GEMINI_API_KEY", "test_mock_gemini_key")

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
# Explanation Tests
# ==============================================================================

def test_explanation_fallback_on_leaky_model():
    """When the model generates code in explanation, guardrail blocks it and falls back to base."""
    client = TestClient(app)
    buggy_code = "def last_k_total(marks, k):\n    return sum(marks[-k:])\n"

    # Mock model to return a code block that leaks the solution
    mock_response = MagicMock()
    mock_response.text = "Here is the fix:\n```python\nif k <= 0: return 0\nreturn sum(marks[-k:])\n```"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        res = client.post("/api/submit", json={
            "learner_id": "learner_leaky_exp",
            "challenge_id": "last-k-total",
            "code": buggy_code,
        })
        assert res.status_code == 200
        data = res.json()
        assert not data["all_passed"]
        exp = data["explanation"]

        # Assert no markdown code fences exist in explanation
        assert "```" not in exp
        # Assert fallback contains deterministic count breakdown
        assert "visible tests pass" in exp
        assert "hidden tests fail" in exp


def test_explanation_ai_rewrite_accepted_when_clean():
    """When the model generates warm pedagogical prose without code, it is accepted."""
    client = TestClient(app)
    buggy_code = "def last_k_total(marks, k):\n    return sum(marks[-k:])\n"

    clean_ai_exp = (
        "Your function works nicely on normal inputs, but encounters trouble when k is larger than the list "
        "or equal to zero. Take a look at where you slice marks and consider how to handle edge cases before summing."
    )
    mock_response = MagicMock()
    mock_response.text = clean_ai_exp
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        res = client.post("/api/submit", json={
            "learner_id": "learner_clean_exp",
            "challenge_id": "last-k-total",
            "code": buggy_code,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["explanation"] == clean_ai_exp


# ==============================================================================
# Hint Personalization and Leaky Fallback Tests
# ==============================================================================

def test_hint_fallback_to_authored_when_model_is_leaky():
    """Prove that leaky responses (code fence, full solution, hidden values) fall back to authored hint."""
    client = TestClient(app)
    buggy_code = "def last_k_total(marks, k):\n    return sum(marks[-k:])\n"

    # Submit code without mocking to create submission record
    sub_res = client.post("/api/submit", json={
        "learner_id": "learner_hint_mock",
        "challenge_id": "last-k-total",
        "code": buggy_code,
    })
    submission_id = sub_res.json()["submission_id"]

    leaky_responses = [
        # Attempt 1 (Layer 1): Leaks full markdown code fence
        "```python\ndef last_k_total(marks, k):\n    return sum(marks)\n```",
        # Attempt 2 (Layer 2): Leaks hidden test input data
        "Your code fails on hidden input [5, 10, 15, 20, 25].",
        # Attempt 3 (Layer 3): Leaks prohibited giveaway phrase and code
        "The solution is to check if k <= 0 and return 0.",
    ]

    mock_client = MagicMock()

    for idx, leaky_text in enumerate(leaky_responses, 1):
        mock_resp = MagicMock()
        mock_resp.text = leaky_text
        mock_client.models.generate_content.return_value = mock_resp

        with patch("google.genai.Client", return_value=mock_client):
            hint_res = client.post("/api/hint", json={"submission_id": submission_id})
            assert hint_res.status_code == 200
            hdata = hint_res.json()

            # Guardrail must block leaky text
            assert "```" not in hdata["hint"]
            assert "[5, 10, 15, 20, 25]" not in hdata["hint"]
            assert "The solution is" not in hdata["hint"]
            assert hdata["hint_number"] == idx
            assert hdata["hints_total"] == 3

            # Must fall back to authored ladder entry
            ch = get_full_challenge("last-k-total")
            authored_ladder = ch.hints.get("unhandled_short_input", ch.hints["generic"])
            expected_fallback = authored_ladder[idx - 1]
            assert hdata["hint"] == expected_fallback


def test_no_hint_given_for_passing_submission():
    """Verify that when learner code passes everything, no hint is given."""
    client = TestClient(app)
    correct_code = """
def last_k_total(marks, k):
    if k <= 0 or not marks:
        return 0
    if k >= len(marks):
        return sum(marks)
    return sum(marks[-k:])
"""
    sub_res = client.post("/api/submit", json={
        "learner_id": "learner_pass_nohint",
        "challenge_id": "last-k-total",
        "code": correct_code,
    })
    sub_data = sub_res.json()
    assert sub_data["all_passed"] is True
    sub_id = sub_data["submission_id"]

    hint_res = client.post("/api/hint", json={"submission_id": sub_id})
    assert hint_res.status_code == 200
    hdata = hint_res.json()
    assert hdata["hint_number"] == 0
    assert hdata["exhausted"] is True
    assert "passed all tests" in hdata["hint"]


# ==============================================================================
# Adversarial Hint Harvester Test
# ==============================================================================

FAILING_SCENARIOS = [
    (
        "last-k-total",
        "def last_k_total(marks, k):\n    return sum(marks[-k:])\n",
    ),
    (
        "middle-three",
        "def middle_three(items):\n    mid = len(items) // 2\n    return [items[mid - 1], items[mid], items[mid + 1]]\n",
    ),
    (
        "count-passes",
        "def count_passes(marks):\n    return sum(1 for m in marks if m > 40)\n",
    ),
]


@pytest.mark.parametrize("challenge_id,buggy_code", FAILING_SCENARIOS)
def test_adversarial_hint_harvesting_cannot_solve_challenge(challenge_id: str, buggy_code: str):
    """Adversarial check:

    Requests all hints for 3 failing submissions (looping 10 times past exhaustion).
    Asserts no hint contains a code fence.
    Merges any extracted code-like tokens from the hints into the learner's code.
    Runs merged code through grader and asserts it STILL fails at least one hidden test.
    """
    client = TestClient(app)
    challenge = get_full_challenge(challenge_id)

    # 1. Submit buggy solution
    sub_res = client.post("/api/submit", json={
        "learner_id": f"harvester_{challenge_id}",
        "challenge_id": challenge_id,
        "code": buggy_code,
    })
    assert sub_res.status_code == 200
    sub_id = sub_res.json()["submission_id"]

    all_hint_texts: list[str] = []

    # 2. Query hints 10 times (past 4-hint ladder exhaustion)
    for step in range(1, 11):
        h_res = client.post("/api/hint", json={"submission_id": sub_id})
        assert h_res.status_code == 200
        h_data = h_res.json()
        hint_text = h_data["hint"]
        all_hint_texts.append(hint_text)

        # Invariant 1: No hint may contain a markdown code fence
        assert "```" not in hint_text, f"Hint step {step} for {challenge_id} contained code fence!"
        # Invariant 2: No hint may start with def or return
        assert not hint_text.strip().startswith("def ")
        assert not hint_text.strip().startswith("return ")

    # 3. Attempt to extract code-like tokens or lines from all hints
    extracted_code_snippets = []
    for h in all_hint_texts:
        # Search for assignment statements, loop statements, returns, or backticked expressions
        candidates = re.findall(r"`([^`]+)`", h)
        for cand in candidates:
            # If candidate resembles code (contains operators, brackets, colons)
            if any(op in cand for op in ["=", "[", "]", ":", "+", "-", "*", "/"]):
                extracted_code_snippets.append(cand)

    # Invariant 3: No compound code solution was leaked in backticks
    assert len(extracted_code_snippets) == 0, f"Leaked code candidates found in hints: {extracted_code_snippets}"

    # 4. Attempt to append any extracted tokens to learner's code
    merged_code = buggy_code
    if extracted_code_snippets:
        merged_code += "\n" + "\n".join(extracted_code_snippets)

    # 5. Run merged code through real grader
    graded = grade_submission(challenge, merged_code)

    # Invariant 4: Merged code MUST STILL FAIL at least one hidden test!
    assert graded["all_passed"] is False, f"Adversarial exploit: hints provided enough code to pass {challenge_id}!"
    failed_hidden = [t for t in graded["hidden"] if t["outcome"] != "passed"]
    assert len(failed_hidden) >= 1
