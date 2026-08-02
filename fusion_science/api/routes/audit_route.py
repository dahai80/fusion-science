# api/routes/audit_route.py — GET /api/v1/sessions/{session_id}/audit
# Importers: api/app.py includes router
# API: Returns trace entries + compliance check results for a session
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ...audit.compliance import ComplianceChecker

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def get_audit(session_id: str, request: Request):
    recorder = getattr(request.app.state, "recorder", None)
    trace_entries = []
    if recorder:
        trace_entries = recorder.get_entries()

    checker = ComplianceChecker()
    report = checker.check_report(
        session_id=session_id,
        trace_entries=trace_entries,
    )
    return {"session_id": session_id, "trace_count": len(trace_entries), "compliance": report}
