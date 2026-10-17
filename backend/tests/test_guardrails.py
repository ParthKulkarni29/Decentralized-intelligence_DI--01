"""Unit tests for AI guardrails, prompt builders, and coach integration.

Tests Phase 6 Step 1:
- Positive guardrail examples (pedagogical prose, allowable backticks, prose with 'return').
- Negative guardrail examples (code fences, raw code lines, indented code, prohibited phrases,
  sneaky inline code expressions, word flood, leaked hidden test data).
- Prompt builders format and safety guarantees.
- Coach call_model retry, audit logging (SQLite & jsonl), and guardrail fallback.
"""

import json
from pathlib import Path
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from backend.ai.guardrails import check_text
from backend.ai.prompts import (
    SYSTEM_INSTRUCTION,
    build_explanation_prompt,
    build_hint_prompt,
    build_socratic_reply_prompt,
    build_explain_it_back_evaluation_prompt,
)
from backend.ai.coach import call_model, AI_LOGS_FILE
from backend.database import init_db, get_db_connection


# ==============================================================================
# Positive Guardrail Tests (Must PASS)
# ==============================================================================

POSITIVE_TEST_CASES = [
    # 1. Prose with 'return' in natural sentences
    ("Check what your function should return when given an empty list.", "prose_return_empty_list"),
    ("What value does the function return by default if no return statement is reached?", "prose_return_by_default"),
    ("Does your code return a value or just print it?", "prose_return_or_print"),
    ("Think about the expected return type for this challenge.", "prose_return_type"),

    # 2. Single identifiers and parameters in backticks
    ("Check the value of `k` when it exceeds the length of `marks`.", "backtick_parameters"),
    ("You can use `len()` to check the length of the input list.", "backtick_function_len"),
    ("Consider using `sum()` to calculate the total of the sublist.", "backtick_function_sum"),
    ("Make sure `items` is not modified by your function.", "backtick_identifier"),
    ("Think about what happens when `n` is equal to 0.", "backtick_single_char"),

    # 3. Pedagogical Socratic questions & advice
    ("Notice the threshold 40: should a mark of exactly 40 count as passing?", "boundary_socratic_question"),
    ("Trace your loop step by step with a small 3-element list on paper.", "trace_loop_advice"),
    ("The failing test falls under the single_element category.", "category_reference"),
    ("Check whether your accumulator variable should start at 0 instead of another number.", "accumulator_advice"),
    ("Make sure you use an inclusive comparison so values >= 40 are counted.", "inclusive_comparison_prose"),
    ("You are almost there! What should happen when the input has length 1 or fewer?", "length_edge_case_prompt"),
]


@pytest.mark.parametrize("text,case_id", POSITIVE_TEST_CASES)
def test_guardrails_positive_cases(text: str, case_id: str):
    """Ensure pedagogically valid hints and explanations pass all guardrails."""
    ok, reason = check_text(text, is_hint=True)
    assert ok is True, f"Positive case '{case_id}' was unexpectedly rejected: {reason}"
    assert reason is None


# ==============================================================================
# Negative Guardrail Tests (Must REJECT)
# ==============================================================================

NEGATIVE_TEST_CASES = [
    # 1. Markdown code fences
    ("Here is what you should write:\n```python\nreturn sum(marks)\n```", "code_fence"),
    ("Try this:\n```\nx = 1\n```", "generic_code_fence"),

    # 2. Lines starting with code statements
    ("def last_k_total(marks, k):\n    return sum(marks)", "def_statement"),
    ("return sum(marks[-k:])", "bare_return_statement"),
    ("return 0", "return_zero_statement"),
    ("for i in range(1, n + 1):\n    pass", "for_loop_statement"),
    ("while k > 0:\n    k -= 1", "while_loop_statement"),
    ("if k <= 0:\n    return 0", "if_statement"),
    ("import math\nmath.sqrt(4)", "import_statement"),
    ("from math import sqrt", "from_import_statement"),

    # 3. Variable assignments
    ("x = marks[-k:]", "variable_assignment"),
    ("total += number", "augmented_assignment"),

    # 4. Indented code blocks
    ("Take a look:\n    result = items[0]\n    return result", "indented_code"),

    # 5. Prohibited giveaway phrases
    ("Here is the corrected version of your function for you.", "phrase_here_is_the_corrected"),
    ("The solution is to add a check for an empty list at the top.", "phrase_the_solution_is"),
    ("Here is the fix: check if the length is under three.", "phrase_here_is_the_fix"),
    ("Copy and paste this logic into your editor to pass.", "phrase_copy_and_paste"),
    ("Use this code to get full score on the challenge.", "phrase_use_this_code"),

    # 6. Sneaky inline code solutions
    ("Try using `range(max(0, len(x)-k), len(x))` to get the slice indices.", "sneaky_inline_complex_call"),
    ("You can simply write `marks[-k:]` to slice the list.", "sneaky_inline_subscript"),
    ("Just return `[i * i for i in range(1, n + 1)]` to build the squares.", "sneaky_inline_comprehension"),
    ("You need to calculate `n * n` and append it.", "sneaky_inline_binop"),

    # 7. Sneaky return expression embedded inside prose
    ("In your function you should write return 0 when the list is empty.", "sneaky_prose_return_expression"),
    ("When k is too large, you can just return sum(marks) directly.", "sneaky_prose_return_call"),

    # 8. Word limit violation (> 120 words)
    ("word " * 125, "word_flood_exceeding_120"),
]


@pytest.mark.parametrize("text,case_id", NEGATIVE_TEST_CASES)
def test_guardrails_negative_cases(text: str, case_id: str):
    """Ensure non-pedagogical, leaked, or code-bearing responses are strictly rejected."""
    ok, reason = check_text(text, is_hint=True)
    assert ok is False, f"Negative case '{case_id}' was unexpectedly accepted!"
    assert reason is not None
    assert len(reason) > 0


def test_guardrails_rejects_hidden_test_input_leak():
    """Guardrails must reject responses leaking hidden test input lists or dicts."""
    hidden_tests = [
        {"args": [[5, 10, 15, 20, 25], 4], "expected": 70, "category": "boundary_value"}
    ]
    leaked_text = "Your function gave the wrong output on the hidden input [5, 10, 15, 20, 25]."
    ok, reason = check_text(leaked_text, hidden_tests=hidden_tests)
    assert ok is False
    assert "Leaked hidden test" in reason


def test_guardrails_rejects_hidden_test_expected_output_leak():
    """Guardrails must reject responses leaking hidden test expected collections."""
    hidden_tests = [
        {"args": [5], "expected": [1, 4, 9, 16, 25], "category": "n_five"}
    ]
    leaked_text = "The expected output is [1, 4, 9, 16, 25] for this case."
    ok, reason = check_text(leaked_text, hidden_tests=hidden_tests)
    assert ok is False
    assert "Leaked hidden test" in reason


def test_guardrails_rejects_hidden_test_expected_numeric_leak():
    """Guardrails must reject explicit leakage of multi-digit hidden expected values."""
    hidden_tests = [
        {"args": [[5, 10, 15, 20, 25], 4], "expected": 70, "category": "boundary_value"}
    ]
    leaked_text = "The expected output is 70 for this input."
    ok, reason = check_text(leaked_text, hidden_tests=hidden_tests)
    assert ok is False
    assert "Leaked hidden test" in reason


# ==============================================================================
# Prompt Builders Tests
# ==============================================================================

def test_system_instruction_contains_core_pedagogy():
    """Verify system instruction explicitly bans code generation and solution spoiling."""
    assert "Debug Dojo" in SYSTEM_INSTRUCTION
    assert "NEVER provide complete corrected code" in SYSTEM_INSTRUCTION
    assert "Do not write any code at all" in SYSTEM_INSTRUCTION
    assert "Plain English only" in SYSTEM_INSTRUCTION


def test_build_explanation_prompt_strictly_excludes_hidden_values():
    """Verify build_explanation_prompt uses only counts and category words, never test data."""
    prompt = build_explanation_prompt(
        challenge_title="Last K Total",
        challenge_description="Returns sum of last k items",
        learner_code="def last_k_total(marks, k): return sum(marks[-k:])",
        passed_count=3,
        total_count=7,
        failed_categories=["empty_list", "k_zero"],
        mistake_tags=["unhandled_short_input"],
        visible_summaries=["Test 1: passed", "Test 2: passed"],
    )

    assert "Last K Total" in prompt
    assert "empty_list" in prompt
    assert "k_zero" in prompt
    assert "unhandled short input" in prompt
    assert "3 of 7 tests passed" in prompt
    # Guarantee no secret data tokens
    assert "hidden_tests" not in prompt


def test_build_hint_prompt_anchors_on_authored_hint():
    """Verify build_hint_prompt uses the authored hint as baseline for personalization."""
    prompt = build_hint_prompt(
        challenge_description="Returns middle three elements",
        learner_code="def middle_three(items): return items[1:4]",
        authored_hint="Consider what happens when the list has fewer than 3 elements.",
        hint_level=2,
        mistake_tags=["unhandled_short_input"],
    )
    assert "Hint Level: 2 of 3 (Pointer)" in prompt
    assert "Consider what happens when the list has fewer than 3 elements." in prompt
    assert "Do NOT write any code" in prompt


# ==============================================================================
# Coach call_model Tests
# ==============================================================================

def test_call_model_without_api_key_returns_none_and_logs(tmp_path: Path, monkeypatch):
    """When GEMINI_API_KEY is not set, call_model returns None and logs fallback."""
    test_db = tmp_path / "test_coach.db"
    init_db(str(test_db))
    monkeypatch.setenv("DATABASE_PATH", str(test_db))
    monkeypatch.setenv("GEMINI_API_KEY", "")

    result = call_model(
        purpose="hint_generation",
        system_instruction=SYSTEM_INSTRUCTION,
        user_content="Help me with my code",
    )
    assert result is None

    # Verify log entry in SQLite
    conn = sqlite3.connect(str(test_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM ai_logs ORDER BY id DESC LIMIT 1;")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row["purpose"] == "hint_generation"
    assert row["blocked"] == 1
    assert "GEMINI_API_KEY is not configured" in row["block_reason"]


def test_call_model_guardrail_rejection_logs_and_returns_none(tmp_path: Path, monkeypatch):
    """When Gemini returns unsafe code, call_model blocks it, logs why, and returns None."""
    test_db = tmp_path / "test_coach.db"
    init_db(str(test_db))
    monkeypatch.setenv("DATABASE_PATH", str(test_db))
    monkeypatch.setenv("GEMINI_API_KEY", "test_mock_key")

    mock_response = MagicMock()
    mock_response.text = "Here is the corrected code:\n```python\nreturn sum(marks)\n```"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        result = call_model(
            purpose="explanation",
            system_instruction=SYSTEM_INSTRUCTION,
            user_content="Why did my code fail?",
        )
        assert result is None

    # Verify blocked entry in SQLite
    conn = sqlite3.connect(str(test_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM ai_logs ORDER BY id DESC LIMIT 1;")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row["blocked"] == 1
    assert "markdown code fence" in row["block_reason"] or "prohibited giveaway" in row["block_reason"]


def test_call_model_success_logs_and_returns_clean_text(tmp_path: Path, monkeypatch):
    """When Gemini returns clean pedagogical text, call_model logs blocked=0 and returns text."""
    test_db = tmp_path / "test_coach.db"
    init_db(str(test_db))
    monkeypatch.setenv("DATABASE_PATH", str(test_db))
    monkeypatch.setenv("GEMINI_API_KEY", "test_mock_key")

    mock_response = MagicMock()
    mock_response.text = "Check what your function should return when given an empty list. Where does the loop start?"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        result = call_model(
            purpose="hint_personalization",
            system_instruction=SYSTEM_INSTRUCTION,
            user_content="Help me with last-k-total",
        )
        assert result == "Check what your function should return when given an empty list. Where does the loop start?"

    # Verify clean entry in SQLite
    conn = sqlite3.connect(str(test_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM ai_logs ORDER BY id DESC LIMIT 1;")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row["blocked"] == 0
    assert row["block_reason"] is None
    assert "empty list" in row["response_text"]


def test_layer_3_hint_contains_no_code_block_or_def_return_lines():
    """Verify that Layer 3 ('Approach') hints across all challenges and generated variants
    contain NO code blocks, NO markdown code fences, and NO def/return statements.
    """
    from backend.engine.challenges import load_all_challenges
    challenges = load_all_challenges()

    # 1. Verify every authored Layer 3 hint in every challenge
    for ch_id, ch in challenges.items():
        for tag, ladder in ch.hints.items():
            assert len(ladder) == 3, f"{ch_id}:{tag} must have exactly 3 hints"
            layer_3_hint = ladder[2]

            # Invariant 1: No code fence or block
            assert "```" not in layer_3_hint, f"Layer 3 hint in {ch_id}:{tag} contains code fence: {layer_3_hint}"

            # Invariant 2: No lines starting with def or return
            for line in layer_3_hint.splitlines():
                stripped = line.strip()
                assert not stripped.startswith("def "), f"Layer 3 hint contains def line: {line}"
                assert not stripped.startswith("return "), f"Layer 3 hint contains return line: {line}"

            # Invariant 3: Passes guardrail validation as safe hint
            ok, reason = check_text(layer_3_hint, is_hint=True)
            assert ok is True, f"Layer 3 hint in {ch_id}:{tag} failed guardrail check: {reason}"

    # 2. Verify that if a candidate Layer 3 hint DOES contain a code block or def/return line, guardrail REJECTS it
    code_block_hint = "Here is the strategy:\n```python\nfor x in items:\n    pass\n```"
    ok, reason = check_text(code_block_hint, is_hint=True)
    assert ok is False
    assert "code fence" in reason.lower()

    def_hint = "Consider this structure:\ndef helper(a, b):\n    return a + b"
    ok, reason = check_text(def_hint, is_hint=True)
    assert ok is False
    assert "def" in reason.lower() or "statement" in reason.lower()

    return_hint = "To solve this, use:\nreturn sum(marks[-k:])"
    ok, reason = check_text(return_hint, is_hint=True)
    assert ok is False
    assert "return" in reason.lower() or "statement" in reason.lower()

