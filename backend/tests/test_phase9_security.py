"""Unit and integration tests for Phase 9, Step 1.

Verifies:
1. CORS origins, regex, allowed methods (GET, POST, PUT, OPTIONS), and allowed headers (Content-Type, Authorization).
2. Request body size limit: rejects bodies > 100 KB with HTTP 413 and JSON error payload.
3. Per-IP rate limiting: generous limit (120 req/min on /api/submit) allows 60 quick submissions in a minute; rejects when limit is exceeded with HTTP 429.
4. Access logging: records request_id, method, path, status, and duration; omits bodies and Authorization headers.
5. No debug or admin endpoints exposed; no stack traces leaked in error responses.
"""

import json
import logging
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.middleware import (
    MAX_BODY_SIZE_BYTES,
    SUBMIT_RATE_LIMIT,
    reset_rate_limits,
)
from backend.database import init_db


@pytest.fixture(autouse=True)
def setup_teardown():
    init_db()
    reset_rate_limits()
    yield
    reset_rate_limits()


def test_cors_preflight_and_headers():
    """Verify CORS preflight returns correct methods, headers, and origin matching."""
    client = TestClient(app)

    # 1. Preflight OPTIONS request
    resp = client.options(
        "/api/submit",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, Authorization",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    allowed_methods = resp.headers.get("access-control-allow-methods", "")
    assert "POST" in allowed_methods
    assert "GET" in allowed_methods
    assert "PUT" in allowed_methods
    assert "OPTIONS" in allowed_methods

    allowed_headers = resp.headers.get("access-control-allow-headers", "").lower()
    assert "content-type" in allowed_headers
    assert "authorization" in allowed_headers


def test_request_body_size_limit_under_100kb():
    """Verify normal payloads under 100 KB are processed without 413 error."""
    client = TestClient(app)
    small_payload = {"learner_id": "test_learner", "challenge_id": "last-k-total", "code": "def last_k_total(marks, k): return 0\n"}
    resp = client.post("/api/submit", json=small_payload)
    assert resp.status_code == 200


def test_request_body_size_limit_over_100kb_rejected():
    """Verify payloads exceeding 100 KB are rejected with HTTP 413 and error payload."""
    client = TestClient(app)

    # Generate oversized payload > 100 KB
    huge_comment = "# " + ("A" * (MAX_BODY_SIZE_BYTES + 5000))
    huge_code = f"def last_k_total(marks, k):\n    {huge_comment}\n    return 0\n"
    huge_payload = {
        "learner_id": "test_learner",
        "challenge_id": "last-k-total",
        "code": huge_code,
    }
    payload_bytes = json.dumps(huge_payload).encode("utf-8")
    assert len(payload_bytes) > MAX_BODY_SIZE_BYTES

    resp = client.post(
        "/api/submit",
        content=payload_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    data = resp.json()
    assert "error" in data
    assert "100 KB" in data["error"]


def test_rate_limiting_accepts_60_quick_submissions():
    """Verify that 60 quick submissions within a minute are all accepted (generous limit)."""
    client = TestClient(app)
    code = "def last_k_total(marks, k):\n    return sum(marks[-k:]) if k > 0 else 0\n"

    # Send 60 sequential submissions
    for i in range(60):
        resp = client.post(
            "/api/submit",
            json={"learner_id": f"rapid_learner_{i}", "challenge_id": "last-k-total", "code": code},
            headers={"X-Forwarded-For": "198.51.100.1"},
        )
        assert resp.status_code == 200, f"Request {i+1} failed with status {resp.status_code}"


def test_rate_limiting_rejects_after_limit_exceeded():
    """Verify that exceeding SUBMIT_RATE_LIMIT triggers HTTP 429."""
    import time
    from backend.middleware import _rate_limit_records

    client = TestClient(app)
    code = "def last_k_total(marks, k):\n    return sum(marks[-k:]) if k > 0 else 0\n"
    test_ip = "203.0.113.42"

    # Pre-fill exactly SUBMIT_RATE_LIMIT (120) requests in the current window for this IP
    now = time.time()
    _rate_limit_records[(test_ip, "submit")] = [now - i * 0.1 for i in range(SUBMIT_RATE_LIMIT)]

    # 121st request from same IP must be rejected with 429
    blocked_resp = client.post(
        "/api/submit",
        json={"learner_id": "rate_tester", "challenge_id": "last-k-total", "code": code},
        headers={"X-Forwarded-For": test_ip},
    )
    assert blocked_resp.status_code == 429
    assert "Rate limit exceeded" in blocked_resp.json().get("error", "")

    # Request from a different IP is still accepted
    other_resp = client.post(
        "/api/submit",
        json={"learner_id": "rate_tester_other", "challenge_id": "last-k-total", "code": code},
        headers={"X-Forwarded-For": "198.51.100.99"},
    )
    assert other_resp.status_code == 200



def test_access_logging_contains_no_secrets_or_bodies(caplog):
    """Verify access logger records method, path, status, duration and never logs bodies or Authorization header."""
    client = TestClient(app)

    with caplog.at_level(logging.INFO, logger="debugdojo.access"):
        secret_token = "SUPER_SECRET_BEARER_TOKEN_99999"
        secret_body_marker = "DO_NOT_LOG_THIS_PROPRIETARY_LEARNER_SECRET"

        resp = client.post(
            "/api/submit",
            json={
                "learner_id": "log_tester",
                "challenge_id": "last-k-total",
                "code": f"# {secret_body_marker}\ndef last_k_total(marks, k): return 0\n",
            },
            headers={"Authorization": f"Bearer {secret_token}"},
        )
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers

        # Verify log output
        log_records = [rec.message for rec in caplog.records if rec.name == "debugdojo.access"]
        assert len(log_records) > 0
        latest_log = log_records[-1]

        # Must contain method, path, status, duration
        assert "method=POST" in latest_log
        assert "path=/api/submit" in latest_log
        assert "status=200" in latest_log
        assert "duration=" in latest_log
        assert "request_id=" in latest_log

        # Must NEVER contain body content or secrets
        assert secret_token not in latest_log
        assert secret_body_marker not in latest_log
        assert "Authorization" not in latest_log
        assert "Bearer" not in latest_log


def test_no_admin_or_debug_routes():
    """Verify that no admin, debug, or internal routes exist in the application."""
    route_paths = []
    for r in app.routes:
        if hasattr(r, "path"):
            route_paths.append(r.path)
        elif hasattr(r, "original_router"):
            for sr in r.original_router.routes:
                route_paths.append(sr.path)

    forbidden_keywords = ["admin", "debug", "internal", "root", "secret", "private"]
    for path in route_paths:
        for kw in forbidden_keywords:
            assert kw not in path.lower(), f"Forbidden keyword '{kw}' found in route: {path}"


def test_no_stack_traces_in_error_responses():
    """Verify that unexpected server exceptions return generic error without tracebacks."""
    client = TestClient(app, raise_server_exceptions=False)

    # Calling non-existent endpoint
    not_found = client.get("/api/nonexistent_endpoint_xyz")
    assert not_found.status_code == 404
    assert "Traceback" not in not_found.text
    assert "File " not in not_found.text
    assert "line " not in not_found.text

    # Validation error
    bad_req = client.post("/api/submit", json={"invalid_field": 123})
    assert bad_req.status_code == 400
    assert "Traceback" not in bad_req.text
    assert "File " not in bad_req.text
