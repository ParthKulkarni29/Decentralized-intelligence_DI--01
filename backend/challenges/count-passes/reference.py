"""Reference solution for count-passes.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import List


def count_passes(marks: List[int]) -> int:
    """Return how many marks are 40 or more."""
    count = 0
    for mark in marks:
        if mark >= 40:
            count += 1
    return count
