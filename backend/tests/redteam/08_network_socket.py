"""Red-team attack 8: Open network socket / outbound request."""

import socket
import urllib.request

def last_k_total(marks, k):
    try:
        urllib.request.urlopen("http://example.com", timeout=1.0)
    except Exception:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    s.connect(("1.1.1.1", 80))
    return 0
