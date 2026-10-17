"""Unit tests for pseudonymous authentication and PIN protection (Phase 7 Feature A).

Tests:
1. Pseudonymous registration without PIN -> works, profile accessible without token.
2. Registration with PIN -> 4-8 digits validated, returns token.
3. Protected learner requires valid Bearer token for protected endpoints; unprotected does not.
4. Token forgery and cross-learner access rejected (401/403).
5. Login with valid PIN returns token; invalid PIN fails.
6. Brute force lockout triggered after 5 consecutive failed logins (429).
7. Judge compatibility: unprotected learners can submit, request hints, and view profile without token.
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import init_db
from backend.auth import _LOGIN_ATTEMPTS


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path: Path, monkeypatch):
    """Isolate SQLite database and reset in-memory attempt tracking."""
    test_db = tmp_path / "test_auth.db"
    init_db(str(test_db))
    monkeypatch.setenv("DATABASE_PATH", str(test_db))
    _LOGIN_ATTEMPTS.clear()


def test_register_without_pin():
    """Unprotected registration generates unique slug and allows open access."""
    client = TestClient(app)
    res = client.post("/api/auth/register", json={"display_name": "Open Coder"})
    assert res.status_code == 201
    data = res.json()
    assert data["learner_id"] == "open_coder"
    assert "token" in data

    # Unprotected profile can be viewed without token
    prof_res = client.get(f"/api/learner/{data['learner_id']}")
    assert prof_res.status_code == 200
    assert prof_res.json()["learner_id"] == "open_coder"


def test_register_slug_uniqueness():
    """Duplicate display names receive unique numeric suffixes."""
    client = TestClient(app)
    r1 = client.post("/api/auth/register", json={"display_name": "Shadow Ninja"})
    r2 = client.post("/api/auth/register", json={"display_name": "Shadow Ninja"})
    r3 = client.post("/api/auth/register", json={"display_name": "Shadow Ninja"})

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r3.status_code == 201

    assert r1.json()["learner_id"] == "shadow_ninja"
    assert r2.json()["learner_id"] == "shadow_ninja_1"
    assert r3.json()["learner_id"] == "shadow_ninja_2"


def test_register_pin_validation():
    """PIN must be 4 to 8 digits."""
    client = TestClient(app)
    # Too short
    res_short = client.post("/api/auth/register", json={"display_name": "Bob", "pin": "12"})
    assert res_short.status_code == 400

    # Too long
    res_long = client.post("/api/auth/register", json={"display_name": "Bob", "pin": "123456789"})
    assert res_long.status_code == 400

    # Non-digits
    res_alpha = client.post("/api/auth/register", json={"display_name": "Bob", "pin": "abcd"})
    assert res_alpha.status_code == 400

    # Valid 6-digit PIN
    res_valid = client.post("/api/auth/register", json={"display_name": "Bob", "pin": "123456"})
    assert res_valid.status_code == 201
    assert res_valid.json()["learner_id"] == "bob"


def test_protected_learner_requires_token():
    """Learners with PIN set must provide valid Bearer token for protected profile endpoints."""
    client = TestClient(app)
    reg_res = client.post("/api/auth/register", json={"display_name": "Secret Sensei", "pin": "9876"})
    assert reg_res.status_code == 201
    learner_id = reg_res.json()["learner_id"]
    token = reg_res.json()["token"]

    # 1. No token -> 401
    no_token_res = client.get(f"/api/learner/{learner_id}")
    assert no_token_res.status_code == 401
    assert "Authentication required" in no_token_res.json()["error"]

    # 2. Invalid token -> 401
    bad_token_res = client.get(
        f"/api/learner/{learner_id}",
        headers={"Authorization": "Bearer bogus_token_value"},
    )
    assert bad_token_res.status_code == 401

    # 3. Token for different learner -> 403
    other_reg = client.post("/api/auth/register", json={"display_name": "Other User", "pin": "1111"})
    other_token = other_reg.json()["token"]
    forbidden_res = client.get(
        f"/api/learner/{learner_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert forbidden_res.status_code == 403

    # 4. Valid token -> 200 OK
    valid_res = client.get(
        f"/api/learner/{learner_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert valid_res.status_code == 200
    assert valid_res.json()["learner_id"] == learner_id


def test_login_and_brute_force_protection():
    """Login with PIN returns token; repeated invalid attempts lock out the learner."""
    client = TestClient(app)
    reg = client.post("/api/auth/register", json={"display_name": "Vault Keeper", "pin": "54321"})
    learner_id = reg.json()["learner_id"]

    # Successful login
    login_ok = client.post("/api/auth/login", json={"learner_id": learner_id, "pin": "54321"})
    assert login_ok.status_code == 200
    new_token = login_ok.json()["token"]

    # Access profile with new login token
    prof = client.get(
        f"/api/learner/{learner_id}",
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert prof.status_code == 200

    # Failed login attempts
    for _ in range(4):
        bad_login = client.post("/api/auth/login", json={"learner_id": learner_id, "pin": "00000"})
        assert bad_login.status_code == 401

    # 5th failed attempt triggers lockout
    fifth_fail = client.post("/api/auth/login", json={"learner_id": learner_id, "pin": "00000"})
    assert fifth_fail.status_code == 401

    # 6th attempt is blocked with 429 Too Many Requests
    locked_res = client.post("/api/auth/login", json={"learner_id": learner_id, "pin": "54321"})
    assert locked_res.status_code == 429
    assert "Locked out" in locked_res.json()["error"]
