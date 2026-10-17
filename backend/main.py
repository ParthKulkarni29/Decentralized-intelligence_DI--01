"""FastAPI main application entry point for Debug Dojo.

Configures CORS, database initialization on startup, global exception handler,
and initial health endpoint.
"""

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from backend.api.routes import router as api_router
from backend.api.identity_routes import router as identity_router
from backend.config import get_settings
from backend.database import init_db
from backend.engine.sandbox import (
    SandboxBusyError,
    cleanup_stale_containers,
    cleanup_old_containers,
)
from backend.schemas import HealthResponse

# Configure structured logging - logs exception type only, never request bodies or secrets
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("debugdojo.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager.

    Initializes the SQLite schema, removes stale sandbox containers on startup,
    and maintains a background safety-net cleanup task for old containers.
    """
    logger.info("Initializing Debug Dojo database...")
    init_db()
    logger.info("Database ready.")

    # 1. On server startup remove stale containers whose names start with the sandbox prefix
    stale_count = cleanup_stale_containers()
    logger.info("Startup stale container cleanup removed %d container(s).", stale_count)

    # 2. Background cleanup that removes sandbox containers older than 60 seconds
    async def _background_cleanup_task() -> None:
        while True:
            try:
                await asyncio.sleep(30.0)
                removed = await asyncio.to_thread(cleanup_old_containers, 60.0)
                if removed > 0:
                    logger.info("Background safety-net cleanup removed %d sandbox container(s) older than 60s", removed)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Background container cleanup error: %s", type(exc).__name__)

    cleanup_task = asyncio.create_task(_background_cleanup_task())

    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


settings = get_settings()

app = FastAPI(
    title="Debug Dojo Backend API",
    version="0.1.0",
    lifespan=lifespan,
)

from backend.middleware import (
    AccessLogMiddleware,
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
)

# Middleware configuration (ordered from outer to inner)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestBodyLimitMiddleware)



@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """HTTP exception handler returning {"error": <detail>} per API CONTRACT."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Validation exception handler returning {"error": <message>} per API CONTRACT."""
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid request parameters or payload"},
    )


@app.exception_handler(SandboxBusyError)
async def sandbox_busy_exception_handler(request: Request, exc: SandboxBusyError) -> JSONResponse:
    """Handle sandbox concurrency queue saturation.

    WHY:
    - If every sandbox slot is busy and queue timeout passes, return HTTP 503 with Retry-After.
    - Untrusted learner code is NEVER executed unsandboxed.
    - Server remains healthy for subsequent calls.
    """
    logger.warning("Sandbox concurrency limit saturated; returning 503 Service Unavailable")
    return JSONResponse(
        status_code=503,
        content={"error": "The dojo is busy, please try again"},
        headers={"Retry-After": "5"},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler returning status 500 and generic error.

    WHY:
    - Never exposes raw tracebacks, internal file paths, or hidden evaluation data to the client.
    - Logs ONLY the exception type (e.g. ValueError, KeyError), omitting request bodies, query strings,
      and headers that could contain API keys, passwords, or learner code.
    """
    logger.error("Unhandled server exception: %s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong"},
    )


@app.get("/api/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Service health check endpoint."""
    return HealthResponse(status="ok")


# Include all Phase 4 API routes (/api/challenges, /api/submit, /api/hint, /api/learner)
app.include_router(api_router)
app.include_router(identity_router)
