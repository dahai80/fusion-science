from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from ...utils.keychain import SecureConfig

logger = logging.getLogger(__name__)
router = APIRouter()

_secure_config = SecureConfig()


class StoreKeyRequest(BaseModel):
    key_name: str
    value: str


class DeleteKeyRequest(BaseModel):
    key_name: str


@router.post("/keys")
async def store_api_key(body: StoreKeyRequest):
    ok = _secure_config.store(body.key_name, body.value)
    if ok:
        return {"stored": body.key_name}
    return {"error": f"Failed to store key '{body.key_name}'"}


@router.get("/keys")
async def list_api_keys():
    keys = _secure_config.list_stored_keys()
    return {"keys": keys, "total": len(keys)}


@router.get("/keys/{key_name}")
async def retrieve_api_key(key_name: str):
    val = _secure_config.retrieve(key_name)
    if val is None:
        return {"error": f"Key '{key_name}' not found"}
    return {"key_name": key_name, "exists": True}


@router.delete("/keys/{key_name}")
async def delete_api_key(key_name: str):
    ok = _secure_config.delete(key_name)
    if ok:
        return {"deleted": key_name}
    return {"error": f"Key '{key_name}' not found or delete failed"}
