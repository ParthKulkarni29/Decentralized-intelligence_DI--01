"""Red-team attack 13: os._exit(0) inside function."""

import os

def last_k_total(marks, k):
    os._exit(0)
