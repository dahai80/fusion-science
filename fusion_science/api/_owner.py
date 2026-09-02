from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_DEFAULT_OWNER = "local"


def get_owner(request: Request) -> str:
    owner = request.headers.get("X-Fusion-User", "").strip()
    if not owner:
        owner = _DEFAULT_OWNER
    return owner


def check_owner(request: Request, session) -> JSONResponse | None:
    if session is None:
        return JSONResponse(status_code=404, content={"detail": "session_not_found"})
    owner = get_owner(request)
    if session.owner and session.owner != owner:
        logger.warning(
            "IDOR blocked: owner=%s session=%s owner=%s",
            owner,
            session.id,
            session.owner,
        )
        return JSONResponse(status_code=403, content={"detail": "forbidden: not session owner"})
    return None
