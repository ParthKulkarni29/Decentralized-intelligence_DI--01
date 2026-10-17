"""Tests for configuration and secret masking."""

import os
from backend.config import get_settings, get_safe_settings


def test_get_settings_defaults():
    """Verify settings defaults are populated correctly."""
    settings = get_settings()
    assert settings.DATABASE_PATH != ""
    assert isinstance(settings.MAX_PARALLEL_RUNS, int)
    assert isinstance(settings.CORS_ORIGINS, list)
    assert len(settings.CORS_ORIGINS) >= 1


def test_safe_settings_masks_api_key(monkeypatch):
    """Verify get_safe_settings never exposes the raw GEMINI_API_KEY."""
    monkeypatch.setenv("GEMINI_API_KEY", "super_secret_key_12345")
    safe = get_safe_settings()
    assert safe["GEMINI_API_KEY"] == "***"
    assert "super_secret_key_12345" not in str(safe)
