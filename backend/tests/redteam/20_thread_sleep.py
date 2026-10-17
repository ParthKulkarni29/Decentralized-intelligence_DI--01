"""Red-team attack 20: Sleep in thread and exit."""

import threading
import time

def last_k_total(marks, k):
    def background():
        time.sleep(10)
    t = threading.Thread(target=background, daemon=True)
    t.start()
    return 0
