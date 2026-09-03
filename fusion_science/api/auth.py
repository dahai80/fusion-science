"""Role-based access control (RBAC) + JWT auth for the fusion-science API.

Three built-in roles with a route-prefix → HTTP-method permission map:

- ``admin``  — full access (all routes, all methods). Backward-compatible
  default for the legacy single ``FUSION_SCIENCE_API_KEY``.
- ``science`` — read + research workflows: search, databases, citations,
  math, compute, chat, analysis, visualize, review, audit, pipelines,
  sessions, tools. No system/security/model mutation.
- ``viewer``  — read-only: search, databases, citations, math, visualize,
  models (list), health, metrics. No compute, no chat, no mutations.

API keys are provisioned via ``FUSION_SCIENCE_API_KEYS`` as
``role:key`` pairs (comma-separated). The legacy single
``FUSION_SCIENCE_API_KEY`` is treated as an ``admin`` key for backward
compatibility. JWT session tokens are issued at ``POST /api/v1/auth/token``
(HS256, 1h TTL) and carry the role claim; callers send them as
``Authorization: Bearer <jwt>``.

No external IdP — local-first. External OAuth2/OIDC tracked in issue #22.
"""

from __future__ import annotations

import enum
import hmac
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class Role(enum.Enum):
    ADMIN = "admin"
    SCIENCE = "science"
    VIEWER = "viewer"


# Route prefix (as mounted in app.py, without the /api/v1 prefix) → set of
# allowed HTTP methods per role. "ANY" means all methods. Prefixes are matched
# by longest-prefix so /sessions/{id}/audit is governed by the audit rule.
_PERMISSIONS: dict[Role, dict[str, frozenset[str]]] = {
    Role.ADMIN: {
        "*": frozenset({"ANY"}),
    },
    Role.SCIENCE: {
        "health": frozenset({"ANY"}),
        "sessions": frozenset({"ANY"}),
        "chat": frozenset({"ANY"}),
        "search": frozenset({"ANY"}),
        "analysis": frozenset({"ANY"}),
        "visualize": frozenset({"ANY"}),
        "review": frozenset({"ANY"}),
        "audit": frozenset({"ANY"}),
        "databases": frozenset({"ANY"}),
        "pipelines": frozenset({"ANY"}),
        "models": frozenset({"GET"}),
        "citations": frozenset({"ANY"}),
        "math": frozenset({"ANY"}),
        "viz": frozenset({"ANY"}),
        "compute": frozenset({"ANY"}),
        "tools": frozenset({"GET"}),
        "metrics": frozenset({"GET"}),
        "auth": frozenset({"ANY"}),
    },
    Role.VIEWER: {
        "health": frozenset({"ANY"}),
        "search": frozenset({"ANY"}),
        "databases": frozenset({"ANY"}),
        "citations": frozenset({"ANY"}),
        "math": frozenset({"ANY"}),
        "viz": frozenset({"ANY"}),
        "visualize": frozenset({"GET"}),
        "models": frozenset({"GET"}),
        "metrics": frozenset({"GET"}),
        "auth": frozenset({"ANY"}),
    },
}


@dataclass
class AuthPrincipal:
    role: Role
    subject: str  # API-key id or "jwt:<sub>"


def role_allows(role: Role, route_prefix: str, method: str) -> bool:
    """Check whether ``role`` may perform ``method`` on ``route_prefix``."""
    perms = _PERMISSIONS.get(role, {})
    if "*" in perms:
        return True
    methods = perms.get(route_prefix)
    if methods is None:
        return False
    if "ANY" in methods:
        return True
    return method.upper() in methods


def load_api_keys() -> dict[str, Role]:
    """Parse provisioned API keys into a {key: role} map.

    Sources (later wins):
    - ``FUSION_SCIENCE_API_KEY`` → admin (legacy single key)
    - ``FUSION_SCIENCE_API_KEYS`` → ``science:abc,viewer:def`` pairs
    """
    keys: dict[str, Role] = {}
    legacy = os.getenv("FUSION_SCIENCE_API_KEY", "")
    if legacy:
        keys[legacy] = Role.ADMIN
    multi = os.getenv("FUSION_SCIENCE_API_KEYS", "")
    if multi:
        for pair in multi.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                logger.warning("Ignoring malformed API key entry (no role:key): %r", pair)
                continue
            role_str, _, key = pair.partition(":")
            role_str = role_str.strip().lower()
            key = key.strip()
            if not key:
                logger.warning("Ignoring empty API key for role %s", role_str)
                continue
            try:
                role = Role(role_str)
            except ValueError:
                logger.warning("Ignoring unknown role %r in API keys", role_str)
                continue
            keys[key] = role
    return keys


# --- JWT (HS256, dependency-free: stdlib hmac + hashlib + base64 + json) ---

_JWT_TTL = 3600


def _jwt_secret() -> str:
    secret = os.getenv("FUSION_SCIENCE_JWT_SECRET", "")
    if not secret:
        # Derive from the legacy API key so a single-key setup still gets a
        # stable signing secret without an extra env var.
        secret = os.getenv("FUSION_SCIENCE_API_KEY", "fusion-science-dev-secret")
    return secret


def issue_jwt(role: Role, subject: str) -> str:
    """Issue a short-lived HS256 JWT carrying the role claim."""
    import base64
    import hashlib
    import json

    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": subject,
        "role": role.value,
        "iat": now,
        "exp": now + _JWT_TTL,
    }

    def _b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    signing_input = f"{_b64(header)}.{_b64(payload)}".encode()
    sig = hmac.new(_jwt_secret().encode(), signing_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{signing_input.decode()}.{sig_b64}"


def decode_jwt(token: str) -> AuthPrincipal | None:
    """Verify signature + expiry; return the principal or None on failure."""
    import base64
    import hashlib
    import json

    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts

    def _unb64(seg: str) -> bytes:
        pad = "=" * (-len(seg) % 4)
        return base64.urlsafe_b64decode(seg + pad)

    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = hmac.new(_jwt_secret().encode(), signing_input, hashlib.sha256).digest()
    given = _unb64(sig_b64)
    if not hmac.compare_digest(expected, given):
        logger.warning("JWT signature mismatch")
        return None
    try:
        payload = json.loads(_unb64(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        logger.warning("JWT expired for sub=%s", payload.get("sub"))
        return None
    try:
        role = Role(payload.get("role", ""))
    except ValueError:
        return None
    return AuthPrincipal(role=role, subject=f"jwt:{payload.get('sub', '')}")


def authenticate(authorization: str | None, x_api_key: str | None, api_keys: dict[str, Role]) -> AuthPrincipal | None:
    """Resolve a principal from a Bearer JWT or an X-API-Key header.

    Returns None when no credential is present or verification fails — the
    caller decides whether to reject (non-exempt route) or admit (exempt).
    """
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            return decode_jwt(token)
    if x_api_key and x_api_key in api_keys:
        return AuthPrincipal(role=api_keys[x_api_key], subject=f"apikey:{x_api_key[:8]}")
    return None
