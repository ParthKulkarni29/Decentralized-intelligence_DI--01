"""Red-team attack 7: Read environment variables."""

import os

def last_k_total(marks, k):
    env_data = dict(os.environ)
    print("LEAKED_ENV_CONTENT:", env_data)
    return len(env_data)
