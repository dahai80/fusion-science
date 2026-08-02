# api/routes/analysis.py — POST /api/v1/analyze
# Importers: api/app.py includes router
# API: AnalysisRequest(query, language, max_iterations) -> DataAgent result
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalysisRequest(BaseModel):
    query: str
    language: str = Field(default="python", pattern="^(python|r)$")
    max_iterations: int = Field(default=10, ge=1, le=50)


@router.post("")
async def analyze(req: AnalysisRequest, request: Request):
    router_agent = getattr(request.app.state, "router_agent", None)
    if not router_agent:
        return {"error": "router_agent not available"}
    data_agent = router_agent.get_agent("data")
    if not data_agent:
        return {"error": "data agent not available"}
    result = await data_agent.run(req.query, max_iterations=req.max_iterations)
    return {
        "agent": result.agent_name,
        "output": result.output,
        "error": result.error,
        "duration": result.duration,
    }
