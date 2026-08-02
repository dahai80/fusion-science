from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class SetModelRequest(BaseModel):
    model: str


class SetModelRoleRequest(BaseModel):
    role: str
    model: str


@router.get("")
async def list_models(request: Request):
    gateway = getattr(request.app.state, "gateway", None)
    if not gateway:
        return {"models": [], "error": "gateway not initialized"}
    try:
        models = await gateway.refresh_available_models()
        return {"models": models, "current": gateway.model}
    except Exception as e:
        logger.error("Failed to list models: %s", e)
        return {"models": [], "error": str(e)}


@router.put("/current")
async def set_current_model(request: Request, body: SetModelRequest):
    gateway = getattr(request.app.state, "gateway", None)
    if not gateway:
        return {"error": "gateway not initialized"}
    gateway.set_model(body.model)
    return {"model": gateway.model}


@router.get("/roles")
async def get_model_roles(request: Request):
    gateway = getattr(request.app.state, "gateway", None)
    if not gateway:
        return {"roles": {}, "error": "gateway not initialized"}
    return {"roles": gateway.get_model_roles(), "current": gateway.model}


@router.put("/roles")
async def set_model_role(request: Request, body: SetModelRoleRequest):
    gateway = getattr(request.app.state, "gateway", None)
    if not gateway:
        return {"error": "gateway not initialized"}
    gateway.set_model_for_role(body.role, body.model)
    return {"role": body.role, "model": body.model}
