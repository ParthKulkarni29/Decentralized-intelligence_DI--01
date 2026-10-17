"""Routes for optional Google / phone sign-in (see backend/identity.py for the privacy design)."""

from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend import identity
from backend.database import get_db_connection

router = APIRouter(prefix="/api/auth", tags=["auth"])


class GoogleLoginRequest(BaseModel):
    """POST /api/auth/google request body."""

    credential: str = Field(..., min_length=20, max_length=4096)
    display_name: Optional[str] = Field(None, max_length=50)


class PhoneRequestBody(BaseModel):
    """POST /api/auth/phone/request request body."""

    phone: str = Field(..., min_length=8, max_length=24)


class PhoneVerifyBody(BaseModel):
    """POST /api/auth/phone/verify request body."""

    phone: str = Field(..., min_length=8, max_length=24)
    code: str = Field(..., min_length=4, max_length=10)
    display_name: Optional[str] = Field(None, max_length=50)


class IdentityLoginResponse(BaseModel):
    """Result of a successful Google or phone sign-in."""

    learner_id: str
    token: str
    is_new: bool


@router.get("/providers")
def list_providers() -> dict:
    """Which sign-in methods are available. Contains no secrets (the Google client id is public)."""
    return identity.provider_info()


@router.post("/google", response_model=IdentityLoginResponse)
def google_login(body: GoogleLoginRequest) -> IdentityLoginResponse:
    """Sign in (or create a pseudonymous learner) with a Google ID token."""
    subject = identity.verify_google_credential(body.credential)
    conn = get_db_connection()
    try:
        learner_id, token, is_new = identity.sign_in_with_identity(conn, "google", subject, body.display_name)
        return IdentityLoginResponse(learner_id=learner_id, token=token, is_new=is_new)
    finally:
        conn.close()


@router.post("/phone/request")
def phone_request(body: PhoneRequestBody) -> dict:
    """Send a one-time code to a phone number."""
    conn = get_db_connection()
    try:
        return identity.request_phone_code(conn, body.phone)
    finally:
        conn.close()


@router.post("/phone/verify", response_model=IdentityLoginResponse)
def phone_verify(body: PhoneVerifyBody) -> IdentityLoginResponse:
    """Verify a one-time code and sign in (or create a pseudonymous learner)."""
    conn = get_db_connection()
    try:
        phone = identity.verify_phone_code(conn, body.phone, body.code)
        learner_id, token, is_new = identity.sign_in_with_identity(conn, "phone", phone, body.display_name)
        return IdentityLoginResponse(learner_id=learner_id, token=token, is_new=is_new)
    finally:
        conn.close()
