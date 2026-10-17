"""Reference solution for rotate-list.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import Any, List


def rotate_list(items: List[Any], k: int) -> List[Any]:
    """Rotate a list to the right by k steps, wrapping around without mutating input."""
    if not items or k == 0:
        return list(items)
    eff_k = k % len(items)
    if eff_k == 0:
        return list(items)
    return list(items[-eff_k:]) + list(items[:-eff_k])
