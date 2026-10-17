"""Red-team attack 2: Recursion bomb."""

def last_k_total(marks, k):
    def bomb(n):
        return bomb(n + 1)
    return bomb(0)
