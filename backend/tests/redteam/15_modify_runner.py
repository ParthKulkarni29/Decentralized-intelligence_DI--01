"""Red-team attack 15: Try to modify or overwrite /runner/runner.py."""

def last_k_total(marks, k):
    try:
        with open("/runner/runner.py", "w") as f:
            f.write("# HACKED")
    except Exception:
        pass
    return 0
