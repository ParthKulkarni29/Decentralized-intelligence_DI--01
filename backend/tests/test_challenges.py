"""Tests for challenge loader, validator, and public contract secrecy."""

from collections import Counter
import json
from pathlib import Path
import pytest
from backend.engine.challenges import (
    FullChallenge,
    get_full_challenge,
    get_public_challenge,
    list_challenges,
    load_all_challenges,
    load_challenge,
)

ALL_TEN_CHALLENGES = [
    "last-k-total",
    "count-passes",
    "first-n-squares",
    "middle-three",
    "safe-first-element",
    "count-even",
    "remove-duplicates",
    "running-total",
    "rotate-list",
    "group-passing-marks",
]


def test_ten_challenges_load_and_validate():
    """Verify all 10 challenges exist, validate, and satisfy difficulty and concept criteria."""
    challenges = load_all_challenges()
    assert len(challenges) == 10, f"Expected 10 challenges, found {len(challenges)}"

    difficulties = Counter()
    all_concepts = set()

    for ch_id in ALL_TEN_CHALLENGES:
        assert ch_id in challenges, f"Missing challenge: {ch_id}"
        full = challenges[ch_id]
        assert isinstance(full, FullChallenge)
        assert full.id == ch_id
        difficulties[full.difficulty] += 1
        all_concepts.update(full.concepts)

        assert len(full.visible_tests) >= 3, f"{ch_id} must have >= 3 visible tests"
        assert len(full.hidden_tests) >= 6, f"{ch_id} must have >= 6 hidden tests"
        assert "generic" in full.hints, f"{ch_id} must have 'generic' hint ladder"
        for tag, ladder in full.hints.items():
            assert len(ladder) == 3, f"{ch_id} ladder for '{tag}' must have 3 hints"
            for h in ladder:
                assert isinstance(h, str) and h.strip(), f"Empty hint in {ch_id} ladder {tag}"
        for h_test in full.hidden_tests:
            assert h_test["category"].strip(), f"Missing category label in {ch_id} hidden test"

    # Verify difficulty counts: 5 easy, 3 medium, 2 hard
    assert difficulties["easy"] == 5, f"Expected 5 easy, got {difficulties['easy']}"
    assert difficulties["medium"] == 3, f"Expected 3 medium, got {difficulties['medium']}"
    assert difficulties["hard"] == 2, f"Expected 2 hard, got {difficulties['hard']}"

    # Verify distinct concepts: at least 4
    assert len(all_concepts) >= 4, f"Expected >= 4 distinct concepts, got {len(all_concepts)}: {all_concepts}"

    # Verify safe-first-element targets unhandled_short_input
    assert "unhandled_short_input" in challenges["safe-first-element"].targets_mistakes

    # Verify mutates_input is present in descriptions and targets
    assert "mutates_input" in challenges["remove-duplicates"].targets_mistakes
    assert "not be changed" in challenges["remove-duplicates"].description.lower()
    assert "mutates_input" in challenges["running-total"].targets_mistakes
    assert "not be changed" in challenges["running-total"].description.lower()


def test_public_challenge_strictly_excludes_hidden_data():
    """SECURITY TEST: Verify get_public_challenge never exposes hidden test data for all 10 challenges."""
    for ch_id in ALL_TEN_CHALLENGES:
        full = get_full_challenge(ch_id)
        public = get_public_challenge(ch_id)

        # 1. Allowed keys strictly match docs/API_CONTRACT.md
        expected_keys = {
            "id",
            "title",
            "difficulty",
            "concepts",
            "description",
            "starter_code",
            "visible_tests",
            "hidden_test_count",
            "arg_names",
        }
        assert set(public.keys()) == expected_keys, f"Unexpected public keys in {ch_id}"

        # 2. Field-level checks
        assert "hidden_tests" not in public
        assert "reference" not in public
        assert "reference_code" not in public
        assert "hints" not in public
        assert "targets_mistakes" not in public
        assert public["hidden_test_count"] == len(full.hidden_tests)

        # 3. Deep inspection of serialized JSON representation
        serialized_public = json.dumps(public)

        # Hidden categories must NEVER appear in public output
        for test in full.hidden_tests:
            category = test["category"]
            assert category not in serialized_public, f"Category '{category}' leaked in public output of {ch_id}!"


def test_list_challenges_format():
    """Verify list_challenges matches GET /api/challenges summary contract for all 10 challenges."""
    challenges = list_challenges()
    assert len(challenges) == 10
    for ch in challenges:
        assert set(ch.keys()) == {"id", "title", "difficulty", "concepts", "status"}
        assert ch["status"] == "new"


def test_all_reference_solutions_match_all_visible_and_hidden_tests():
    """Verify EVERY challenge's reference.py produces expected output on all visible and hidden tests."""
    challenges = load_all_challenges()
    assert len(challenges) == 10

    for ch_id, full in challenges.items():
        fn = full.reference_func

        # Visible tests
        for idx, test in enumerate(full.visible_tests):
            actual = fn(*test["args"])
            assert actual == test["expected"], (
                f"{ch_id} visible test #{idx + 1} failed: got {actual!r}, expected {test['expected']!r}"
            )

        # Hidden tests
        for idx, test in enumerate(full.hidden_tests):
            actual = fn(*test["args"])
            assert actual == test["expected"], (
                f"{ch_id} hidden test #{idx + 1} ({test['category']}) failed: got {actual!r}, expected {test['expected']!r}"
            )


def test_validation_rejects_insufficient_visible_tests(tmp_path: Path):
    """Validation test: < 3 visible tests raises ValueError."""
    ch_dir = tmp_path / "bad-challenge"
    ch_dir.mkdir()

    meta = {
        "id": "bad-challenge",
        "title": "Bad Challenge",
        "difficulty": "easy",
        "concepts": ["lists"],
        "description": "Desc",
        "starter_code": "def f(): pass",
        "function_name": "f",
        "arg_names": [],
        "targets_mistakes": [],
        "boundary_values": [],
        "sort_order": 1,
    }
    (ch_dir / "challenge.json").write_text(json.dumps(meta), encoding="utf-8")
    (ch_dir / "visible_tests.json").write_text(json.dumps([
        {"args": [], "expected": 1, "category": "c1"},
        {"args": [], "expected": 2, "category": "c2"},
    ]), encoding="utf-8")
    (ch_dir / "hidden_tests.json").write_text(json.dumps([
        {"args": [], "expected": i, "category": f"h{i}"} for i in range(5)
    ]), encoding="utf-8")
    (ch_dir / "hints.json").write_text(json.dumps({"generic": ["h1", "h2", "h3", "h4"]}), encoding="utf-8")
    (ch_dir / "reference.py").write_text("def f(): return 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match=">= 3 visible tests"):
        load_challenge(ch_dir)


def test_validation_rejects_insufficient_hidden_tests(tmp_path: Path):
    """Validation test: < 5 hidden tests raises ValueError."""
    ch_dir = tmp_path / "bad-hidden"
    ch_dir.mkdir()

    meta = {
        "id": "bad-hidden",
        "title": "Bad Hidden",
        "difficulty": "medium",
        "concepts": ["loops"],
        "description": "Desc",
        "starter_code": "def f(): pass",
        "function_name": "f",
        "arg_names": [],
        "targets_mistakes": [],
        "boundary_values": [],
        "sort_order": 1,
    }
    (ch_dir / "challenge.json").write_text(json.dumps(meta), encoding="utf-8")
    (ch_dir / "visible_tests.json").write_text(json.dumps([
        {"args": [], "expected": i, "category": f"v{i}"} for i in range(3)
    ]), encoding="utf-8")
    (ch_dir / "hidden_tests.json").write_text(json.dumps([
        {"args": [], "expected": i, "category": f"h{i}"} for i in range(4)
    ]), encoding="utf-8")
    (ch_dir / "hints.json").write_text(json.dumps({"generic": ["h1", "h2", "h3", "h4"]}), encoding="utf-8")
    (ch_dir / "reference.py").write_text("def f(): return 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match=">= 5 hidden tests"):
        load_challenge(ch_dir)


def test_validation_rejects_invalid_concept(tmp_path: Path):
    """Validation test: invalid concept raises ValueError."""
    ch_dir = tmp_path / "bad-concept"
    ch_dir.mkdir()

    meta = {
        "id": "bad-concept",
        "title": "Bad Concept",
        "difficulty": "easy",
        "concepts": ["invalid_concept_name"],
        "description": "Desc",
        "starter_code": "def f(): pass",
        "function_name": "f",
        "arg_names": [],
        "targets_mistakes": [],
        "boundary_values": [],
        "sort_order": 1,
    }
    (ch_dir / "challenge.json").write_text(json.dumps(meta), encoding="utf-8")
    (ch_dir / "visible_tests.json").write_text(json.dumps([
        {"args": [], "expected": i, "category": f"v{i}"} for i in range(3)
    ]), encoding="utf-8")
    (ch_dir / "hidden_tests.json").write_text(json.dumps([
        {"args": [], "expected": i, "category": f"h{i}"} for i in range(5)
    ]), encoding="utf-8")
    (ch_dir / "hints.json").write_text(json.dumps({"generic": ["h1", "h2", "h3", "h4"]}), encoding="utf-8")
    (ch_dir / "reference.py").write_text("def f(): return 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid concepts"):
        load_challenge(ch_dir)


def test_validation_rejects_invalid_hint_ladder(tmp_path: Path):
    """Validation test: ladder with != 3 hints raises ValueError."""
    ch_dir = tmp_path / "bad-hints"
    ch_dir.mkdir()

    meta = {
        "id": "bad-hints",
        "title": "Bad Hints",
        "difficulty": "hard",
        "concepts": ["conditions"],
        "description": "Desc",
        "starter_code": "def f(): pass",
        "function_name": "f",
        "arg_names": [],
        "targets_mistakes": [],
        "boundary_values": [],
        "sort_order": 1,
    }
    (ch_dir / "challenge.json").write_text(json.dumps(meta), encoding="utf-8")
    (ch_dir / "visible_tests.json").write_text(json.dumps([
        {"args": [], "expected": i, "category": f"v{i}"} for i in range(3)
    ]), encoding="utf-8")
    (ch_dir / "hidden_tests.json").write_text(json.dumps([
        {"args": [], "expected": i, "category": f"h{i}"} for i in range(5)
    ]), encoding="utf-8")
    (ch_dir / "hints.json").write_text(json.dumps({"generic": ["h1", "h2"]}), encoding="utf-8")
    (ch_dir / "reference.py").write_text("def f(): return 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly 3 hints"):
        load_challenge(ch_dir)
