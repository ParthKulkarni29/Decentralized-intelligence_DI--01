"""Tests for optional Google and phone-number sign-in.

Covers: provider discovery, Google ID-token checks (mocked network), phone one-time codes
(single use, expiry, attempt and send limits), identity-linked accounts being protected by
their session token, and the privacy rule that raw identifiers are never stored.
"""

import io
import json
from pathlib import Path
import sqlite3
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import identity
from backend.auth import _LOGIN_ATTEMPTS
from backend.database import init_db
from backend.main import app

PHONE = "+919876543210"


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path: Path, monkeypatch):
    """Isolated DB, clean in-memory state, and a captured (never logged) one-time code."""
    db = tmp_path / "test_identity.db"
    init_db(str(db))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("IDENTITY_PEPPER", raising=False)
    monkeypatch.delenv("PHONE_OTP_DELIVERY", raising=False)
    _LOGIN_ATTEMPTS.clear()
    identity.reset_otp_state()
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(identity, "_deliver_code", lambda phone, code: sent.append((phone, code)))
    return {"db": str(db), "sent": sent}


def _client() -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- providers

def test_providers_google_disabled_by_default():
    data = _client().get("/api/auth/providers").json()
    assert data["google"] == {"enabled": False, "client_id": None}
    assert data["phone"]["enabled"] is True
    assert data["phone"]["dev_mode"] is True
    assert data["pin"]["enabled"] is True


def test_providers_google_enabled_when_client_id_set(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc.apps.googleusercontent.com")
    data = _client().get("/api/auth/providers").json()
    assert data["google"] == {"enabled": True, "client_id": "abc.apps.googleusercontent.com"}


# --------------------------------------------------------------------------- Google

def test_google_login_503_when_not_configured():
    res = _client().post("/api/auth/google", json={"credential": "x" * 40})
    assert res.status_code == 503
    assert "not configured" in res.json()["error"]


def test_google_login_creates_then_reuses_learner(monkeypatch):
    monkeypatch.setattr(identity, "verify_google_credential", lambda cred: "google-sub-1")
    client = _client()
    first = client.post("/api/auth/google", json={"credential": "x" * 40, "display_name": "Hana Dev"})
    assert first.status_code == 200
    body = first.json()
    assert body["is_new"] is True and body["learner_id"] == "hana_dev" and body["token"]

    second = client.post("/api/auth/google", json={"credential": "y" * 40})
    assert second.json()["learner_id"] == "hana_dev"
    assert second.json()["is_new"] is False


def test_identity_learner_is_protected_by_token(monkeypatch):
    monkeypatch.setattr(identity, "verify_google_credential", lambda cred: "google-sub-2")
    client = _client()
    body = client.post("/api/auth/google", json={"credential": "x" * 40, "display_name": "Mika"}).json()
    lid, token = body["learner_id"], body["token"]

    assert client.get(f"/api/learner/{lid}").status_code == 401
    ok = client.get(f"/api/learner/{lid}", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200

    other = client.post("/api/auth/register", json={"display_name": "Someone Else", "pin": "1234"}).json()
    cross = client.get(f"/api/learner/{lid}", headers={"Authorization": f"Bearer {other['token']}"})
    assert cross.status_code == 403


def test_plain_learners_stay_open_after_identity_feature():
    client = _client()
    assert client.get("/api/learner/zen_coder_409").status_code == 200


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_tokeninfo(monkeypatch, payload: dict):
    monkeypatch.setattr(
        identity.urllib.request, "urlopen", lambda url, timeout=0: _FakeResponse(json.dumps(payload).encode())
    )


def _good_info(**over) -> dict:
    info = {
        "aud": "cid", "iss": "https://accounts.google.com", "sub": "123",
        "exp": str(int(time.time()) + 600), "email_verified": "true",
    }
    info.update(over)
    return info


@pytest.mark.parametrize(
    "override",
    [{"aud": "someone-elses-client"}, {"iss": "evil.example"}, {"exp": "1"}, {"email_verified": "false"}, {"sub": ""}],
)
def test_verify_google_credential_rejects_bad_tokens(monkeypatch, override):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    _fake_tokeninfo(monkeypatch, _good_info(**override))
    with pytest.raises(HTTPException) as exc:
        identity.verify_google_credential("t" * 40)
    assert exc.value.status_code == 401


def test_verify_google_credential_accepts_valid_token(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    _fake_tokeninfo(monkeypatch, _good_info())
    assert identity.verify_google_credential("t" * 40) == "123"


# --------------------------------------------------------------------------- phone

def test_phone_flow_creates_learner_and_code_is_single_use(setup_test_db):
    client, sent = _client(), setup_test_db["sent"]
    res = client.post("/api/auth/phone/request", json={"phone": "+91 98765-43210"})
    assert res.status_code == 200 and res.json()["sent"] is True
    assert "code" not in json.dumps(res.json())  # the code must never be in the API response
    phone, code = sent[-1]
    assert phone == PHONE

    ok = client.post("/api/auth/phone/verify", json={"phone": PHONE, "code": code, "display_name": "Ravi"})
    assert ok.status_code == 200 and ok.json()["is_new"] is True and ok.json()["token"]

    reuse = client.post("/api/auth/phone/verify", json={"phone": PHONE, "code": code})
    assert reuse.status_code == 401


def test_phone_returning_user_gets_same_learner(setup_test_db):
    client, sent = _client(), setup_test_db["sent"]
    client.post("/api/auth/phone/request", json={"phone": PHONE})
    first = client.post("/api/auth/phone/verify", json={"phone": PHONE, "code": sent[-1][1], "display_name": "Ravi"}).json()
    client.post("/api/auth/phone/request", json={"phone": PHONE})
    again = client.post("/api/auth/phone/verify", json={"phone": PHONE, "code": sent[-1][1]}).json()
    assert again["learner_id"] == first["learner_id"] and again["is_new"] is False


def test_phone_wrong_code_and_attempt_lockout(setup_test_db):
    client, sent = _client(), setup_test_db["sent"]
    client.post("/api/auth/phone/request", json={"phone": PHONE})
    real = sent[-1][1]
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(identity.MAX_OTP_ATTEMPTS):
        assert client.post("/api/auth/phone/verify", json={"phone": PHONE, "code": wrong}).status_code == 401
    locked = client.post("/api/auth/phone/verify", json={"phone": PHONE, "code": real})
    assert locked.status_code == 429


def test_phone_code_expires(setup_test_db, monkeypatch):
    client, sent = _client(), setup_test_db["sent"]
    client.post("/api/auth/phone/request", json={"phone": PHONE})
    real_time = time.time
    monkeypatch.setattr(identity.time, "time", lambda: real_time() + identity.OTP_TTL_SECONDS + 5)
    res = client.post("/api/auth/phone/verify", json={"phone": PHONE, "code": sent[-1][1]})
    assert res.status_code == 401


def test_phone_send_rate_limit(setup_test_db):
    client = _client()
    for _ in range(identity.MAX_OTP_SENDS):
        assert client.post("/api/auth/phone/request", json={"phone": PHONE}).status_code == 200
    assert client.post("/api/auth/phone/request", json={"phone": PHONE}).status_code == 429


@pytest.mark.parametrize("bad", ["12345678", "+0123456789", "+12", "abcdefghij", "+91 98x65"])
def test_phone_format_validation(bad):
    res = _client().post("/api/auth/phone/request", json={"phone": bad})
    assert res.status_code == 400


def test_phone_verify_without_request_fails():
    res = _client().post("/api/auth/phone/verify", json={"phone": PHONE, "code": "123456"})
    assert res.status_code == 401


# --------------------------------------------------------------------------- privacy

def test_raw_identifiers_are_never_stored(setup_test_db, monkeypatch):
    client, sent = _client(), setup_test_db["sent"]
    monkeypatch.setattr(identity, "verify_google_credential", lambda cred: "raw-google-sub-xyz")
    client.post("/api/auth/google", json={"credential": "x" * 40, "display_name": "Priv"})
    client.post("/api/auth/phone/request", json={"phone": PHONE})
    client.post("/api/auth/phone/verify", json={"phone": PHONE, "code": sent[-1][1], "display_name": "Priv2"})

    conn = sqlite3.connect(setup_test_db["db"])
    dump = "\n".join(conn.iterdump())
    conn.close()
    assert "raw-google-sub-xyz" not in dump
    assert PHONE not in dump and PHONE[1:] not in dump
    rows = sqlite3.connect(setup_test_db["db"]).execute("SELECT provider FROM identities").fetchall()
    assert sorted(r[0] for r in rows) == ["google", "phone"]


def test_env_pepper_changes_hash(monkeypatch, setup_test_db):
    conn = sqlite3.connect(setup_test_db["db"])
    conn.row_factory = sqlite3.Row
    monkeypatch.setenv("IDENTITY_PEPPER", "pepper-one")
    one = identity.hash_identifier(conn, "phone", PHONE)
    monkeypatch.setenv("IDENTITY_PEPPER", "pepper-two")
    two = identity.hash_identifier(conn, "phone", PHONE)
    conn.close()
    assert one != two and PHONE not in one
