from __future__ import annotations

import contextlib
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ...audit.compliance import ComplianceChecker
from ...compute.code_generator import CodeGenerator
from ...compute.jupyter_kernel import JupyterKernelManager

logger = logging.getLogger(__name__)
router = APIRouter()


class CodeGenRequest(BaseModel):
    query: str
    language: str = "python"


class CodeGenBatchRequest(BaseModel):
    queries: list[str]
    language: str = "python"


class JupyterExecuteRequest(BaseModel):
    code: str = Field(..., max_length=200_000)
    timeout: int = Field(default=120, ge=1, le=300)


class ComplianceCheckRequest(BaseModel):
    session_id: str = ""
    usage_context: str = "personal"


@router.post("/code-gen")
async def generate_code(request: Request, body: CodeGenRequest):
    gateway = getattr(request.app.state, "gateway", None)
    gen = CodeGenerator(gateway=gateway)
    try:
        result = await gen.generate(body.query, body.language)
        return {
            "code": result.code,
            "language": result.language,
            "confidence": result.confidence,
            "packages": result.packages,
        }
    except Exception as e:
        logger.error("Code gen failed: %s", e)
        return {"error": str(e)}


@router.post("/code-gen/batch")
async def generate_code_batch(request: Request, body: CodeGenBatchRequest):
    gateway = getattr(request.app.state, "gateway", None)
    gen = CodeGenerator(gateway=gateway)
    try:
        results = await gen.generate_batch(body.queries, body.language)
        return {"results": [{"code": r.code, "language": r.language, "confidence": r.confidence} for r in results]}
    except Exception as e:
        logger.error("Batch code gen failed: %s", e)
        return {"error": str(e)}


@router.post("/jupyter/execute")
async def jupyter_execute(request: Request, body: JupyterExecuteRequest):
    mgr = JupyterKernelManager()
    try:
        started = await mgr.start_kernel()
        if not started:
            return {"error": "Failed to start Jupyter kernel"}
        result = await mgr.execute(body.code, timeout=body.timeout)
        await mgr.shutdown()
        return {
            "output": result.output,
            "error": result.error,
            "mime_data": result.mime_data,
            "success": result.success,
            "execution_count": result.execution_count,
        }
    except Exception as e:
        logger.error("Jupyter execute failed: %s", e)
        with contextlib.suppress(Exception):
            await mgr.shutdown()
        return {"error": str(e)}


@router.get("/jupyter/kernels")
async def list_jupyter_kernels():
    kernels = JupyterKernelManager.list_available_kernels()
    return {"kernels": [{"name": k.name, "display_name": k.display_name, "language": k.language} for k in kernels]}


@router.post("/compliance")
async def check_compliance(request: Request, body: ComplianceCheckRequest):
    checker = ComplianceChecker()
    trace_entries = []
    if body.session_id:
        session = request.app.state.session_manager.get_session(body.session_id)
        if session:
            recorder = getattr(request.app.state, "recorder", None)
            if recorder:
                trace_entries = recorder.get_traces(session_id=body.session_id)

    report = checker.check_report(
        session_id=body.session_id or "api",
        trace_entries=trace_entries or None,
        usage_context=body.usage_context,
    )
    return report
