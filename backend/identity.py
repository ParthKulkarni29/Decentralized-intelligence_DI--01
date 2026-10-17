"""Optional Google and phone-number sign-in for Debug Dojo.

PRIVACY DESIGN (extends the pseudonymous model in backend/auth.py):
- The raw Google account id, e-mail address and phone number are NEVER stored.
- Only HMAC-SHA256(pepper, "<provider>:<identifier>") is written to the ``identities`` table,
  linking that opaque hash to a pseudonymous learner slug.
- A learner linked to an identity is *protected*: a valid Bearer session token is required
  for the protected endpoints (see ``verify_learner_token`` in backend/auth.py).
- One-time phone codes live only in process memory, stored as HMAC hashes, expire after
  five minutes, and are limited in both sends and verification attempts.

CONFIGURATION (environment variables, normally set in backend/.env):
- ``GOOGLE_CLIENT_ID``      OAuth 2.0 Web client id. Google sign-in is disabled when unset.
- ``GOOGLE_TOKENINFO_URL``  Override for tests only; defaults to Google's tokeninfo endpoint.
- ``IDENTITY_PEPPER``       Secret used to hash identifiers. If unset, a random pepper is created
                            once and kept in the ``app_meta`` table (development convenience).
- ``PHONE_OTP_DELIVERY``    ``console`` (default; DEV ONLY, the code is written to the server log)
                            or ``twilio`` (needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM).
"""

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from typing import Optional
import urllib.error
import urllib.parse
import urllib.request

from fastapi import HTTPException, status

from backend.auth import create_session, generate_learner_id

logger = logging.getLogger("debugdojo.identity")

PHONE_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")
OTP_LENGTH = 6
OTP_TTL_SECONDS = 300.0
MAX_OTP_ATTEMPTS = 5
MAX_OTP_SENDS = 3
OTP_SEND_WINDOW_SECONDS = 600.0

# phone_hash -> {"code_hash": str, "expires": float, "attempts": int, "sends": list[float]}
_OTP_STORE: dict[str, dict] = {}


def reset_otp_state() -> None:
    """Clear in-memory one-time-code state (used by tests)."""
    _OTP_STORE.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- hashing

def get_pepper(conn: sqlite3.Connection) -> bytes:
    """Return the secret pepper used to hash identifiers.

    WHY: Phone numbers have tiny entropy, so an unkeyed hash could be reversed by brute force
    if the database leaked. A keyed hash is useless without the pepper.
    """
    env_value = os.getenv("IDENTITY_PEPPER", "").strip()
    if env_value:
        return env_value.encode("utf-8")
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'identity_pepper'").fetchone()
    if row:
        return row["value"].encode("utf-8")
    generated = secrets.token_hex(32)
    conn.execute("INSERT OR IGNORE INTO app_meta (key, value) VALUES ('identity_pepper', ?)", (generated,))
    conn.commit()
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'identity_pepper'").fetchone()
    return row["value"].encode("utf-8")


def hash_identifier(conn: sqlite3.Connection, provider: str, identifier: str) -> str:
    """Keyed hash of ``provider:identifier`` suitable for storage."""
    return hmac.new(get_pepper(conn), f"{provider}:{identifier}".encode("utf-8"), hashlib.sha256).hexdigest()


def _hash_otp(conn: sqlite3.Connection, phone_hash: str, code: str) -> str:
    return hmac.new(get_pepper(conn), f"otp:{phone_hash}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def has_identity(conn: sqlite3.Connection, learner_id: str) -> bool:
    """Whether the learner was created through (or linked to) Google/phone sign-in."""
    row = conn.execute("SELECT 1 FROM identities WHERE learner_id = ? LIMIT 1", (learner_id,)).fetchone()
    return row is not None


# --------------------------------------------------------------------------- providers

def google_client_id() -> str:
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def otp_delivery_mode() -> str:
    mode = os.getenv("PHONE_OTP_DELIVERY", "console").strip().lower()
    return mode if mode in ("console", "twilio") else "console"


def provider_info() -> dict:
    """Public description of which sign-in methods this server offers (no secrets)."""
    client_id = google_client_id()
    mode = otp_delivery_mode()
    return {
        "pin": {"enabled": True},
        "google": {"enabled": bool(client_id), "client_id": client_id or None},
        "phone": {"enabled": True, "delivery": mode, "dev_mode": mode == "console"},
    }


# --------------------------------------------------------------------------- Google

def verify_google_credential(credential: str) -> str:
    """Validate a Google ID token and return its stable ``sub`` (account id).

    Uses Google's documented ``tokeninfo`` endpoint so no extra dependency is needed, then
    checks audience, issuer, expiry and verified e-mail ourselves.
    """
    client_id = google_client_id()
    if not client_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is not configured on this server.")

    base = os.getenv("GOOGLE_TOKENINFO_URL", "https://oauth2.googleapis.com/tokeninfo")
    url = f"{base}?{urllib.parse.urlencode({'id_token': credential})}"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in could not be verified.")
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not reach Google to verify sign-in.")

    try:
        expired = int(info.get("exp", 0)) <= int(time.time())
    except (TypeError, ValueError):
        expired = True
    email_ok = str(info.get("email_verified", "")).lower() == "true"
    issuer_ok = info.get("iss") in ("accounts.google.com", "https://accounts.google.com")
    sub = info.get("sub")
    if info.get("aud") != client_id or not issuer_ok or expired or not email_ok or not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in could not be verified.")
    return str(sub)


# --------------------------------------------------------------------------- phone OTP

def normalize_phone(raw: str) -> str:
    """Return E.164 (+<country><number>) or raise 400."""
    cleaned = re.sub(r"[\s\-().]", "", raw or "")
    if not PHONE_REGEX.match(cleaned):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the phone number in international format, e.g. +919876543210.")
    return cleaned


def _deliver_code(phone: str, code: str) -> None:
    mode = otp_delivery_mode()
    if mode == "console":
        # DEV ONLY: lets a developer sign in without an SMS provider. The code never reaches the API response.
        logger.warning("DEV OTP for %s***%s is %s (valid 5 min)", phone[:3], phone[-2:], code)
        return

    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    sender = os.getenv("TWILIO_FROM", "").strip()
    if not (sid and auth_token and sender):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "SMS delivery is not configured on this server.")
    body = urllib.parse.urlencode({"To": phone, "From": sender, "Body": f"Your Debug Dojo code is {code}"}).encode()
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=body, method="POST"
    )
    basic = base64.b64encode(f"{sid}:{auth_token}".encode()).decode()
    request.add_header("Authorization", f"Basic {basic}")
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except (urllib.error.URLError, TimeoutError):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not send the verification code. Please try again.")


def request_phone_code(conn: sqlite3.Connection, raw_phone: str) -> dict:
    """Create and deliver a one-time code. Always answers the same way for known/unknown numbers."""
    phone = normalize_phone(raw_phone)
    phone_hash = hash_identifier(conn, "phone", phone)
    now = time.time()
    record = _OTP_STORE.setdefault(phone_hash, {"code_hash": "", "expires": 0.0, "attempts": 0, "sends": []})
    record["sends"] = [t for t in record["sends"] if now - t < OTP_SEND_WINDOW_SECONDS]
    if len(record["sends"]) >= MAX_OTP_SENDS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many codes requested. Please wait a few minutes.")

    code = "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))
    _deliver_code(phone, code)
    record.update(code_hash=_hash_otp(conn, phone_hash, code), expires=now + OTP_TTL_SECONDS, attempts=0)
    record["sends"].append(now)
    return {"sent": True, "expires_in": int(OTP_TTL_SECONDS), "delivery": otp_delivery_mode()}


def verify_phone_code(conn: sqlite3.Connection, raw_phone: str, code: str) -> str:
    """Check a one-time code; on success return the phone number's identifier (E.164)."""
    phone = normalize_phone(raw_phone)
    phone_hash = hash_identifier(conn, "phone", phone)
    record = _OTP_STORE.get(phone_hash)
    now = time.time()
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "That code is invalid or has expired.")
    if not record or not record["code_hash"] or now > record["expires"]:
        raise invalid
    if record["attempts"] >= MAX_OTP_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many wrong codes. Request a new code.")
    record["attempts"] += 1
    if not (code.isdigit() and len(code) == OTP_LENGTH) or not hmac.compare_digest(
        _hash_otp(conn, phone_hash, code), record["code_hash"]
    ):
        raise invalid
    record["code_hash"] = ""  # single use
    return phone


# --------------------------------------------------------------------------- account linking

def sign_in_with_identity(
    conn: sqlite3.Connection, provider: str, identifier: str, display_name: Optional[str]
) -> tuple[str, str, bool]:
    """Return (learner_id, session_token, is_new) for a verified identity, creating the learner if needed."""
    subject_hash = hash_identifier(conn, provider, identifier)
    row = conn.execute(
        "SELECT learner_id FROM identities WHERE provider = ? AND subject_hash = ?", (provider, subject_hash)
    ).fetchone()
    if row:
        with conn:
            token = create_session(conn, row["learner_id"])
        return row["learner_id"], token, False

    name = (display_name or "").strip()[:50] or "learner"
    learner_id = generate_learner_id(conn, name)
    now = _now_iso()
    with conn:
        conn.execute(
            "INSERT INTO learners (id, display_name, pin_hash, created_at) VALUES (?, ?, NULL, ?)",
            (learner_id, name, now),
        )
        conn.execute(
            "INSERT INTO identities (provider, subject_hash, learner_id, created_at) VALUES (?, ?, ?, ?)",
            (provider, subject_hash, learner_id, now),
        )
        token = create_session(conn, learner_id)
    return learner_id, token, True
