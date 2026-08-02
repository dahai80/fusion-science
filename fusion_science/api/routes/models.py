# api/routes/models.py — GET /api/v1/models
# Importers: api/app.py includes router; called by fusion-studio ScienceBridge
# API: returns list of available LLM models from fusion-mlx
# Data: calls LLMGateway.list_models() -> list[dict]
# User instruction: "继续实施下一个阶段"

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_models(request: Request):
    gateway = getattr(request.app.state, "gateway", None)
    if not gateway:
        return {"models": [], "error": "gateway not initialized"}
    try:
        models = await gateway.list_models()
        return {"models": models}
    except Exception as e:
        logger.error("Failed to list models: %s", e)
        return {"models": [], "error": str(e)}
