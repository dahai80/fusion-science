from __future__ import annotations

import hmac
import logging
import os
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


_EXEMPT_PATHS = {"/api/v1/health", "/docs", "/openapi.json", "/redoc", "/redoc.openapi.json", "/favicon.ico"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    # F-S8: lightweight per-IP fixed-window rate limit. Prevents a single
    # client from flooding chat/compute (each LLM call is expensive). Not a
    # distributed limiter — single-process, consistent with the single-worker
    # model. Configurable via env, disabled when limit <= 0.
    def __init__(self, app, limit: int = 0, window: int = 60):
        super().__init__(app)
        self.limit = limit
        self.window = window
        self._counts: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if self.limit <= 0:
            return await call_next(request)
        path = request.url.path.rstrip("/").lower()
        if path in _EXEMPT_PATHS:
            return await call_next(request)
        client_host = request.client.host if request.client else "unknown"
        now = time.monotonic()
        bucket = self._counts[client_host]
        # drop entries outside the window
        cutoff = now - self.window
        self._counts[client_host] = [t for t in bucket if t > cutoff]
        if len(self._counts[client_host]) >= self.limit:
            logger.warning("Rate limit exceeded for %s on %s", client_host, request.url.path)
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded: {self.limit} requests per {self.window}s"},
                headers={"Retry-After": str(self.window)},
            )
        self._counts[client_host].append(now)
        return await call_next(request)


# P1 (S4): loopback client addresses. When FUSION_SCIENCE_API_KEY is unset the
# middleware fails CLOSED for any non-loopback caller — a LAN-bound server
# (0.0.0.0) without a key can no longer be reached by other machines. Loopback
# is still allowed keyless for local single-user / dev use.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        api_key = os.getenv("FUSION_SCIENCE_API_KEY", "")
        path = request.url.path.rstrip("/").lower()

        if path in _EXEMPT_PATHS:
            return await call_next(request)

        if not api_key:
            client_host = request.client.host if request.client else ""
            if client_host in _LOOPBACK_HOSTS:
                logger.warning(
                    "FUSION_SCIENCE_API_KEY not set — loopback caller admitted keyless. "
                    "Set the key and bind 127.0.0.1, or set FUSION_SCIENCE_API_KEY before LAN exposure."
                )
                return await call_next(request)
            # P1 (S4): fail-closed for non-loopback callers when no key is set.
            logger.error(
                "Blocked non-loopback request without API key: %s %s from %s",
                request.method,
                request.url.path,
                client_host,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "API key required: set FUSION_SCIENCE_API_KEY to allow remote access"},
            )

        key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(key, api_key):
            logger.warning("Unauthorized API access: %s %s", request.method, request.url.path)
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})

        return await call_next(request)


class MetricsMiddleware(BaseHTTPMiddleware):
    # F-O2: count requests, errors, and latency for the /metrics endpoint.
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        try:
            response: Response = await call_next(request)
        except Exception:
            from .routes.metrics import get_metrics

            get_metrics().record_request(time.monotonic() - start, is_error=True)
            raise
        from .routes.metrics import get_metrics

        is_error = response.status_code >= 500
        get_metrics().record_request(time.monotonic() - start, is_error=is_error)
        return response
