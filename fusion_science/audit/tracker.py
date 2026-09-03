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

import contextlib
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Sensitive field patterns — values matching these keys are redacted in audit logs
_SENSITIVE_PATTERNS = ["patient", "身份证", "姓名", "phone", "email", "password", "token", "secret", "api_key", "私钥"]


def _redaction_patterns() -> list[str]:
    # G7: extend the hardcoded sensitive-field list from env so a deployer can
    # add data-class-specific PII patterns (e.g. "mrn", "ssn", "医保号") without
    # a code change. FUSION_SCIENCE_REDACT_PATTERNS is comma-separated; merged
    # case-insensitively with the built-ins. Read fresh each call (live-rotate).
    extra = os.getenv("FUSION_SCIENCE_REDACT_PATTERNS", "")
    if not extra:
        return _SENSITIVE_PATTERNS
    return _SENSITIVE_PATTERNS + [p.strip() for p in extra.split(",") if p.strip()]


def _load_retention_map() -> dict[str, int]:
    # G8: per-data-class retention ages (days) from env.
    # FUSION_SCIENCE_RETENTION_MAP="ephi:2555,literature:365,audit:180"
    # A class age of 0 means retain indefinitely (never age-prune). Empty/absent
    # env -> empty map (all sessions fall back to the global max_age_days).
    raw = os.getenv("FUSION_SCIENCE_RETENTION_MAP", "")
    mapping: dict[str, int] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        cls, _, age = pair.partition(":")
        try:
            mapping[cls.strip()] = int(age)
        except ValueError:
            logger.warning("Retention map: bad age %r for class %r, skipping", age, cls)
    return mapping


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive parameter values before logging.

    Args:
        params: Original parameters dict.

    Returns:
        Sanitized copy with sensitive values replaced by ***REDACTED***.
    """
    patterns = _redaction_patterns()
    sanitized = {}
    for k, v in params.items():
        if any(p in k.lower() for p in patterns):
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
    prev_hash: str = ""  # hash chain: sha256 of previous entry's canonical json
    entry_hash: str = ""  # sha256 of this entry (excluding entry_hash itself)


@dataclass
class TraceSession:
    """A complete tracing session for a research workflow."""

    session_id: str
    start_time: float
    end_time: float = 0.0
    entries: list[TraceEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "active"  # active, completed, failed


@dataclass
class _ChainResult:
    # F-E12: structured chain-verification result exposing the broken entry ids.
    ok: bool
    mismatches: list[dict[str, str]] = field(default_factory=list)


class TraceRecorder:
    """Records and manages a complete audit trail of all operations.

    Creates a trace session for each research workflow, tracking
    every operation with full parameter and result context.
    """

    def __init__(
        self,
        storage_dir: str = "~/.cache/fusion-science/traces",
        max_age_days: int = 90,
        max_sessions: int = 1000,
        sink_url: str = "",
        encrypt_at_rest: bool = False,
        retention_map: dict[str, int] | None = None,
    ):
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # F-ENT-AUDIT: retention policy. Audit JSON files accumulate forever
        # without a cap — a long-running enterprise deploy fills the disk. Prune
        # sessions older than max_age_days and keep at most max_sessions (newest).
        # max_age_days<=0 disables age pruning; max_sessions<=0 disables count
        # pruning. Pruning runs once at startup (not per-record, to avoid the
        # cost on every audit event).
        self._max_age_days = max_age_days
        self._max_sessions = max_sessions
        # G8: per-data-class retention. GDPR/HIPAA require different retention
        # per data class (ePHI vs literature vs audit). A session whose metadata
        # carries a `data_class` is pruned by that class's age (days) when
        # present, else by the global max_age_days. A class age of 0 means
        # "retain indefinitely" (never age-prune that class). Loaded from env
        # FUSION_SCIENCE_RETENTION_MAP="ephi:2555,literature:365,audit:180".
        self._retention_map = retention_map or _load_retention_map()
        self._session: TraceSession | None = None
        self._current_parent: str = ""
        self._last_entry_hash: str = ""
        # Concurrency: record() mutates the shared session; guard with a lock.
        self._lock = threading.Lock()
        # F-7: persist incrementally so a crash mid-session doesn't lose the trail
        self._persist_every: int = 20
        self._records_since_persist: int = 0
        # F-ENT-HA-SINK (issue #24): central audit sink for multi-node HA. Each
        # node forwards every audit entry (NDJSON line) to a shared collector
        # (SIEM/ELK/HTTP log aggregator) so the full audit trail lives in one
        # place regardless of which node handled the request. Fire-and-forget
        # on a daemon thread — never blocks the request path, never raises into
        # it; a collector outage degrades to local-file-only audit (still
        # tamper-evident via the hash chain) rather than failing the operation.
        self._sink_url = sink_url
        # G1 encryption-at-rest: audit JSON files are AES-256-GCM enveloped on
        # write + decrypted on read (utils.crypto). Default off (local-first);
        # enable for HIPAA/等保三级 disk-encryption control. The envelope carries
        # a magic prefix so an existing plaintext store still reads back after
        # toggling the flag on (new writes encrypt, old reads stay plaintext).
        self._encrypt_at_rest = encrypt_at_rest
        with contextlib.suppress(Exception):
            self.prune()

    def _read_file(self, path: Path) -> str:
        # G1: read + decrypt when encrypt_at_rest is on. The decrypt helper is
        # a no-op on plaintext (no magic prefix), so this also works for a store
        # that was partly written before the flag was enabled.
        raw = path.read_bytes()
        if self._encrypt_at_rest:
            from ..utils.crypto import decrypt_bytes

            raw = decrypt_bytes(raw)
        return raw.decode("utf-8")

    def _write_file_atomic(self, path: Path, payload: str) -> None:
        # G1: encrypt payload bytes when encrypt_at_rest is on, then atomic write
        # (temp + os.replace) so a crash never leaves a half-written audit file.
        data = payload.encode("utf-8")
        if self._encrypt_at_rest:
            from ..utils.crypto import encrypt_bytes

            data = encrypt_bytes(data)
        fd, tmp_path = tempfile.mkstemp(dir=str(self.storage_dir), suffix=".tmp", prefix="trace_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp_path, path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            logger.error("Failed to persist trace session to %s", path)
            raise

    @staticmethod
    def _field(entry: Any, key: str) -> Any:
        # Entries are TraceEntry objects in a live session but plain dicts when
        # loaded back from storage (the dataclass field types are not coerced
        # on deserialize). Normalize access so chain verify/export work in both.
        return getattr(entry, key) if not isinstance(entry, dict) else entry.get(key, "")

    @staticmethod
    def _entry_hash(entry: TraceEntry) -> str:
        g = TraceRecorder._field
        payload = {
            "id": g(entry, "id"),
            "timestamp": g(entry, "timestamp"),
            "operation": g(entry, "operation"),
            "source": g(entry, "source"),
            "description": g(entry, "description"),
            "parameters": g(entry, "parameters"),
            "result_summary": g(entry, "result_summary"),
            "duration_ms": g(entry, "duration_ms"),
            "success": g(entry, "success"),
            "error": g(entry, "error"),
            "parent_id": g(entry, "parent_id"),
            "prev_hash": g(entry, "prev_hash"),
        }
        raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

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
        self._last_entry_hash = ""
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
            prev_hash=self._last_entry_hash,
        )
        entry.entry_hash = self._entry_hash(entry)
        with self._lock:
            self._session.entries.append(entry)
            self._last_entry_hash = entry.entry_hash
            self._records_since_persist += 1
            should_persist = self._records_since_persist >= self._persist_every
            if should_persist:
                self._records_since_persist = 0
        if should_persist:
            self._save_session()
        if self._sink_url:
            self._forward_to_sink(entry)
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

    def _forward_to_sink(self, entry: TraceEntry) -> None:
        # F-ENT-HA-SINK: push this entry as one NDJSON line to the central
        # audit collector. Runs on a daemon thread so the request path never
        # waits on the network or blocks on a dead collector. Failures are
        # logged once and swallowed — local hash-chain audit is the source of
        # truth; the sink is a fan-out for SIEM aggregation across HA nodes.
        session_id = self._session.session_id if self._session else ""
        try:
            line = self._entry_to_jsonl(entry, session_id)
        except Exception as e:
            logger.warning("audit sink: failed to serialize entry %s: %s", self._field(entry, "id"), e)
            return

        def _post() -> None:
            try:
                import httpx

                httpx.post(
                    self._sink_url, content=line + "\n", headers={"Content-Type": "application/x-ndjson"}, timeout=5.0
                )
            except Exception as e:
                logger.debug("audit sink post failed (non-fatal, local audit intact): %s", e)

        threading.Thread(target=_post, daemon=True).start()

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

    def get_traces(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Get trace entries as plain dicts, scoped to a request session_id.

        Filters entries whose parameters carry the given session_id, so one
        API session cannot read another session's audit trail (IDOR guard).
        Returns dicts (not TraceEntry) so compliance checkers can call .get().

        Args:
            session_id: Request session ID to scope entries to. If None, all
                entries of the current trace session are returned.

        Returns:
            List of trace entry dicts.
        """
        if self._session is None:
            return []
        entries: list[TraceEntry] = []
        if session_id:
            entries = [e for e in self._session.entries if str(e.parameters.get("session_id", "")) == session_id]
        else:
            entries = list(self._session.entries)
        return [
            {
                "id": e.id,
                "timestamp": e.timestamp,
                "operation": e.operation,
                "source": e.source,
                "description": e.description,
                "parameters": e.parameters,
                "result_summary": e.result_summary,
                "duration_ms": e.duration_ms,
                "success": e.success,
                "error": e.error,
                "parent_id": e.parent_id,
            }
            for e in entries
        ]

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
            data = json.loads(self._read_file(session_path))
            # Schema-tolerant deserialize: tolerate field additions/omissions
            try:
                return TraceSession(**data)
            except TypeError:
                entries = [
                    TraceEntry(
                        id=e.get("id", ""),
                        timestamp=e.get("timestamp", 0.0),
                        operation=e.get("operation", ""),
                        source=e.get("source", ""),
                        description=e.get("description", ""),
                        parameters=e.get("parameters", {}),
                        result_summary=e.get("result_summary", ""),
                        duration_ms=e.get("duration_ms", 0.0),
                        success=e.get("success", True),
                        error=e.get("error", ""),
                        parent_id=e.get("parent_id", ""),
                        prev_hash=e.get("prev_hash", ""),
                        entry_hash=e.get("entry_hash", ""),
                    )
                    for e in data.get("entries", [])
                ]
                return TraceSession(
                    session_id=data.get("session_id", ""),
                    start_time=data.get("start_time", 0.0),
                    end_time=data.get("end_time", 0.0),
                    entries=entries,
                    metadata=data.get("metadata", {}),
                    status=data.get("status", "unknown"),
                )
        return None

    def _save_session(self) -> None:
        """Save the current session to disk atomically (temp + rename)."""
        if self._session is None:
            return

        export_path = self.storage_dir / f"{self._session.session_id}.json"
        payload = self.export_json()
        self._write_file_atomic(export_path, payload)
        logger.info("Saved trace session to %s", export_path)

    def verify_chain(self, session_id: str | None = None) -> bool:
        """Verify the hash chain of a trace session is intact (tamper-evident)."""
        return self.audit_chain(session_id).ok

    def audit_chain(self, session_id: str | None = None) -> Any:
        # F-E12: verify_chain returned a bare bool and silently dropped WHICH
        # entry broke the chain. audit_chain returns the mismatch entry ids so
        # an operator can investigate tampering, while verify_chain stays a
        # bool for existing callers.
        session = self._get_session(session_id)
        mismatches: list[dict[str, str]] = []
        if session is None:
            return _ChainResult(ok=False, mismatches=[{"reason": "session_not_found"}])
        prev = ""
        g = self._field
        for entry in session.entries:
            eid = g(entry, "id")
            if g(entry, "prev_hash") != prev:
                mismatches.append({"entry_id": eid, "reason": "prev_hash_mismatch"})
                logger.error("Chain broken at entry %s: prev_hash mismatch", eid)
            recomputed = self._entry_hash(entry)
            if g(entry, "entry_hash") != recomputed:
                mismatches.append({"entry_id": eid, "reason": "entry_hash_mismatch"})
                logger.error("Chain broken at entry %s: entry_hash mismatch (tampered)", eid)
            prev = g(entry, "entry_hash")
        result = _ChainResult(ok=not mismatches, mismatches=mismatches)
        # G10: breach/tamper alerting. A broken audit hash chain is evidence of
        # tampering (or corruption) — 等保三级/HIPAA require an alert so an
        # operator can act, not just a log line. Fire a POST to a configured
        # webhook (FUSION_SCIENCE_TAMPER_ALERT_URL) carrying the session id +
        # mismatch details. Fire-and-forget on a daemon thread: never blocks
        # verification, never raises into the caller (a sink outage degrades to
        # the existing ERROR log + local file trail, still tamper-evident).
        if mismatches:
            self._fire_tamper_alert(session_id or (session.session_id if session else ""), mismatches)
        return result

    def _fire_tamper_alert(self, session_id: str, mismatches: list[dict[str, str]]) -> None:
        url = os.getenv("FUSION_SCIENCE_TAMPER_ALERT_URL", "")
        if not url:
            return
        payload = json.dumps(
            {"event": "audit_tamper_detected", "session_id": session_id, "mismatches": mismatches},
            ensure_ascii=False,
        ).encode("utf-8")

        def _send() -> None:
            try:
                import httpx

                with httpx.Client(timeout=5.0) as client:
                    resp = client.post(url, content=payload, headers={"Content-Type": "application/json"})
                    if resp.status_code >= 400:
                        logger.warning("Tamper-alert sink %s returned %s", url, resp.status_code)
            except Exception as exc:
                logger.warning("Tamper-alert delivery to %s failed (non-fatal): %s", url, exc)

        threading.Thread(target=_send, daemon=True).start()

    def _retention_age_for(self, path: Path) -> int | None:
        # G8: resolve the age (days) at which a session file should be pruned.
        # Reads the stored metadata `data_class` to look up a per-class age in
        # the retention map; falls back to the global max_age_days. Returns None
        # when neither applies (retain indefinitely). A read failure (corrupt/
        # encrypted-without-key file) falls back to the global age rather than
        # risking over-retention — and never raises into prune().
        data_class = ""
        with contextlib.suppress(Exception):
            session = self._read_file(path)
            meta = json.loads(session).get("metadata", {}) if session else {}
            if isinstance(meta, dict):
                data_class = str(meta.get("data_class", ""))
        if data_class and data_class in self._retention_map:
            return self._retention_map[data_class]
        return self._max_age_days if self._max_age_days > 0 else None

    def prune(self) -> dict[str, int]:
        """Apply the retention policy: drop sessions older than their retention
        age (per-data-class map or global max_age_days) and keep at most
        max_sessions (newest first).

        Returns a summary {pruned_by_age, pruned_by_count, remaining}.
        """
        pruned_age = 0
        pruned_count = 0
        files = sorted(
            self.storage_dir.glob("trace_*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        now = time.time()
        # Age-based pruning. G8: when a per-data-class retention map is set, a
        # session is pruned by ITS class's age (read from stored metadata
        # `data_class`), not the global max_age_days. A class age of 0 retains
        # that class indefinitely. Sessions whose class is unmapped (or whose
        # file has no data_class) fall back to the global max_age_days. When no
        # retention map is set this collapses to the original mtime-only prune.
        if self._max_age_days > 0 or self._retention_map:
            for f in files:
                age_days = self._retention_age_for(f)
                if age_days is None or age_days <= 0:
                    continue  # retain indefinitely (no global limit or class=0)
                if f.stat().st_mtime < now - age_days * 86400:
                    with contextlib.suppress(OSError):
                        f.unlink()
                    pruned_age += 1
            files = [f for f in files if f.exists()]
        # Count-based pruning (keep newest max_sessions).
        if self._max_sessions > 0 and len(files) > self._max_sessions:
            for f in files[self._max_sessions :]:
                with contextlib.suppress(OSError):
                    f.unlink()
                pruned_count += 1
        remaining = len([f for f in files if f.exists()])
        if pruned_age or pruned_count:
            logger.info(
                "Audit retention prune: %d by age, %d by count, %d remaining",
                pruned_age,
                pruned_count,
                remaining,
            )
        return {
            "pruned_by_age": pruned_age,
            "pruned_by_count": pruned_count,
            "remaining": remaining,
        }

    def export_jsonl(self, session_id: str | None = None) -> str:
        """Export trace entries as newline-delimited JSON (JSONL / NDJSON).

        JSONL is the de-facto SIEM/streaming ingest format (one record per
        line, no wrapping array) so an enterprise can ship audit events to a
        SIEM/ELK/Splunk pipeline without re-parsing. Each line is a single
        TraceEntry dict.
        """
        session = self._get_session(session_id)
        if session is None:
            return ""
        lines = []
        for entry in session.entries:
            # Entries may be TraceEntry objects (live session) or plain dicts
            # (loaded from storage, where dataclass field types aren't coerced).
            line = self._entry_to_jsonl(entry, session.session_id)
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _entry_to_jsonl(entry: Any, session_id: str) -> str:
        get = (lambda k: getattr(entry, k)) if not isinstance(entry, dict) else (lambda k: entry.get(k, ""))
        return json.dumps(
            {
                "session_id": session_id,
                "id": get("id"),
                "timestamp": get("timestamp"),
                "operation": get("operation"),
                "source": get("source"),
                "description": get("description"),
                "parameters": get("parameters"),
                "result_summary": get("result_summary"),
                "duration_ms": get("duration_ms"),
                "success": get("success"),
                "error": get("error"),
                "parent_id": get("parent_id"),
                "entry_hash": get("entry_hash"),
                "prev_hash": get("prev_hash"),
            },
            default=str,
            ensure_ascii=False,
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all saved trace sessions.

        Returns:
            List of session summaries.
        """
        sessions = []
        for f in self.storage_dir.glob("trace_*.json"):
            try:
                data = json.loads(self._read_file(f))
                sessions.append(
                    {
                        "session_id": data.get("session_id", ""),
                        "start_time": data.get("start_time", 0),
                        "status": data.get("status", "unknown"),
                        "entry_count": len(data.get("entries", [])),
                    }
                )
            except Exception:
                continue

        sessions.sort(key=lambda s: s["start_time"], reverse=True)
        return sessions
