"""Application configuration and environment settings.

Handles loading settings from environment variables and backend/.env.
Never prints or logs secret values.
"""

from dataclasses import dataclass
import os
from pathlib import Path
from dotenv import load_dotenv

# Locate backend/.env or root .env
_BACKEND_DIR = Path(__file__).resolve().parent
_ENV_PATH = _BACKEND_DIR / ".env"
_ROOT_ENV_PATH = _BACKEND_DIR.parent / ".env"

if _ENV_PATH.exists():
    load_dotenv(dotenv_path=_ENV_PATH)
elif _ROOT_ENV_PATH.exists():
    load_dotenv(dotenv_path=_ROOT_ENV_PATH)
else:
    load_dotenv()


from typing import Optional


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    GEMINI_API_KEY: str
    GEMINI_MODEL: str
    DATABASE_PATH: str
    SANDBOX_MODE: str
    MAX_PARALLEL_RUNS: int
    CORS_ORIGINS: list[str]
    CORS_ORIGIN_REGEX: Optional[str] = None


def get_settings() -> Settings:
    """Retrieve application configuration from environment variables.

    WHY: Reads directly from os.environ after load_dotenv, centralizing
    configuration parsing in one place.
    """
    raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

    raw_regex = os.getenv("CORS_ORIGIN_REGEX", "").strip()
    cors_origin_regex = raw_regex if raw_regex else None

    raw_runs = os.getenv("MAX_PARALLEL_RUNS", "3")
    try:
        max_runs = int(raw_runs)
    except ValueError:
        max_runs = 3

    return Settings(
        GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", ""),
        GEMINI_MODEL=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        DATABASE_PATH=os.getenv("DATABASE_PATH", "backend/debugdojo.db"),
        SANDBOX_MODE=os.getenv("SANDBOX_MODE", "docker"),
        MAX_PARALLEL_RUNS=max_runs,
        CORS_ORIGINS=origins,
        CORS_ORIGIN_REGEX=cors_origin_regex,
    )


def get_safe_settings() -> dict[str, object]:
    """Return settings dictionary with sensitive secrets masked for debugging.

    WHY: Anti-leak requirement. Prevents accidental leakage of GEMINI_API_KEY
    into logs, debug dumps, or admin endpoints.
    """
    settings = get_settings()
    masked_key = "***" if settings.GEMINI_API_KEY else ""

    return {
        "GEMINI_API_KEY": masked_key,
        "GEMINI_MODEL": settings.GEMINI_MODEL,
        "DATABASE_PATH": settings.DATABASE_PATH,
        "SANDBOX_MODE": settings.SANDBOX_MODE,
        "MAX_PARALLEL_RUNS": settings.MAX_PARALLEL_RUNS,
        "CORS_ORIGINS": settings.CORS_ORIGINS,
        "CORS_ORIGIN_REGEX": settings.CORS_ORIGIN_REGEX,
    }

