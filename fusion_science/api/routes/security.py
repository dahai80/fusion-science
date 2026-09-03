from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...utils.keychain import SecureConfig
from ...utils.malware_scan import scan_bytes
from ..auth import describe_api_keys, load_api_keys

logger = logging.getLogger(__name__)
router = APIRouter()

_secure_config = SecureConfig()


class StoreKeyRequest(BaseModel):
    key_name: str
    value: str


class DeleteKeyRequest(BaseModel):
    key_name: str


class ScanArtifactRequest(BaseModel):
    # G3: scan an uploaded artifact (paper PDF, dataset) for malware indicators
    # before it is persisted. The blob arrives base64-encoded (JSON body), so a
    # caller can pre-screen any fetch/upload without a multipart dependency.
    filename: str = ""
    content_b64: str


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


@router.post("/rotate-keys")
async def rotate_api_keys(request: Request):
    # F-ENT-ROTATE: runtime key rotation without process restart. Middleware
    # re-reads provisioned keys per request, so an operator only needs to
    # rewrite the key file (FUSION_SCIENCE_API_KEYS_FILE) or change the env
    # in the process supervisor — this endpoint confirms the reload took and
    # records who triggered it. Admin-only (RBAC: only admin reaches the
    # security route prefix). No env mutation over HTTP on purpose: secrets
    # must never cross the wire inbound.
    principal = getattr(request.state, "principal", None)
    actor = getattr(principal, "subject", "unknown") if principal else "unknown"
    keys = load_api_keys()
    summary = describe_api_keys(keys)
    logger.info("API key rotation triggered by %s — active keys: %d", actor, summary["total"])
    return {"rotated": True, "actor": actor, **summary}


@router.post("/scan")
async def scan_artifact(body: ScanArtifactRequest):
    # G3: malware-scan an uploaded/fetched artifact blob. Returns the ScanResult
    # (clean + flags + scanned_bytes). The caller decides whether a flagged
    # result blocks ingestion (fail-closed for uploads). Admin-only via RBAC
    # (the security prefix is not in the science/viewer map).
    try:
        raw = base64.b64decode(body.content_b64)
    except Exception as exc:
        logger.warning("Artifact scan: bad base64 payload: %s", exc)
        return {"error": "content_b64 is not valid base64"}
    result = scan_bytes(raw, filename=body.filename)
    return {
        "clean": result.clean,
        "flags": result.flags,
        "scanned_bytes": result.scanned_bytes,
        "filename": body.filename,
    }
