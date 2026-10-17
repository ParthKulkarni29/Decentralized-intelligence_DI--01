"""Test script verifying the real backend for all endpoints consumed by the LeetCode Workbench.

Checks:
1. GET /api/challenges/last-k-total
2. POST /api/run (visible tests only, 0 DB writes)
3. POST /api/submit (returns visible + hidden outcomes, explanation, mistake_tags, prediction_result, arena)
4. POST /api/hint (socratic hint)
5. GET /api/learner/rohan/history?challenge_id=last-k-total (submission list)
6. GET /api/submissions/{id} (read-only code inspection)
7. Security check: Zero leaked hidden data in visible responses.
"""

import sys
import os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_workbench_flow():
    print("=== Testing Real Backend for LeetCode Workbench ===")
    
    # 1. Get challenge details
    resp = client.get("/api/challenges/last-k-total")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    ch = resp.json()
    print(f"1. GET /api/challenges/last-k-total -> 200 OK | Title: {ch['title']} | Visible tests: {len(ch['visible_tests'])}")
    assert "description" in ch
    assert "visible_tests" in ch
    assert "starter_code" in ch
    assert "hidden_test_count" in ch
    assert "hidden_tests" not in ch, "CRITICAL SECURITY: hidden_tests must not be in public challenge"

    # 2. POST /api/run (visible only)
    buggy_code = (
        "def last_k_total(marks, k):\n"
        "    start = len(marks) - k\n"
        "    return sum(marks[start:])\n"
    )
    resp = client.post("/api/run", json={
        "learner_id": "workbench_tester",
        "challenge_id": "last-k-total",
        "code": buggy_code,
    })
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    run_data = resp.json()
    print(f"2. POST /api/run -> 200 OK | Visible results: {len(run_data['visible'])}")
    assert "visible" in run_data
    assert "hidden" not in run_data, "CRITICAL: /api/run must not contain hidden results"
    for vt in run_data["visible"]:
        assert "call" in vt
        assert "expected" in vt
        assert "outcome" in vt

    # 3. POST /api/submit (visible + hidden)
    resp = client.post("/api/submit", json={
        "learner_id": "workbench_tester",
        "challenge_id": "last-k-total",
        "code": buggy_code,
        "prediction": "some_fail",
    })
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    sub_data = resp.json()
    sub_id = sub_data["submission_id"]
    print(f"3. POST /api/submit -> 200 OK | Submission ID: {sub_id} | all_passed: {sub_data['all_passed']}")
    assert "visible" in sub_data
    assert "hidden" in sub_data
    assert "explanation" in sub_data
    assert "mistake_tags" in sub_data
    assert "prediction_result" in sub_data
    assert "arena" in sub_data
    print(f"   Mistake tags: {sub_data['mistake_tags']}")
    print(f"   Prediction result: {sub_data['prediction_result']}")
    print(f"   Arena defeated: {sub_data['arena']['defeated']}, remaining: {sub_data['arena']['remaining']}")

    # Security check on hidden tests
    for ht in sub_data["hidden"]:
        assert set(ht.keys()).issubset({"test", "outcome", "limit"}), f"Leaked hidden test keys: {ht.keys()}"

    # 4. POST /api/hint
    resp = client.post("/api/hint", json={"submission_id": sub_id})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    hint_data = resp.json()
    print(f"4. POST /api/hint -> 200 OK | Hint #{hint_data['hint_number']}: {hint_data['hint'][:50]}...")
    assert "hint" in hint_data
    assert hint_data["hint_number"] == 1

    # 5. GET /api/learner/{id}/history?challenge_id=...
    resp = client.get("/api/learner/workbench_tester/history?challenge_id=last-k-total")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    history_items = resp.json()
    print(f"5. GET /api/learner/workbench_tester/history?challenge_id=last-k-total -> 200 OK | Count: {len(history_items)}")
    assert len(history_items) >= 1
    assert history_items[0]["submission_id"] == sub_id

    # 6. GET /api/submissions/{id} (read-only code inspection)
    resp = client.get(f"/api/submissions/{sub_id}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    detail_data = resp.json()
    print(f"6. GET /api/submissions/{sub_id} -> 200 OK | Code length: {len(detail_data['code'])} chars")
    assert detail_data["code"] == buggy_code
    assert detail_data["challenge_id"] == "last-k-total"

    # Security check on submission detail
    for ht in detail_data.get("hidden", []):
        assert set(ht.keys()).issubset({"test", "outcome", "limit"}), "Leaked hidden test keys in submission detail"

    print("\nPASS: All real backend endpoints consumed by LeetCode Workbench verified successfully!")

if __name__ == "__main__":
    test_workbench_flow()
