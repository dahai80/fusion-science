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
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=10000)
    stream: bool = False


@router.post("")
async def chat(request: Request, body: ChatRequest) -> dict[str, Any] | Any:
    mgr = request.app.state.session_manager
    session = mgr.get_session(body.session_id)
    if not session:
        return {"error": "session_not_found", "session_id": body.session_id}

    await mgr.add_message(body.session_id, "user", body.message)

    gateway: LLMGateway = request.app.state.gateway
    messages = mgr.get_messages(body.session_id)

    if body.stream:
        tokens = gateway.chat_stream(messages=messages)
        return sse_response(tokens)

    result = await gateway.chat(messages=messages)
    await mgr.add_message(body.session_id, "assistant", result.content)
    return {
        "session_id": body.session_id,
        "response": result.content,
        "model": result.model,
    }
