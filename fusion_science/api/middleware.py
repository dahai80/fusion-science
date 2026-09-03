from __future__ import annotations

import logging
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .auth import AuthPrincipal, Role, authenticate, load_api_keys, role_allows, touch_principal

logger = logging.getLogger(__name__)


_EXEMPT_PATHS = {
    "/api/v1/health",
    "/api/v1/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/redoc.openapi.json",
    "/favicon.ico",
}

# Routes that authenticate themselves in the body and must NOT be gated by the
# API-key/JWT check (else the token-exchange endpoint could never be reached).
_SELF_AUTH_PATHS = {"/api/v1/auth/token"}


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


# P1 (S4): loopback client addresses. When no API key is provisioned the
# middleware fails CLOSED for any non-loopback caller — a LAN-bound server
# (0.0.0.0) without a key can no longer be reached by other machines. Loopback
# is still allowed keyless for local single-user / dev use (role=admin).
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _route_prefix(path: str) -> str:
    # Derive the RBAC permission key from the request path. Mounted routes are
    # under /api/v1/<prefix>...; the prefix (sessions/databases/compute/...) is
    # the permission key. The chat/search/analysis/visualize/review/audit routes
    # are nested under /api/v1/sessions/{id}/<verb> so they map to their own
    # permission key, not "sessions".
    # e.g. /api/v1/sessions/{id}/chat -> chat, /api/v1/databases -> databases
    parts = path.split("/")
    # find the segment after "v1"
    try:
        idx = parts.index("v1")
    except ValueError:
        return ""
    tail = parts[idx + 1 :]
    if not tail:
        return ""
    first = tail[0]
    # /api/v1/sessions/{id}/<verb> -> <verb>
    if first == "sessions" and len(tail) >= 3:
        return tail[2]
    # /api/v1/sessions or /api/v1/sessions/{id} -> sessions
    return first


class APIKeyMiddleware(BaseHTTPMiddleware):
    # RBAC (F-ENT-AUTH): resolves an AuthPrincipal from a Bearer JWT or an
    # X-API-Key header (role-scoped), then enforces the role → route-prefix →
    # method permission map in auth.py. Legacy single-key setups (no
    # FUSION_SCIENCE_API_KEY[_S]) degrade to the keyless loopback-only dev path.
    # Keys are re-read from env on every request: cheap, and preserves the
    # per-test env-set contract used across the test suite.
    def __init__(self, app, api_keys: dict[str, Role] | None = None):
        super().__init__(app)
        self._api_keys = api_keys

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/").lower()

        if path in _EXEMPT_PATHS:
            return await call_next(request)
        if path in _SELF_AUTH_PATHS:
            # /auth/token authenticates via the request body (api_key field);
            # the route handler enforces it. Admit so the exchange can run.
            return await call_next(request)

        keys = self._api_keys if self._api_keys is not None else load_api_keys()
        principal = authenticate(
            authorization=request.headers.get("Authorization"),
            x_api_key=request.headers.get("X-API-Key"),
            api_keys=keys,
        )

        if principal is None:
            # No/invalid credential. Allow loopback keyless (dev convenience),
            # fail-closed otherwise.
            client_host = request.client.host if request.client else ""
            if client_host in _LOOPBACK_HOSTS and not keys:
                logger.warning(
                    "No API key provisioned — loopback caller admitted keyless as admin. "
                    "Set FUSION_SCIENCE_API_KEY[_S] and bind 127.0.0.1 before LAN exposure."
                )
                principal = AuthPrincipal(role=Role.ADMIN, subject="loopback")
            else:
                logger.warning(
                    "Unauthorized: %s %s from %s (no/invalid credential)",
                    request.method,
                    request.url.path,
                    client_host,
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required: provide X-API-Key or Authorization: Bearer <jwt>"},
                )

        # G4 idle session lockout: enforce per-principal auto-logoff BEFORE the
        # RBAC check so an idle-but-otherwise-authorized principal is still
        # rejected. API keys bypass idle lockout (they have no session concept).
        if not principal.subject.startswith("apikey:"):
            from .auth import _load_idle_timeout

            if not touch_principal(principal.subject, _load_idle_timeout()):
                logger.warning(
                    "Idle-lockout 401 for %s %s subject=%s", request.method, request.url.path, principal.subject
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Session expired due to inactivity (auto-logoff); re-authenticate"},
                )
        prefix = _route_prefix(path)
        if prefix and not role_allows(principal.role, prefix, request.method):
            logger.warning(
                "Forbidden: role=%s denied %s %s (prefix=%s) subject=%s",
                principal.role.value,
                request.method,
                request.url.path,
                prefix,
                principal.subject,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": f"Role '{principal.role.value}' may not {request.method} {prefix}"},
            )

        # Stash the principal on request state for downstream routes.
        request.state.principal = principal
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
