"""Reference solution for safe-first-element.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import Any, List


def safe_first_element(items: List[Any], default: Any) -> Any:
    """Return the first element if items is non-empty, otherwise default."""
    if not items:
        return default
    return items[0]
