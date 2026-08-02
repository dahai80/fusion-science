from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    return {"status": "ok", "service": "fusion-science"}
