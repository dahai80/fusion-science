from __future__ import annotations

import hmac
import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


_EXEMPT_PATHS = {"/api/v1/health", "/docs", "/openapi.json", "/redoc", "/redoc.openapi.json", "/favicon.ico"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        api_key = os.getenv("FUSION_SCIENCE_API_KEY", "")
        path = request.url.path.rstrip("/").lower()

        if path in _EXEMPT_PATHS:
            return await call_next(request)

        if not api_key:
            logger.warning(
                "FUSION_SCIENCE_API_KEY not set — API auth disabled. Bind to 127.0.0.1 in production or set the key."
            )
            return await call_next(request)

        key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(key, api_key):
            logger.warning("Unauthorized API access: %s %s", request.method, request.url.path)
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})

        return await call_next(request)
