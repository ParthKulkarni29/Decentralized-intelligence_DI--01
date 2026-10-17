"""Reference solution for last-k-total.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import List


def last_k_total(marks: List[int], k: int) -> int:
    """Return the total of the last k marks, or of all marks if k >= len(marks).

    If k is 0 or marks is empty, returns 0.
    """
    if k <= 0 or not marks:
        return 0
    if k >= len(marks):
        return sum(marks)
    return sum(marks[-k:])
