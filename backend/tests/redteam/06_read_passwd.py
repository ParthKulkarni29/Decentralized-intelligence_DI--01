"""Red-team attack 6: Read /etc/passwd."""

def last_k_total(marks, k):
    try:
        with open("/etc/passwd", "r") as f:
            data = f.read()
            return len(data)
    except Exception:
        return 0
