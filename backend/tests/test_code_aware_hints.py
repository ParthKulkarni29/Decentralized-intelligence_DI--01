"""Hints must react to what the learner actually typed, before and after submitting."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.database import init_db
from backend.engine.challenges import get_full_challenge
from backend.engine.code_hints import analyze_code
from backend.main import app


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "hints.db"
    init_db(str(db))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    # No AI key in tests: deterministic ladders only
    monkeypatch.setattr("backend.api.routes.call_model", lambda **kw: None)


CH = "last-k-total"


def kinds(code):
    return [f.kind for f in analyze_code(get_full_challenge(CH), code)]


def test_analysis_detects_specific_shapes():
    assert kinds("") == ["empty_editor"]
    assert kinds("def last_k_total(marks, k:\n    return 1") == ["syntax_error"]
    assert kinds("def other(a):\n    return a") == ["missing_function"]
    assert "placeholder_body" in kinds("def last_k_total(marks, k):\n    pass\n")
    assert "print_without_return" in kinds("def last_k_total(marks, k):\n    print(sum(marks[-k:]))\n")
    assert "no_return" in kinds("def last_k_total(marks, k):\n    t = 0\n    for m in marks:\n        t += m\n")
    assert "unused_input" in kinds("def last_k_total(marks, k):\n    return sum(marks)\n")
    assert "mutates_input" in kinds("def last_k_total(marks, k):\n    marks.sort()\n    return sum(marks)\n")
    assert "negative_slice_unguarded" in kinds("def last_k_total(marks, k):\n    return sum(marks[-k:])\n")
    assert "endless_loop" in kinds("def last_k_total(marks, k):\n    while True:\n        k += 1\n    return k\n")


def test_correct_code_has_no_structural_findings():
    good = (
        "def last_k_total(marks, k):\n"
        "    if k <= 0 or not marks:\n        return 0\n"
        "    return sum(marks[-k:])\n"
    )
    assert kinds(good) == []


def test_hints_never_contain_code_or_fences():
    for code in ["", "def last_k_total(marks, k):\n    pass\n", "def last_k_total(marks, k:\n x", "def f(a):\n  return a"]:
        for f in analyze_code(get_full_challenge(CH), code):
            for line in f.ladder:
                assert "```" not in line and "def " not in line and "return " not in line.replace("hand back", "")


def _hint(client, level, code, **extra):
    return client.post("/api/hint", json={"challenge_id": CH, "code": code, "hint_level": level, **extra})


def test_editor_hint_works_before_submitting_and_is_code_specific():
    client = TestClient(app)
    syntax = _hint(client, 2, "def last_k_total(marks, k:\n    return 1")
    assert syntax.status_code == 200
    body = syntax.json()
    assert body["hint_number"] == 2 and body["hints_total"] == 3 and body["exhausted"] is False
    assert "line" in body["hint"].lower()

    placeholder = _hint(client, 2, "def last_k_total(marks, k):\n    pass\n").json()
    assert placeholder["hint"] != body["hint"]
    assert "placeholder" in placeholder["hint"]

    assert _hint(client, 3, "def last_k_total(marks, k):\n    pass\n").json()["exhausted"] is True


def test_editor_hint_default_level_and_validation():
    client = TestClient(app)
    assert client.post("/api/hint", json={"challenge_id": CH, "code": "x = 1"}).json()["hint_number"] == 1
    assert client.post("/api/hint", json={}).status_code == 400
    assert client.post("/api/hint", json={"challenge_id": CH}).status_code == 400
    assert _hint(client, 4, "x").status_code == 400
    assert client.post("/api/hint", json={"challenge_id": "nope", "code": "x"}).status_code == 404
    assert client.post("/api/hint", json={"challenge_id": CH, "code": "x" * 20001}).status_code == 400


def test_editor_hint_writes_nothing():
    import sqlite3, os
    client = TestClient(app)
    _hint(client, 1, "def last_k_total(marks, k):\n    pass\n")
    conn = sqlite3.connect(os.environ["DATABASE_PATH"])
    assert conn.execute("SELECT COUNT(*) FROM hints").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0
    conn.close()


def test_editor_hint_does_not_leak_hidden_data():
    client = TestClient(app)
    ch = get_full_challenge(CH)
    secrets = {str(v) for t in ch.hidden_tests for v in (t.get("args") or [])}
    text = " ".join(_hint(client, lv, "def last_k_total(marks, k):\n    return sum(marks[-k:])\n").json()["hint"] for lv in (1, 2, 3))
    for s in secrets:
        if len(s) > 4:
            assert s not in text


def test_live_mode_returns_whole_ladder_without_ai_or_sandbox(monkeypatch):
    def boom(**kw):
        raise AssertionError("live mode must not call the AI or the sandbox")
    monkeypatch.setattr("backend.api.routes.call_model", boom)
    monkeypatch.setattr("backend.api.routes.grade_run", boom)
    client = TestClient(app)
    r = client.post("/api/hint", json={"challenge_id": CH, "code": "hello world(print)", "all_levels": True}).json()
    assert r["focus"] == "syntax_error" and len(r["ladder"]) == 3 and r["ladder"][0] == r["hint"]

    clean = (
        "def last_k_total(marks, k):\n    if k <= 0 or not marks:\n        return 0\n    return sum(marks[-k:])\n"
    )
    assert client.post("/api/hint", json={"challenge_id": CH, "code": clean, "all_levels": True}).json()["ladder"] == []

    failing = client.post("/api/hint", json={
        "challenge_id": CH, "code": clean, "all_levels": True, "visible_outcomes": ["passed", "wrong_output"],
    }).json()
    assert failing["focus"] == "visible_outcomes" and "1 of 2" in failing["ladder"][1]

    bad = client.post("/api/hint", json={"challenge_id": CH, "code": clean, "all_levels": True, "visible_outcomes": ["secret"]})
    assert bad.status_code == 400


USER_SCRIPT = (
    "# Program to add two numbers\n\na = 10\nb = 20\n\nsum = a + b\nprint(\"The sum is:\", Sum)\n"
)


def test_unrelated_script_gets_hints_about_its_own_bug():
    """A learner typing a different program must see hints about THAT code, not about the challenge function."""
    ks = kinds(USER_SCRIPT)
    assert ks[0] == "undefined_name_case"
    assert "shadowed_builtin" in ks and ks[-1] == "missing_function"
    f = analyze_code(get_full_challenge(CH), USER_SCRIPT)[0]
    assert "Line 7" in f.ladder[1] and "Sum" in f.ladder[1]
    assert "last_k_total" not in " ".join(f.ladder)


def test_other_undefined_name_and_valid_names_not_flagged():
    assert kinds("x = 1\nprint(y)\n")[0] == "undefined_name"
    ok = "import math\nfor i in range(3):\n    v = [j for j in range(i)]\nprint(math.pi, v, i)\n"
    assert "undefined_name" not in kinds(ok) and "undefined_name_case" not in kinds(ok)
    assert "undefined_name" not in kinds("from os import *\nprint(path)\n")


def test_live_endpoint_includes_notes_for_other_problems():
    client = TestClient(app)
    r = client.post("/api/hint", json={"challenge_id": CH, "code": USER_SCRIPT, "all_levels": True}).json()
    assert r["focus"] == "undefined_name_case" and len(r["notes"]) >= 1
