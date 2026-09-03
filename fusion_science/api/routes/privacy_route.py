from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.delete("/data-subject/{subject_id}")
async def purge_data_subject(request: Request, subject_id: str) -> JSONResponse:
    # G9 DSAR / right-to-erasure (GDPR Art.17 right to erasure; HIPAA purge).
    # Admin-only (RBAC: the `data-subject` prefix is not in the science/viewer
    # permission map, so role_allows denies them). Deletes every session owned
    # by `subject_id` across the shared store (single-node or Postgres HA), and
    # best-effort removes each session's per-session audit trace file.
    #
    # Idempotent: re-invoking on an already-purged subject returns an empty
    # list, not an error. Returns the purged session ids so the operator/DPO
    # has a record of what was removed (the audit trail of the erasure itself
    # is recorded by the TraceRecorder before the session audit files drop).
    mgr = getattr(request.app.state, "session_manager", None)
    if mgr is None:
        return JSONResponse(status_code=503, content={"detail": "session store not initialized"})
    try:
        purged = await mgr.purge_subject(subject_id)
    except Exception as e:
        logger.exception("DSAR purge failed for subject=%s", subject_id)
        return JSONResponse(status_code=500, content={"detail": f"erasure failed: {e}"[:200]})
    logger.info("DSAR erasure: subject=%s purged=%d sessions %s", subject_id, len(purged), purged)
    return JSONResponse(
        status_code=200,
        content={
            "subject": subject_id,
            "purged_sessions": purged,
            "count": len(purged),
        },
    )


@router.get("/data-subject/{subject_id}/sessions")
async def list_data_subject_sessions(request: Request, subject_id: str) -> JSONResponse:
    # G9 helper for GDPR Art.15 right of access: list all sessions for a subject
    # so a DPO can respond to a data-subject access request. Admin-only (same
    # RBAC prefix gate as the DELETE). Returns session metadata, not message
    # bodies — the DPO can then GET individual sessions if full content is needed.
    mgr = getattr(request.app.state, "session_manager", None)
    if mgr is None:
        return JSONResponse(status_code=503, content={"detail": "session store not initialized"})
    sessions = mgr.list_sessions(owner=subject_id, limit=1000)
    return JSONResponse(
        status_code=200,
        content={
            "subject": subject_id,
            "count": len(sessions),
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title,
                    "owner": s.owner,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                }
                for s in sessions
            ],
        },
    )
