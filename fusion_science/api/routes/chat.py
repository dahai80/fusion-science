from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...core.gateway import LLMGateway
from .._owner import check_owner
from ..sse import sse_response

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    stream: bool = False
    use_agent: bool = True


@router.post("/chat")
async def chat(session_id: str, request: Request, body: ChatRequest) -> dict[str, Any] | Any:
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    denied = check_owner(request, session)
    if denied:
        return denied

    await mgr.add_message(session_id, "user", body.message)

    router_agent = getattr(request.app.state, "router_agent", None)
    context_manager = getattr(request.app.state, "context_manager", None)
    if context_manager:
        messages = context_manager.fit(session_id)
        await context_manager.maybe_compress(session_id)
    else:
        messages = mgr.get_messages(session_id)

    gateway: LLMGateway = request.app.state.gateway

    # Streaming path stays raw LLM (token replay through an agent loop is unsafe).
    if body.stream:
        tokens = gateway.chat_stream(messages=messages)
        return sse_response(tokens, request)

    # Non-stream: route through the agent system so tool capability is active
    # on the primary chat path. Fall back to raw gateway only if no router.
    if body.use_agent and router_agent:
        result = await router_agent.run(body.message, max_iterations=10)
        if result.error:
            logger.warning("chat agent error on session %s: %s", session_id, result.error)
            return JSONResponse(
                status_code=502,
                content={"detail": "agent_failed", "error": result.error[:500]},
            )
        content = result.output or ""
        await mgr.add_message(session_id, "assistant", content)
        return {
            "session_id": session_id,
            "response": content,
            "agent": result.agent_name,
            "model": getattr(gateway, "model", ""),
            "steps": len(result.steps),
        }

    result = await gateway.chat(messages=messages)
    if result.error:
        logger.warning("chat gateway error on session %s: %s", session_id, result.error)
        return JSONResponse(
            status_code=502,
            content={"detail": "gateway_failed", "error": result.error[:500]},
        )
    await mgr.add_message(session_id, "assistant", result.content)
    return {
        "session_id": session_id,
        "response": result.content,
        "model": result.model,
    }
