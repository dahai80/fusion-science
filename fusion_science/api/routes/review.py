# api/routes/review.py — POST /api/v1/review
# Importers: api/app.py includes router
# API: ReviewRequest(query, max_papers, max_iterations) → LiteratureAgent result
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class ReviewRequest(BaseModel):
    query: str
    max_papers: int = Field(default=20, ge=1, le=100)
    max_iterations: int = Field(default=10, ge=1, le=50)


@router.post("")
async def review(req: ReviewRequest, request: Request):
    router_agent = getattr(request.app.state, "router_agent", None)
    if not router_agent:
        return {"error": "router_agent not available"}
    lit_agent = router_agent.get_agent("literature")
    if not lit_agent:
        return {"error": "literature agent not available"}
    result = await lit_agent.run(req.query, max_iterations=req.max_iterations)
    return {
        "agent": result.agent_name,
        "output": result.output,
        "error": result.error,
        "duration": result.duration,
    }
