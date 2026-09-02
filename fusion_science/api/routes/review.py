# api/routes/review.py — POST /api/v1/sessions/{id}/review
# Importers: api/app.py includes router; consumed by fusion-studio ScienceBridge
# API: ReviewRequest(query, max_papers, max_iterations) -> literature agent result
# Issue #7: 注入前序 search + analyze 上下文，review 可引用前序结果

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ...session.models import Artifact
from .._owner import check_owner
from ._context import build_context_prompt

logger = logging.getLogger(__name__)
router = APIRouter()


class ReviewRequest(BaseModel):
    query: str
    max_papers: int = Field(default=20, ge=1, le=100)
    max_iterations: int = Field(default=10, ge=1, le=50)


@router.post("/review")
async def review(session_id: str, request: Request, req: ReviewRequest):
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    denied = check_owner(request, session)
    if denied:
        return denied

    router_agent = getattr(request.app.state, "router_agent", None)
    if not router_agent:
        return {"error": "router_agent not available"}
    lit_agent = router_agent.get_agent("literature")
    if not lit_agent:
        return {"error": "literature agent not available"}

    task = build_context_prompt(session, "review", req.query)
    result = await lit_agent.run(task, max_iterations=req.max_iterations)

    try:
        artifact = Artifact(
            id=f"review_{int(time.time())}",
            type="review_result",
            name=req.query[:80],
            content=(result.output or "")[:4000],
            metadata={"max_papers": req.max_papers, "error": result.error, "duration": result.duration},
        )
        await mgr.add_artifact(session_id, artifact)
        logger.info("review: result stored as artifact in session %s", session_id)
    except Exception as e:
        logger.warning("review: failed to persist artifact into session %s: %s", session_id, e)

    return {
        "session_id": session_id,
        "agent": result.agent_name,
        "output": result.output,
        "error": result.error,
        "duration": result.duration,
        "context_used": task != req.query,
    }
