# api/routes/audit_route.py — GET /api/v1/sessions/{session_id}/audit
# Importers: api/app.py includes router
# API: Returns trace entries + compliance check results for a session
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ...audit.compliance import ComplianceChecker
from ...audit.integrity import AuditIntegrityChecker
from .._owner import check_owner

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def get_audit(session_id: str, request: Request):
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    denied = check_owner(request, session)
    if denied:
        return denied
    recorder = getattr(request.app.state, "recorder", None)
    trace_entries = []
    if recorder:
        trace_entries = recorder.get_traces(session_id=session_id)

    checker = ComplianceChecker()
    report = checker.check_report(
        session_id=session_id,
        trace_entries=trace_entries or None,
    )
    return {"session_id": session_id, "trace_count": len(trace_entries), "compliance": report}


@router.get("/export", response_class=PlainTextResponse)
async def export_audit(session_id: str, request: Request):
    # F-ENT-AUDIT: JSONL (NDJSON) export for SIEM/ELK/Splunk ingest. One
    # TraceEntry per line, no wrapping array — the de-facto streaming format.
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    denied = check_owner(request, session)
    if denied:
        return denied
    recorder = getattr(request.app.state, "recorder", None)
    if not recorder:
        return PlainTextResponse("", status_code=404)
    body = recorder.export_jsonl(session_id=session_id)
    if not body:
        return PlainTextResponse("", status_code=404)
    return PlainTextResponse(body, media_type="application/x-ndjson")


@router.get("/integrity")
async def check_audit_integrity(session_id: str, request: Request):
    mgr = request.app.state.session_manager
    session = mgr.get_session(session_id)
    denied = check_owner(request, session)
    if denied:
        return denied
    recorder = getattr(request.app.state, "recorder", None)
    session = recorder.get_session() if recorder else None
    integrity_checker = AuditIntegrityChecker()
    report = integrity_checker.check_session(session)
    return report.to_dict()


@router.get("/provenance-integrity")
async def check_provenance_integrity(request: Request):
    provenance = getattr(request.app.state, "provenance_tracker", None)
    graph = provenance.get_graph() if provenance else None
    integrity_checker = AuditIntegrityChecker()
    report = integrity_checker.check_provenance_chain(graph)
    return report.to_dict()
