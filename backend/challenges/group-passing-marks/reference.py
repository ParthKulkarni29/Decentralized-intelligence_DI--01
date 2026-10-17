"""Reference solution for group-passing-marks.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import Dict, List


def group_passing_marks(marks: List[int]) -> Dict[str, List[int]]:
    """Group marks into pass (>= 40) and fail (< 40) lists, preserving order."""
    passing: List[int] = []
    failing: List[int] = []
    for mark in marks:
        if mark >= 40:
            passing.append(mark)
        else:
            failing.append(mark)
    return {"pass": passing, "fail": failing}
