"""Reference solution for running-total.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import List


def running_total(numbers: List[int]) -> List[int]:
    """Return a new list where each element is the cumulative sum up to that index."""
    result = []
    total = 0
    for n in numbers:
        total += n
        result.append(total)
    return result
