# api/routes/visualize.py — POST /api/v1/visualize
# Importers: api/app.py includes router
# API: VisualizeRequest(query, chart_type, data_description, max_iterations) -> VizAgent result
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class VisualizeRequest(BaseModel):
    query: str
    chart_type: str = Field(default="scatter")
    data_description: str = Field(default="")
    max_iterations: int = Field(default=10, ge=1, le=50)


@router.post("")
async def visualize(req: VisualizeRequest, request: Request):
    router_agent = getattr(request.app.state, "router_agent", None)
    if not router_agent:
        return {"error": "router_agent not available"}
    viz_agent = router_agent.get_agent("visualize")
    if not viz_agent:
        return {"error": "visualize agent not available"}
    result = await viz_agent.run(req.query, max_iterations=req.max_iterations)
    return {
        "agent": result.agent_name,
        "output": result.output,
        "error": result.error,
        "duration": result.duration,
    }
