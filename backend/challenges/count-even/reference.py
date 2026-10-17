"""Reference solution for count-even.

Trusted verification code only; NEVER exposed to the frontend or learner.
"""

from typing import List


def count_even(numbers: List[int]) -> int:
    """Return the count of even integers in the numbers list."""
    count = 0
    for n in numbers:
        if n % 2 == 0:
            count += 1
    return count
