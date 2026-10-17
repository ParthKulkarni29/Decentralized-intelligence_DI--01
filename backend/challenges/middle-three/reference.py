"""Reference solution for middle-three.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import Any, List


def middle_three(items: List[Any]) -> List[Any]:
    """Return the middle three items, or the whole list if len(items) < 3."""
    if len(items) < 3:
        return list(items)
    mid = len(items) // 2
    return items[mid - 1 : mid + 2]
