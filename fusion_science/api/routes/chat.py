from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ...core.gateway import LLMGateway
from ..sse import sse_response

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    stream: bool = False


@router.post("/chat")
async def chat(session_id: str, request: Request, body: ChatRequest) -> dict[str, Any] | Any:
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        return {"error": "session_not_found", "session_id": session_id}

    await mgr.add_message(session_id, "user", body.message)

    gateway: LLMGateway = request.app.state.gateway
    context_manager = getattr(request.app.state, "context_manager", None)
    if context_manager:
        messages = context_manager.fit(session_id)
        await context_manager.maybe_compress(session_id)
    else:
        messages = mgr.get_messages(session_id)

    if body.stream:
        tokens = gateway.chat_stream(messages=messages)
        return sse_response(tokens)

    result = await gateway.chat(messages=messages)
    await mgr.add_message(session_id, "assistant", result.content)
    return {
        "session_id": session_id,
        "response": result.content,
        "model": result.model,
    }
