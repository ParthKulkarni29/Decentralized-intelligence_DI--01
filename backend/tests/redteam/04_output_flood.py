"""Red-team attack 4: Output flood."""

def last_k_total(marks, k):
    for _ in range(50000):
        print("FLOOD_STREAM_OUTPUT_TEST_" * 20)
    return 0
