# api/routes/analysis.py — POST /api/v1/sessions/{id}/analyze
# Importers: api/app.py includes router; consumed by fusion-studio ScienceBridge
# API: AnalysisRequest(query, language, max_iterations) -> data agent result
# Issue #7: 注入前序 search 上下文，analyze 可引用 search 结果

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...session.models import Artifact
from .._owner import check_owner
from ._context import build_context_prompt

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalysisRequest(BaseModel):
    # R-7: cap the user query so it cannot exhaust the LLM context budget.
    query: str = Field(..., max_length=8000)
    language: str = Field(default="python", pattern="^(python|r)$")
    max_iterations: int = Field(default=10, ge=1, le=50)


@router.post("/analyze")
async def analyze(session_id: str, request: Request, req: AnalysisRequest):
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    denied = check_owner(request, session)
    if denied:
        return denied

    router_agent = getattr(request.app.state, "router_agent", None)
    if not router_agent:
        # R-8: missing infra is a server error, not a 200 with an error string.
        raise HTTPException(status_code=503, detail="router_agent not available")
    data_agent = router_agent.get_agent("data")
    if not data_agent:
        raise HTTPException(status_code=503, detail="data agent not available")

    task = build_context_prompt(session, "analyze", req.query)
    result = await data_agent.run(task, max_iterations=req.max_iterations)

    try:
        artifact = Artifact(
            id=f"analyze_{int(time.time())}",
            type="analysis_result",
            name=req.query[:80],
            content=(result.output or "")[:4000],
            metadata={"language": req.language, "error": result.error, "duration": result.duration},
        )
        await mgr.add_artifact(session_id, artifact)
        logger.info("analyze: result stored as artifact in session %s", session_id)
    except Exception as e:
        logger.warning("analyze: failed to persist artifact into session %s: %s", session_id, e)

    return {
        "session_id": session_id,
        "agent": result.agent_name,
        "output": result.output,
        "error": result.error,
        "duration": result.duration,
        "context_used": task != req.query,
    }
