"""Reference solution for first-n-squares.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import List


def first_n_squares(n: int) -> List[int]:
    """Return [1, 4, 9, ..., n*n], or [] when n is 0."""
    if n <= 0:
        return []
    return [i * i for i in range(1, n + 1)]
