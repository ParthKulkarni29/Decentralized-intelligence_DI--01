"""Challenge loader, validator, and public contract serializer.

Enforces strict challenge structure, test case counts, category descriptions,
and guarantees that hidden test data is completely isolated from public views.
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Optional

VALID_CONCEPTS = {"loops", "lists", "indexing", "conditions", "edge_cases", "functions"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}

DEFAULT_CHALLENGES_DIR = Path(__file__).resolve().parent.parent / "challenges"


def format_expected(val: Any) -> str:
    """Format expected return value into a string for the public API contract."""
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)


def format_call(function_name: str, args: list[Any]) -> str:
    """Format function call expression with arguments."""
    args_str = ", ".join(repr(arg) for arg in args)
    return f"{function_name}({args_str})"


@dataclass(frozen=True)
class FullChallenge:
    """Internal representation of a challenge with all test data and reference solution."""

    id: str
    title: str
    difficulty: str
    concepts: list[str]
    description: str
    starter_code: str
    function_name: str
    arg_names: list[str]
    targets_mistakes: list[str]
    boundary_values: list[Any]
    sort_order: int
    visible_tests: list[dict[str, Any]]
    hidden_tests: list[dict[str, Any]]
    hints: dict[str, list[str]]
    reference_code: str
    reference_func: Callable[..., Any]
    short_input_categories: list[str] = field(default_factory=list)


def _validate_json_serializable(value: Any, context: str) -> None:
    """Ensure data can be serialized to JSON without error."""
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} is not JSON serializable: {exc}") from exc


def load_challenge(challenge_dir: Path) -> FullChallenge:
    """Load and strictly validate a single challenge folder.

    Validates:
    - >= 3 visible tests with args, expected, category
    - >= 5 hidden tests with args, expected, category (descriptive)
    - Valid concepts subset and valid difficulty
    - JSON-serializable args and expected
    - Exactly 3 hints per ladder, including 'generic'
    - Executable reference.py defining the target function
    """
    if not challenge_dir.is_dir():
        raise ValueError(f"Challenge path {challenge_dir} is not a directory")

    challenge_json_path = challenge_dir / "challenge.json"
    visible_tests_path = challenge_dir / "visible_tests.json"
    hidden_tests_path = challenge_dir / "hidden_tests.json"
    hints_path = challenge_dir / "hints.json"
    reference_py_path = challenge_dir / "reference.py"

    required_files = [
        challenge_json_path,
        visible_tests_path,
        hidden_tests_path,
        hints_path,
        reference_py_path,
    ]
    for file_path in required_files:
        if not file_path.exists():
            raise ValueError(f"Missing required file: {file_path.name} in {challenge_dir.name}")

    # 1. Parse and validate challenge.json
    try:
        meta = json.loads(challenge_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {challenge_json_path.name}: {exc}") from exc

    required_keys = [
        "id",
        "title",
        "difficulty",
        "concepts",
        "description",
        "starter_code",
        "function_name",
        "arg_names",
        "targets_mistakes",
        "boundary_values",
        "sort_order",
    ]
    for key in required_keys:
        if key not in meta:
            raise ValueError(f"Missing key '{key}' in {challenge_json_path.name}")

    challenge_id = meta["id"]
    if challenge_id != challenge_dir.name:
        raise ValueError(f"Challenge id '{challenge_id}' does not match folder '{challenge_dir.name}'")

    if meta["difficulty"] not in VALID_DIFFICULTIES:
        raise ValueError(f"Invalid difficulty '{meta['difficulty']}'. Must be one of {VALID_DIFFICULTIES}")

    concepts = meta["concepts"]
    if not isinstance(concepts, list) or not concepts:
        raise ValueError("Concepts must be a non-empty list")
    invalid_concepts = set(concepts) - VALID_CONCEPTS
    if invalid_concepts:
        raise ValueError(f"Invalid concepts {invalid_concepts}. Allowed: {VALID_CONCEPTS}")

    function_name = meta["function_name"]
    arg_names = meta["arg_names"]
    if not isinstance(arg_names, list):
        raise ValueError("arg_names must be a list of strings")

    # 2. Parse and validate visible_tests.json
    try:
        visible_tests = json.loads(visible_tests_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {visible_tests_path.name}: {exc}") from exc

    if not isinstance(visible_tests, list) or len(visible_tests) < 3:
        raise ValueError(f"Challenge '{challenge_id}' must have >= 3 visible tests, found {len(visible_tests) if isinstance(visible_tests, list) else 0}")

    for idx, test in enumerate(visible_tests):
        if not isinstance(test, dict) or "args" not in test or "expected" not in test or "category" not in test:
            raise ValueError(f"Visible test #{idx + 1} in '{challenge_id}' missing args, expected, or category")
        if not isinstance(test["args"], list) or len(test["args"]) != len(arg_names):
            raise ValueError(f"Visible test #{idx + 1} args length ({len(test['args'])}) does not match arg_names ({len(arg_names)})")
        _validate_json_serializable(test["args"], f"Visible test #{idx + 1} args")
        _validate_json_serializable(test["expected"], f"Visible test #{idx + 1} expected")

    # 3. Parse and validate hidden_tests.json
    try:
        hidden_tests = json.loads(hidden_tests_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {hidden_tests_path.name}: {exc}") from exc

    if not isinstance(hidden_tests, list) or len(hidden_tests) < 5:
        raise ValueError(f"Challenge '{challenge_id}' must have >= 5 hidden tests, found {len(hidden_tests) if isinstance(hidden_tests, list) else 0}")

    for idx, test in enumerate(hidden_tests):
        if not isinstance(test, dict) or "args" not in test or "expected" not in test or "category" not in test:
            raise ValueError(f"Hidden test #{idx + 1} in '{challenge_id}' missing args, expected, or category")
        if not isinstance(test["args"], list) or len(test["args"]) != len(arg_names):
            raise ValueError(f"Hidden test #{idx + 1} args length ({len(test['args'])}) does not match arg_names ({len(arg_names)})")
        if not isinstance(test["category"], str) or not test["category"].strip():
            raise ValueError(f"Hidden test #{idx + 1} in '{challenge_id}' must have a descriptive string category")
        _validate_json_serializable(test["args"], f"Hidden test #{idx + 1} args")
        _validate_json_serializable(test["expected"], f"Hidden test #{idx + 1} expected")

    # 4. Parse and validate hints.json
    try:
        hints = json.loads(hints_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {hints_path.name}: {exc}") from exc

    if not isinstance(hints, dict) or "generic" not in hints:
        raise ValueError(f"hints.json for '{challenge_id}' must be an object containing at least a 'generic' ladder")

    for tag, ladder in hints.items():
        if not isinstance(ladder, list) or len(ladder) != 3:
            raise ValueError(f"Hint ladder for tag '{tag}' in '{challenge_id}' must contain exactly 3 hints, found {len(ladder) if isinstance(ladder, list) else 0}")
        for h_idx, hint_text in enumerate(ladder):
            if not isinstance(hint_text, str) or not hint_text.strip():
                raise ValueError(f"Hint #{h_idx + 1} for tag '{tag}' in '{challenge_id}' must be a non-empty string")

    # 5. Load and validate reference.py
    ref_code = reference_py_path.read_text(encoding="utf-8")
    compiled_code = compile(ref_code, str(reference_py_path), "exec")
    ref_namespace: dict[str, Any] = {}
    exec(compiled_code, ref_namespace)

    if function_name not in ref_namespace or not callable(ref_namespace[function_name]):
        raise ValueError(f"reference.py in '{challenge_id}' must define callable function '{function_name}'")

    return FullChallenge(
        id=challenge_id,
        title=meta["title"],
        difficulty=meta["difficulty"],
        concepts=concepts,
        description=meta["description"],
        starter_code=meta["starter_code"],
        function_name=function_name,
        arg_names=arg_names,
        targets_mistakes=meta["targets_mistakes"],
        boundary_values=meta["boundary_values"],
        sort_order=int(meta["sort_order"]),
        visible_tests=visible_tests,
        hidden_tests=hidden_tests,
        hints=hints,
        reference_code=ref_code,
        reference_func=ref_namespace[function_name],
        short_input_categories=list(meta.get("short_input_categories", [])),
    )


def load_all_challenges(challenges_dir: Optional[Path | str] = None) -> dict[str, FullChallenge]:
    """Load and validate all challenges found in the challenges directory."""
    path = Path(challenges_dir) if challenges_dir is not None else DEFAULT_CHALLENGES_DIR
    if not path.exists():
        return {}

    challenges: dict[str, FullChallenge] = {}
    for entry in sorted(path.iterdir()):
        if entry.is_dir() and not entry.name.startswith((".", "_")):
            ch = load_challenge(entry)
            challenges[ch.id] = ch
    return challenges


def list_challenges(challenges_dir: Optional[Path | str] = None) -> list[dict[str, Any]]:
    """List public challenge summaries matching GET /api/challenges contract."""
    all_ch = load_all_challenges(challenges_dir)
    sorted_ch = sorted(all_ch.values(), key=lambda c: (c.sort_order, c.id))

    return [
        {
            "id": ch.id,
            "title": ch.title,
            "difficulty": ch.difficulty,
            "concepts": ch.concepts,
            "status": "new",
        }
        for ch in sorted_ch
    ]


def get_public_challenge(challenge_id: str, challenges_dir: Optional[Path | str] = None) -> dict[str, Any]:
    """Return public challenge metadata strictly matching GET /api/challenges/{id}.

    SECURITY GUARANTEE:
    - Absolutely NO hidden test cases, inputs, expected values, or categories are included.
    - Only hidden_test_count (integer) is returned.
    - Reference solution and authored hints are completely excluded.
    """
    all_ch = load_all_challenges(challenges_dir)
    if challenge_id not in all_ch:
        raise KeyError(f"Challenge '{challenge_id}' not found")

    ch = all_ch[challenge_id]
    formatted_visible = [
        {
            "test": idx + 1,
            "call": format_call(ch.function_name, test["args"]),
            "expected": format_expected(test["expected"]),
        }
        for idx, test in enumerate(ch.visible_tests)
    ]

    return {
        "id": ch.id,
        "title": ch.title,
        "difficulty": ch.difficulty,
        "concepts": ch.concepts,
        "description": ch.description,
        "starter_code": ch.starter_code,
        "visible_tests": formatted_visible,
        "hidden_test_count": len(ch.hidden_tests),
        "arg_names": ch.arg_names,
    }


def get_full_challenge(challenge_id: str, challenges_dir: Optional[Path | str] = None) -> FullChallenge:
    """Internal function retrieving full challenge definition including hidden tests and reference solution."""
    all_ch = load_all_challenges(challenges_dir)
    if challenge_id not in all_ch:
        raise KeyError(f"Challenge '{challenge_id}' not found")
    return all_ch[challenge_id]


@dataclass(frozen=True)
class VisibleChallenge:
    """Internal challenge representation containing only visible tests and metadata.

    Guarantees hidden_tests.json, hints.json, and reference.py are NEVER loaded.
    """

    id: str
    title: str
    function_name: str
    visible_tests: list[dict[str, Any]]


def get_visible_challenge(
    challenge_id: str,
    challenges_dir: Optional[Path | str] = None,
) -> VisibleChallenge:
    """Load visible-only challenge configuration without touching hidden tests or reference files.

    SECURITY GUARANTEE:
    - Never opens or reads hidden_tests.json, hints.json, or reference.py.
    """
    path = Path(challenges_dir) if challenges_dir is not None else DEFAULT_CHALLENGES_DIR
    challenge_dir = path / challenge_id
    if not challenge_dir.is_dir():
        raise KeyError(f"Challenge '{challenge_id}' not found")

    challenge_json_path = challenge_dir / "challenge.json"
    visible_tests_path = challenge_dir / "visible_tests.json"

    if not challenge_json_path.exists() or not visible_tests_path.exists():
        raise KeyError(f"Challenge '{challenge_id}' is missing configuration")

    meta = json.loads(challenge_json_path.read_text(encoding="utf-8"))
    visible_tests = json.loads(visible_tests_path.read_text(encoding="utf-8"))

    return VisibleChallenge(
        id=meta["id"],
        title=meta["title"],
        function_name=meta["function_name"],
        visible_tests=visible_tests,
    )

