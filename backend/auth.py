"""Pseudonymous authentication and authorization service for Debug Dojo.

SECURITY AND PRIVACY NOTICE:
No real names, emails, or personal data are collected or stored by this service.
All accounts are pseudonymous slugs created with an optional numeric PIN.
Tokens are random 256-bit strings stored only as SHA-256 hashes with an expiry.
PINs are stored using salted PBKDF2-HMAC-SHA256 with per-learner salts.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from typing import Optional
from fastapi import HTTPException, Header, status

PIN_REGEX = re.compile(r"^\d{4,8}$")
SLUG_CLEAN_REGEX = re.compile(r"[^a-zA-Z0-9_-]+")

# In-memory rate limiting and brute force tracking
# learner_id -> {"attempts": int, "locked_until": Optional[float]}
_LOGIN_ATTEMPTS: dict[str, dict] = {}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60.0


def _get_utc_now() -> str:
    """Return ISO 8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()


def hash_pin(pin: str) -> str:
    """Hash a 4-8 digit numeric PIN with a unique random salt using PBKDF2-HMAC-SHA256."""
    salt = secrets.token_hex(16)
    pin_hash = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=100_000,
    ).hex()
    return f"{salt}${pin_hash}"


def verify_pin(pin: str, stored_pin_hash: str) -> bool:
    """Verify PIN against the stored salt$hash representation."""
    if not stored_pin_hash or "$" not in stored_pin_hash:
        return False
    salt, expected_hash = stored_pin_hash.split("$", 1)
    computed_hash = hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=100_000,
    ).hex()
    return hmac.compare_digest(computed_hash, expected_hash)


def hash_token(raw_token: str) -> str:
    """Hash a bearer token with SHA-256 for secure database storage."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session(db: sqlite3.Connection, learner_id: str, days_valid: int = 30) -> str:
    """Generate a high-entropy random URL-safe token, store its hash, and return raw token."""
    raw_token = secrets.token_urlsafe(32)
    token_h = hash_token(raw_token)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = (now + timedelta(days=days_valid)).isoformat()

    db.execute(
        """
        INSERT INTO sessions (token_hash, learner_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (token_h, learner_id, created_at, expires_at),
    )
    return raw_token


def generate_learner_id(db: sqlite3.Connection, display_name: str) -> str:
    """Generate a unique pseudonymous slug from display_name with numeric suffix if taken."""
    cleaned = SLUG_CLEAN_REGEX.sub("_", display_name.strip()).lower().strip("_")
    if not cleaned:
        cleaned = "learner"
    base_slug = cleaned[:30]

    # Check existence
    cursor = db.cursor()
    cursor.execute("SELECT id FROM learners WHERE id = ?", (base_slug,))
    if not cursor.fetchone():
        return base_slug

    suffix = 1
    while True:
        candidate = f"{base_slug}_{suffix}"
        cursor.execute("SELECT id FROM learners WHERE id = ?", (candidate,))
        if not cursor.fetchone():
            return candidate
        suffix += 1


def check_brute_force(learner_id: str) -> None:
    """Check if the learner_id is currently locked out due to excessive failed attempts."""
    now = time.time()
    record = _LOGIN_ATTEMPTS.get(learner_id)
    if record and record.get("locked_until"):
        if now < record["locked_until"]:
            wait_sec = int(record["locked_until"] - now) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed login attempts. Locked out for {wait_sec} seconds.",
            )
        else:
            # Lockout expired, reset attempts
            _LOGIN_ATTEMPTS[learner_id] = {"attempts": 0, "locked_until": None}


def record_login_attempt(learner_id: str, success: bool) -> None:
    """Update attempt tracker and apply rate limiting."""
    # Small constant-time sleep to deter timing attacks and slow brute force
    time.sleep(0.05)

    if success:
        _LOGIN_ATTEMPTS.pop(learner_id, None)
        return

    now = time.time()
    record = _LOGIN_ATTEMPTS.setdefault(learner_id, {"attempts": 0, "locked_until": None})
    record["attempts"] += 1
    if record["attempts"] >= MAX_FAILED_ATTEMPTS:
        record["locked_until"] = now + LOCKOUT_SECONDS


def verify_learner_token(
    db: sqlite3.Connection,
    learner_id: str,
    authorization: Optional[str] = None,
) -> bool:
    """Check learner authentication.
    
    Rules:
    - Learners WITHOUT a PIN and without a linked Google/phone identity never require authentication.
    - Learners WITH a PIN set (or a linked identity) require a valid, non-expired Bearer token belonging to them.
    - Raises HTTPException 401 if token is missing, invalid, or expired for a pinned learner.
    """
    cursor = db.cursor()
    cursor.execute("SELECT pin_hash FROM learners WHERE id = ?", (learner_id,))
    row = cursor.fetchone()
    if not row:
        # Learner does not exist yet; auto-created or checked downstream
        return True

    pin_hash = row["pin_hash"]
    has_pin = bool(pin_hash)
    # Learners created via Google/phone sign-in are protected even without a PIN
    has_identity = cursor.execute(
        "SELECT 1 FROM identities WHERE learner_id = ? LIMIT 1", (learner_id,)
    ).fetchone() is not None
    if not has_pin and not has_identity:
        # Unprotected learner (no PIN, no linked identity) - no auth required
        return True

    # Protected learner (PIN set or identity linked) -> Bearer token mandatory
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: this learner profile is protected by a PIN.",
        )

    raw_token = authorization.split("Bearer ", 1)[1].strip()
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or empty Bearer token.",
        )

    token_h = hash_token(raw_token)
    now_iso = _get_utc_now()
    cursor.execute(
        """
        SELECT learner_id, expires_at FROM sessions
        WHERE token_hash = ? AND expires_at > ?
        """,
        (token_h, now_iso),
    )
    session_row = cursor.fetchone()
    if not session_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token is invalid or has expired.",
        )

    if session_row["learner_id"] != learner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session token does not match the requested learner profile.",
        )

    return True
