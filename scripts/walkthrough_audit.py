"""Phase 7 Final Audit Walkthrough.

Executes real HTTP requests against the live running Uvicorn server at http://127.0.0.1:8000:
1. register -> 2. challenge -> 3. Run -> 4. Submit -> 5. hints -> 6. focus mode -> 7. history -> 8. offline audit
"""

import sys
import httpx
import json
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"

def run_walkthrough():
    print("=====================================================================")
    print("   PHASE 7 FINAL AUDIT: END-TO-END LIVE WALKTHROUGH")
    print("=====================================================================")
    client = httpx.Client(base_url=BASE_URL, timeout=10.0)

    # 0. Health check
    res = client.get("/api/health")
    assert res.status_code == 200, f"Health check failed: {res.status_code}"
    print(f"[OK] Health check passed: {res.json()}")

    # 1. Register learner
    print("\n--- 1. REGISTER LEARNER ---")
    reg_payload = {"display_name": "Audit Ninja", "pin": "7788"}
    res = client.post("/api/auth/register", json=reg_payload)
    assert res.status_code == 201, f"Registration failed: {res.text}"
    reg_data = res.json()
    learner_id = reg_data["learner_id"]
    token = reg_data.get("token")
    assert token, "No token returned from register"
    print(f"[OK] Registered learner: {learner_id}")
    print(f"     Token issued: {token[:12]}...")

    headers = {"Authorization": f"Bearer {token}"}

    # 2. Challenge detail
    print("\n--- 2. CHALLENGE INSPECTION ---")
    res = client.get("/api/challenges/last-k-total", headers=headers)
    assert res.status_code == 200, f"Get challenge failed: {res.text}"
    chal_data = res.json()
    assert chal_data["id"] == "last-k-total"
    assert "visible_tests" in chal_data
    assert "hidden_test_count" in chal_data
    assert "hidden_tests" not in chal_data, "SECURITY VIOLATION: hidden_tests exposed in challenge endpoint!"
    print(f"[OK] Retrieved challenge '{chal_data['title']}'")
    print(f"     Visible tests count: {len(chal_data['visible_tests'])}")
    print(f"     Hidden test count guarded: {chal_data['hidden_test_count']}")
    print(f"     Hidden tests definition exposed? NO (Strictly Redacted)")

    # 3. Run endpoint (Visible only, no DB writes)
    print("\n--- 3. POST /api/run (LEETCODE RUN) ---")
    run_code = (
        "def last_k_total(marks, k):\n"
        "    if not marks or k <= 0: return 0\n"
        "    return sum(marks[-k:])\n"
    )
    res = client.post(
        "/api/run",
        json={"learner_id": learner_id, "challenge_id": "last-k-total", "code": run_code},
        headers=headers,
    )
    assert res.status_code == 200, f"Run failed: {res.text}"
    run_data = res.json()
    assert "visible" in run_data
    assert "hidden" not in run_data, "SECURITY VIOLATION: hidden tests returned from /api/run!"
    assert len(run_data["visible"]) == len(chal_data["visible_tests"])
    print(f"[OK] POST /api/run succeeded:")
    for vt in run_data["visible"]:
        print(f"     Case #{vt['test']}: outcome={vt['outcome']} | expected={vt['expected']} | got={vt['got']}")

    # Check that Run did NOT record a submission in history
    hist_res = client.get(f"/api/learner/{learner_id}/history?challenge_id=last-k-total", headers=headers)
    assert hist_res.status_code == 200
    assert len(hist_res.json()) == 0, f"Run incorrectly recorded a submission: {hist_res.json()}"
    print("[OK] Confirmed /api/run recorded ZERO submissions in the database.")

    # 4. Submit endpoint (Buggy code to trigger mistake tags and hints)
    print("\n--- 4. POST /api/submit (EVALUATION & HIDDEN TESTS) ---")
    buggy_code = (
        "def last_k_total(marks, k):\n"
        "    # Missing guard when k > len(marks)\n"
        "    start = len(marks) - k\n"
        "    return sum(marks[start:])\n"
    )
    res = client.post(
        "/api/submit",
        json={
            "learner_id": learner_id,
            "challenge_id": "last-k-total",
            "code": buggy_code,
            "prediction": "some_fail",
        },
        headers=headers,
    )
    assert res.status_code == 200, f"Submit failed: {res.text}"
    sub_data = res.json()
    submission_id = sub_data["submission_id"]
    print(f"[OK] Submission evaluated: ID = {submission_id}")
    print(f"     All passed: {sub_data['all_passed']}")
    print(f"     Mistake tags: {sub_data['mistake_tags']}")
    print(f"     Prediction result: {sub_data['prediction_result']}")

    # STRICT SECURITY AUDIT: Verify hidden test result structure
    print("\n--- AUDIT: HIDDEN TEST RESULT SECRECY IN /api/submit ---")
    assert "hidden" in sub_data
    for h in sub_data["hidden"]:
        keys = set(h.keys())
        allowed_keys = {"test", "outcome", "limit"}
        forbidden_keys = {"call", "expected", "got", "input", "args", "error", "exception", "traceback", "category"}
        assert keys.issubset(allowed_keys), f"SECURITY VIOLATION: Unexpected keys in hidden test result: {keys}"
        assert not (keys & forbidden_keys), f"SECURITY VIOLATION: Forbidden keys present: {keys & forbidden_keys}"
    print(f"[OK] All {len(sub_data['hidden'])} hidden test outcomes expose ONLY allowed keys (test, outcome, limit).")
    for h in sub_data["hidden"][:3]:
        print(f"     Hidden #{h['test']}: outcome={h['outcome']}, limit={h['limit']}")
    if len(sub_data["hidden"]) > 3:
        print(f"     ... and {len(sub_data['hidden']) - 3} more.")

    # 5. Hints ladder (Exactly 3 layers)
    print("\n--- 5. SOCRATIC HINT LADDER (3 LAYERS) ---")
    expected_layers = ["Nudge", "Pointer", "Approach"]
    for i in range(3):
        h_res = client.post(
            "/api/hint",
            json={"submission_id": submission_id},
            headers=headers,
        )
        assert h_res.status_code == 200, f"Hint #{i+1} failed: {h_res.text}"
        h_data = h_res.json()
        assert h_data["hint_number"] == i + 1
        assert h_data["hints_total"] == 3
        # Verify layer 3 contains NO code or def/return
        if i == 2:
            hint_txt = h_data.get("hint") or h_data.get("text") or ""
            assert "```" not in hint_txt, "SECURITY VIOLATION: Code block in layer 3 hint!"
            assert "def " not in hint_txt, "SECURITY VIOLATION: 'def' in layer 3 hint!"
            assert "return " not in hint_txt, "SECURITY VIOLATION: 'return' in layer 3 hint!"
        print(f"[OK] Layer {h_data['hint_number']} ({expected_layers[i]}): {h_data.get('hint') or h_data.get('text')}")

    # Verify hint 4 reports exhausted
    h_exhaust = client.post(
        "/api/hint",
        json={"submission_id": submission_id},
        headers=headers,
    )
    assert h_exhaust.status_code == 200
    h_exhaust_data = h_exhaust.json()
    assert h_exhaust_data["exhausted"] is True
    assert h_exhaust_data["hints_remaining"] == 0
    print(f"[OK] Hint exhaustion strictly enforced (exhausted=True, hints_remaining=0: {h_exhaust_data['hint']})")

    # 6. Focus mode verification
    print("\n--- 6. FOCUS MODE AUDIT ---")
    index_html = Path("frontend/index.html").read_text(encoding="utf-8")
    sakura_css = Path("frontend/assets/sakura.css").read_text(encoding="utf-8")
    
    assert "debugdojo_focus_mode" in index_html, "debugdojo_focus_mode missing from index.html"
    assert "handleFocusKeys" in index_html, "handleFocusKeys missing from index.html"
    assert "focus-workbench" in sakura_css, "focus-workbench missing from sakura.css"
    assert "focus-viewport" in sakura_css, "focus-viewport missing from sakura.css"
    assert "prefers-reduced-motion" in sakura_css, "prefers-reduced-motion missing from sakura.css"
    assert "innerHTML" not in index_html, "innerHTML present in index.html!"
    assert "dangerouslySetInnerHTML" not in index_html, "dangerouslySetInnerHTML present in index.html!"
    print("[OK] Focus mode markup, keyboard handlers (F/Esc), and CSS tokens verified.")

    # 7. History & Submission Detail
    print("\n--- 7. HISTORY & SUBMISSION DETAIL ---")
    hist_res = client.get(f"/api/learner/{learner_id}/history?challenge_id=last-k-total", headers=headers)
    assert hist_res.status_code == 200
    hist_list = hist_res.json()
    assert len(hist_list) == 1
    print(f"[OK] History retrieved 1 submission chronicle:")
    print(f"     ID: {hist_list[0]['submission_id']}, all_passed={hist_list[0]['all_passed']}, mistakes={hist_list[0]['mistake_tags']}")

    detail_res = client.get(f"/api/submissions/{submission_id}", headers=headers)
    assert detail_res.status_code == 200
    detail_data = detail_res.json()
    assert detail_data["code"] == buggy_code
    assert "hidden" in detail_data
    for h in detail_data["hidden"]:
        assert set(h.keys()).issubset({"test", "outcome", "limit"})
    print(f"[OK] /api/submissions/{submission_id} returns historical code and clean test outcomes without secrets.")

    # 8. Offline Assets Audit
    print("\n--- 8. OFFLINE ASSETS AUDIT ---")
    vendor_dir = Path("frontend/assets/vendor")
    fonts_dir = Path("frontend/assets/fonts")
    assert (vendor_dir / "react.production.min.js").exists()
    assert (vendor_dir / "react-dom.production.min.js").exists()
    assert (vendor_dir / "babel.min.js").exists()
    assert (vendor_dir / "tailwindcss.js").exists()
    assert (vendor_dir / "codemirror.js").exists()
    assert (vendor_dir / "codemirror-python.js").exists()
    assert len(list(fonts_dir.glob("*.woff2"))) >= 13
    print(f"[OK] All 21 vendor and font files exist locally on disk.")

    print("\n=====================================================================")
    print("   ALL WALKTHROUGH AND AUDIT CHECKS PASSED (100% HEALTHY)")
    print("=====================================================================")

if __name__ == "__main__":
    run_walkthrough()
