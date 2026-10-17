"""In-process reference solution loader for trusted answer computation.

SECURITY & ARCHITECTURE NOTE:
Reference solutions authored in backend/challenges/{challenge_id}/reference.py
are 100% trusted, static, author-verified Python code with no arbitrary user input.
Running the reference solution in-process on the host is safe, deterministic,
and avoids container startup overhead for counterexample evaluation.

Learner code is NEVER executed on the host; it runs strictly within the Docker sandbox.
"""

import copy
import importlib.util
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_reference_function(challenge_id: str, function_name: str) -> Callable[..., Any]:
    """Dynamically load and return the reference solution function for a challenge."""
    ref_path = PROJECT_ROOT / "backend" / "challenges" / challenge_id / "reference.py"
    if not ref_path.exists():
        raise FileNotFoundError(f"Reference file not found for challenge: {challenge_id}")

    module_name = f"ref_{challenge_id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, str(ref_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load reference spec from {ref_path}")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fn = getattr(mod, function_name, None)
    if fn is None:
        raise AttributeError(f"Function {function_name} not found in {ref_path}")

    return fn


def execute_reference(challenge_id: str, function_name: str, args: list[Any]) -> Any:
    """Execute trusted reference solution on deep-copied args in-process."""
    fn = get_reference_function(challenge_id, function_name)
    safe_args = copy.deepcopy(args)
    return fn(*safe_args)
