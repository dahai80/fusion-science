from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...api.auth import Role, issue_jwt, load_api_keys

logger = logging.getLogger(__name__)

router = APIRouter()


class TokenRequest(BaseModel):
    api_key: str
    subject: str | None = None
    role: str | None = None


@router.post("/auth/token")
async def issue_token(request: Request, body: TokenRequest) -> JSONResponse:
    # Exchange a provisioned API key for a short-lived JWT carrying its role.
    # The caller then sends `Authorization: Bearer <jwt>` on subsequent calls.
    keys = load_api_keys()
    if body.api_key not in keys:
        logger.warning("Auth: token request rejected (unknown API key)")
        return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
    role = keys[body.api_key]
    # Allow a key to mint a lower-privilege token only (never escalate).
    if body.role:
        order = [Role.VIEWER, Role.SCIENCE, Role.ADMIN]
        if body.role not in {r.value for r in order}:
            return JSONResponse(status_code=400, content={"detail": f"Unknown role: {body.role}"})
        requested = Role(body.role)
        if order.index(requested) > order.index(role):
            logger.warning(
                "Auth: key role=%s tried to mint %s token (escalation denied)",
                role.value,
                requested.value,
            )
            return JSONResponse(status_code=403, content={"detail": "Cannot escalate role above key's role"})
        role = requested
    sub = body.subject or f"apikey:{body.api_key[:8]}"
    token = issue_jwt(role, sub)
    logger.info("Auth: issued %s JWT for subject=%s", role.value, sub)
    return JSONResponse(
        status_code=200,
        content={
            "access_token": token,
            "token_type": "bearer",
            "role": role.value,
            "subject": sub,
        },
    )


@router.get("/auth/whoami")
async def whoami(request: Request) -> JSONResponse:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return JSONResponse(status_code=401, content={"detail": "No authenticated principal"})
    return JSONResponse(
        status_code=200,
        content={"role": principal.role.value, "subject": principal.subject},
    )
