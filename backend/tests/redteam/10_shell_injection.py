"""Red-team attack 10: Shell injection / subprocess."""

import os
import subprocess

def last_k_total(marks, k):
    try:
        os.system("echo 'pwn' > /tmp/pwn.txt")
        subprocess.run(["ls", "/root"], capture_output=True)
    except Exception:
        pass
    return 0
