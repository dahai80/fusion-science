from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateSessionRequest(BaseModel):
    title: str = ""


class UpdateSessionRequest(BaseModel):
    title: str = Field(..., min_length=1)


@router.post("")
async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, Any]:
    mgr = request.app.state.session_manager
    session = await mgr.create_session(title=body.title)
    return {"session_id": session.id, "title": session.title, "created_at": session.created_at}


@router.get("")
async def list_sessions(request: Request) -> dict[str, Any]:
    mgr = request.app.state.session_manager
    sessions = mgr.list_sessions()
    return {
        "sessions": [
            {"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at} for s in sessions
        ]
    }


@router.get("/{session_id}")
async def get_session(request: Request, session_id: str) -> dict[str, Any]:
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        return {"error": "session_not_found", "session_id": session_id}
    return {
        "session_id": session.id,
        "title": session.title,
        "messages": session.messages,
        "artifacts": [a.to_dict() for a in session.artifacts],
    }


@router.delete("/{session_id}")
async def delete_session(request: Request, session_id: str) -> dict[str, Any]:
    mgr = request.app.state.session_manager
    deleted = await mgr.delete_session(session_id)
    if not deleted:
        return {"error": "session_not_found", "session_id": session_id}
    return {"session_id": session_id, "status": "deleted"}


@router.patch("/{session_id}")
async def update_session(request: Request, session_id: str, body: UpdateSessionRequest) -> dict[str, Any]:
    mgr = request.app.state.session_manager
    session = await mgr.update_title(session_id, body.title)
    if not session:
        return {"error": "session_not_found", "session_id": session_id}
    return {"session_id": session.id, "title": session.title}
