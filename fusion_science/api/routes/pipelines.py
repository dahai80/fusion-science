# api/routes/pipelines.py — GET /api/v1/pipelines, POST /api/v1/pipelines/{name}/run
# Importers: api/app.py includes router; called by fusion-studio ScienceBridge
# API: PipelineRunRequest(query, max_iterations) -> pipeline execution result
# Data: PipelineFactory.TEMPLATES keys = literature_review, bioinformatics_analysis, molecular_analysis
# User instruction: "继续实施下一个阶段"

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ...core.pipeline import PipelineFactory

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_pipelines():
    templates = PipelineFactory.list_templates()
    return {"pipelines": templates, "total": len(templates)}


class PipelineRunRequest(BaseModel):
    query: str
    max_iterations: int = Field(default=10, ge=1, le=50)


@router.post("/{name}/run")
async def run_pipeline(name: str, req: PipelineRunRequest, request: Request):
    engine = getattr(request.app.state, "gateway", None)
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if not engine:
        return {"error": "LLM engine not available"}
    factory = PipelineFactory(engine=engine, tool_registry=tool_registry)
    template = PipelineFactory.TEMPLATES.get(name)
    if not template:
        available = list(PipelineFactory.TEMPLATES.keys())
        return {"error": f"Pipeline '{name}' not found", "available": available}
    try:
        pipeline = factory.create_pipeline(name)
        result = await pipeline.sequential(
            [a.name for a in template.agents],
            req.query,
        )
        return {
            "pipeline": name,
            "task": result.task,
            "summary": result.summary,
            "agent_count": len(result.agent_results),
            "duration": result.total_duration,
            "results": [
                {"agent": r.agent_name, "output": r.output[:2000], "error": r.error, "duration": r.duration}
                for r in result.agent_results
            ],
        }
    except Exception as e:
        logger.error("Pipeline run failed: %s", e)
        return {"error": str(e), "pipeline": name}
