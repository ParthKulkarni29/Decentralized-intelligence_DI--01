"""Red-team attack 3: Memory bomb."""

def last_k_total(marks, k):
    data = [1] * (1024 * 1024)
    while True:
        data = data + data
    return len(data)
