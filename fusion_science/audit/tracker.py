"""Operation trace recorder — tracks every action in the scientific workflow.

Provides a complete audit trail of all operations performed during a
research session, including:
- Database queries and their parameters
- Code execution and results
- LLM interactions and generated content
- Visualization generation
- File operations and outputs
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Sensitive field patterns — values matching these keys are redacted in audit logs
_SENSITIVE_PATTERNS = ["patient", "身份证", "姓名", "phone", "email", "password", "token", "secret", "api_key", "私钥"]


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive parameter values before logging.

    Args:
        params: Original parameters dict.

    Returns:
        Sanitized copy with sensitive values replaced by ***REDACTED***.
    """
    sanitized = {}
    for k, v in params.items():
        if any(p in k.lower() for p in _SENSITIVE_PATTERNS):
            sanitized[k] = "***REDACTED***"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_params(v)  # Recurse nested dicts
        elif isinstance(v, str) and len(v) > 1000:
            sanitized[k] = v[:1000] + "... [truncated]"  # Truncate long strings
        else:
            sanitized[k] = v
    return sanitized


@dataclass
class TraceEntry:
    """A single trace entry recording an operation."""

    id: str
    timestamp: float
    operation: str  # db_query, code_execution, llm_call, visualization, file_write
    source: str  # module name
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""
    parent_id: str = ""  # For hierarchical tracing


@dataclass
class TraceSession:
    """A complete tracing session for a research workflow."""

    session_id: str
    start_time: float
    end_time: float = 0.0
    entries: list[TraceEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "active"  # active, completed, failed


class TraceRecorder:
    """Records and manages a complete audit trail of all operations.

    Creates a trace session for each research workflow, tracking
    every operation with full parameter and result context.
    """

    def __init__(self, storage_dir: str = "~/.cache/fusion-science/traces"):
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._session: TraceSession | None = None
        self._current_parent: str = ""

    def start_session(self, metadata: dict[str, Any] | None = None) -> str:
        """Start a new tracing session.

        Args:
            metadata: Optional session metadata (e.g., task description, user).

        Returns:
            Session ID.
        """
        session_id = f"trace_{uuid.uuid4().hex[:12]}"
        self._session = TraceSession(
            session_id=session_id,
            start_time=time.time(),
            metadata=metadata or {},
        )
        logger.info("Started trace session: %s", session_id)
        return session_id

    def end_session(self, status: str = "completed") -> TraceSession:
        """End the current tracing session.

        Args:
            status: Final status (completed, failed).

        Returns:
            The completed TraceSession.
        """
        if self._session is None:
            raise RuntimeError("No active session to end.")

        self._session.end_time = time.time()
        self._session.status = status
        self._save_session()

        logger.info(
            "Ended trace session: %s (%d entries, %.1fs)",
            self._session.session_id,
            len(self._session.entries),
            self._session.end_time - self._session.start_time,
        )
        session = self._session
        self._session = None  # Release reference so GC can collect
        return session

    def record(
        self,
        operation: str,
        source: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        result_summary: str = "",
        duration_ms: float = 0.0,
        success: bool = True,
        error: str = "",
    ) -> str:
        """Record a trace entry.

        Args:
            operation: Operation type (db_query, code_execution, llm_call, etc.).
            source: Module name.
            description: Human-readable description.
            parameters: Operation parameters (for audit).
            result_summary: Summary of the result.
            duration_ms: Execution duration in milliseconds.
            success: Whether the operation succeeded.
            error: Error message if failed.

        Returns:
            Entry ID.
        """
        if self._session is None:
            self.start_session()

        entry_id = f"entry_{uuid.uuid4().hex[:8]}"
        entry = TraceEntry(
            id=entry_id,
            timestamp=time.time(),
            operation=operation,
            source=source,
            description=description,
            parameters=_sanitize_params(parameters or {}),  # Sanitize before recording
            result_summary=result_summary,
            duration_ms=duration_ms,
            success=success,
            error=error,
            parent_id=self._current_parent,
        )
        self._session.entries.append(entry)
        return entry_id

    def set_parent(self, parent_id: str) -> None:
        """Set the current parent entry ID for hierarchical tracing.

        Args:
            parent_id: Parent entry ID.
        """
        self._current_parent = parent_id

    def clear_parent(self) -> None:
        """Clear the current parent entry."""
        self._current_parent = ""

    def record_db_query(
        self,
        source: str,
        database: str,
        query: str,
        result_count: int,
        success: bool = True,
        error: str = "",
        duration_ms: float = 0.0,
    ) -> str:
        """Record a database query operation.

        Args:
            source: Calling module.
            database: Database name (pubmed, uniprot, etc.).
            query: Query string.
            result_count: Number of results returned.
            success: Whether the query succeeded.
            error: Error message if failed.
            duration_ms: Query duration.

        Returns:
            Entry ID.
        """
        return self.record(
            operation="db_query",
            source=source,
            description=f"Query {database}: {query[:100]}",
            parameters={"database": database, "query": query},
            result_summary=f"{result_count} results",
            duration_ms=duration_ms,
            success=success,
            error=error,
        )

    def record_code_execution(
        self,
        source: str,
        language: str,
        code_summary: str,
        success: bool = True,
        error: str = "",
        duration_ms: float = 0.0,
        output_files: list[str] | None = None,
    ) -> str:
        """Record a code execution operation.

        Args:
            source: Calling module.
            language: Programming language (python, r, julia).
            code_summary: Brief description of the code.
            success: Whether execution succeeded.
            error: Error message if failed.
            duration_ms: Execution duration.
            output_files: Paths to output files.

        Returns:
            Entry ID.
        """
        return self.record(
            operation="code_execution",
            source=source,
            description=f"Execute {language}: {code_summary}",
            parameters={"language": language, "output_files": output_files or []},
            result_summary="Success" if success else f"Failed: {error[:100]}",
            duration_ms=duration_ms,
            success=success,
            error=error,
        )

    def record_llm_call(
        self,
        source: str,
        model: str,
        prompt_summary: str,
        response_summary: str = "",
        success: bool = True,
        error: str = "",
        duration_ms: float = 0.0,
        token_usage: dict[str, int] | None = None,
    ) -> str:
        """Record an LLM call.

        Args:
            source: Calling module.
            model: Model name.
            prompt_summary: Summary of the prompt.
            response_summary: Summary of the response.
            success: Whether the call succeeded.
            error: Error message if failed.
            duration_ms: Call duration.
            token_usage: Token usage statistics.

        Returns:
            Entry ID.
        """
        return self.record(
            operation="llm_call",
            source=source,
            description=f"LLM ({model}): {prompt_summary[:100]}",
            parameters={"model": model, "token_usage": token_usage or {}},
            result_summary=response_summary[:200],
            duration_ms=duration_ms,
            success=success,
            error=error,
        )

    def record_visualization(
        self,
        source: str,
        viz_type: str,
        file_path: str,
        success: bool = True,
        error: str = "",
        duration_ms: float = 0.0,
    ) -> str:
        """Record a visualization generation.

        Args:
            source: Calling module.
            viz_type: Type of visualization (chart, molecule, protein).
            file_path: Path to the generated file.
            success: Whether generation succeeded.
            error: Error message if failed.
            duration_ms: Generation duration.

        Returns:
            Entry ID.
        """
        return self.record(
            operation="visualization",
            source=source,
            description=f"Generate {viz_type}: {os.path.basename(file_path)}",
            parameters={"type": viz_type, "file_path": file_path},
            result_summary=f"File: {file_path}",
            duration_ms=duration_ms,
            success=success,
            error=error,
        )

    def get_session(self) -> TraceSession | None:
        """Get the current trace session.

        Returns:
            Current TraceSession or None.
        """
        return self._session

    def get_entries(self, operation: str | None = None) -> list[TraceEntry]:
        """Get trace entries, optionally filtered by operation type.

        Args:
            operation: Optional operation type filter.

        Returns:
            List of matching TraceEntry objects.
        """
        if self._session is None:
            return []
        if operation:
            return [e for e in self._session.entries if e.operation == operation]
        return self._session.entries

    def get_session_summary(self, session_id: str | None = None) -> dict[str, Any]:
        """Get a summary of a trace session.

        Args:
            session_id: Session ID (default: current session).

        Returns:
            Summary dict with counts and timing.
        """
        session = self._get_session(session_id)
        if session is None:
            return {"error": "Session not found"}

        duration = (session.end_time or time.time()) - session.start_time
        op_counts: dict[str, int] = {}
        success_count = 0
        fail_count = 0

        for entry in session.entries:
            op_counts[entry.operation] = op_counts.get(entry.operation, 0) + 1
            if entry.success:
                success_count += 1
            else:
                fail_count += 1

        return {
            "session_id": session.session_id,
            "status": session.status,
            "duration_seconds": round(duration, 2),
            "total_entries": len(session.entries),
            "operations": op_counts,
            "successful": success_count,
            "failed": fail_count,
            "metadata": session.metadata,
        }

    def export_json(self, session_id: str | None = None, pretty: bool = True) -> str:
        """Export the trace session as JSON.

        Args:
            session_id: Session ID (default: current session).
            pretty: Pretty-print the JSON.

        Returns:
            JSON string of the session.
        """
        session = self._get_session(session_id)
        if session is None:
            return json.dumps({"error": "Session not found"})

        from dataclasses import asdict
        data = asdict(session)
        indent = 2 if pretty else None
        return json.dumps(data, indent=indent, default=str, ensure_ascii=False)

    def _get_session(self, session_id: str | None) -> TraceSession | None:
        """Get a session by ID, or the current session."""
        if session_id is None:
            return self._session

        # Try to load from storage
        session_path = self.storage_dir / f"{session_id}.json"
        if session_path.exists():
            with open(session_path, "r") as f:
                data = json.load(f)
            return TraceSession(**data)
        return None

    def _save_session(self) -> None:
        """Save the current session to disk."""
        if self._session is None:
            return

        export_path = self.storage_dir / f"{self._session.session_id}.json"
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(self.export_json())

        logger.info("Saved trace session to %s", export_path)

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all saved trace sessions.

        Returns:
            List of session summaries.
        """
        sessions = []
        for f in self.storage_dir.glob("trace_*.json"):
            try:
                with open(f, "r") as fh:
                    data = json.load(fh)
                sessions.append({
                    "session_id": data.get("session_id", ""),
                    "start_time": data.get("start_time", 0),
                    "status": data.get("status", "unknown"),
                    "entry_count": len(data.get("entries", [])),
                })
            except Exception:
                continue

        sessions.sort(key=lambda s: s["start_time"], reverse=True)
        return sessions