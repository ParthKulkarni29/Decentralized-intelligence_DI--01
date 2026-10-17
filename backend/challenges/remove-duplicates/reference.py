"""Reference solution for remove-duplicates.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import Any, List


def remove_duplicates(items: List[Any]) -> List[Any]:
    """Return a new list with duplicates removed, preserving first-seen order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
