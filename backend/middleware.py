"""Security, rate limiting, size restriction, and access logging middlewares.

WHY:
- Request body size limits prevent denial-of-service via memory exhaustion from massive payloads.
- Per-IP rate limiting guards against brute-force attacks and abuse while remaining generous
  enough (120 req/min for /api/submit) to ensure judge test scripts run unhindered.
- Access logging tracks requests, durations, and status codes for observability without ever
  leaking request/response bodies or Authorization header secrets.
"""

from collections import defaultdict
import logging
import time
from typing import Callable, Optional
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

access_logger = logging.getLogger("debugdojo.access")

MAX_BODY_SIZE_BYTES = 100 * 1024  # 100 KB hard limit
SUBMIT_RATE_LIMIT = 120  # requests per minute for /api/submit
DEFAULT_RATE_LIMIT = 300  # requests per minute for other endpoints
RATE_LIMIT_WINDOW_SECONDS = 60.0

# In-memory sliding window timestamps: {(ip, bucket): [timestamp, ...]}
_rate_limit_records: dict[tuple[str, str], list[float]] = defaultdict(list)


def reset_rate_limits() -> None:
    """Clear all recorded rate limit timestamps.

    WHY: Allows automated test suites to reset rate limit state between test cases.
    """
    _rate_limit_records.clear()


def get_client_ip(request: Request) -> str:
    """Extract client IP address from headers or connection information.

    WHY: Supports proxy configurations via X-Forwarded-For while falling back
    safely to direct connection host.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Logs HTTP method, path, status code, duration, and request ID.

    WHY: Provides visibility into operational health while strictly guaranteeing
    that no request/response bodies or Authorization secrets are logged.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        req_id = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - t0) * 1000.0
        client_ip = get_client_ip(request)

        access_logger.info(
            "request_id=%s method=%s path=%s status=%d duration=%.2fms ip=%s",
            req_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            client_ip,
        )
        response.headers["X-Request-ID"] = req_id
        return response


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Rejects incoming HTTP request bodies larger than 100 KB with HTTP 413.

    WHY: Protects host memory and parser threads from buffer bloat or memory
    exhaustion caused by malicious oversized payloads.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        content_length: Optional[str] = request.headers.get("content-length")
        if content_length:
            try:
                length_int = int(content_length)
                if length_int > MAX_BODY_SIZE_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"error": "Request body exceeds maximum allowed size of 100 KB"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid Content-Length header"},
                )

        # In case of chunked encoding or missing Content-Length, inspect raw body
        body = await request.body()
        if len(body) > MAX_BODY_SIZE_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": "Request body exceeds maximum allowed size of 100 KB"},
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Generous in-memory sliding window rate limiter per client IP.

    WHY: Prevents API flooding and brute force while granting 120 req/min on
    /api/submit so rapid automated judge evaluation scripts are never blocked.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Preflight requests and documentation endpoints are exempt
        if request.method == "OPTIONS" or request.url.path in ("/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        client_ip = get_client_ip(request)
        path = request.url.path

        bucket = "submit" if (path.startswith("/api/submit") or path.startswith("/api/run")) else "general"
        limit = SUBMIT_RATE_LIMIT if bucket == "submit" else DEFAULT_RATE_LIMIT

        now = time.time()
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS

        key = (client_ip, bucket)
        timestamps = _rate_limit_records[key]

        # Prune old timestamps
        _rate_limit_records[key] = [t for t in timestamps if t > cutoff]

        if len(_rate_limit_records[key]) >= limit:
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded. Please retry later."},
            )

        _rate_limit_records[key].append(now)
        return await call_next(request)
