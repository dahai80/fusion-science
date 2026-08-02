from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class IntegrityIssue:
    severity: str
    category: str
    description: str
    entry_id: str = ""
    suggestion: str = ""


@dataclass
class IntegrityReport:
    session_id: str
    total_entries: int = 0
    traced_operations: int = 0
    coverage_percent: float = 0.0
    issues: list[IntegrityIssue] = field(default_factory=list)
    passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_entries": self.total_entries,
            "traced_operations": self.traced_operations,
            "coverage_percent": round(self.coverage_percent, 1),
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "description": i.description,
                    "entry_id": i.entry_id,
                    "suggestion": i.suggestion,
                }
                for i in self.issues
            ],
            "passed": self.passed,
        }


_REQUIRED_OPERATION_TYPES = {
    "db_query", "code_execution", "llm_call", "visualization",
}


class AuditIntegrityChecker:
    def __init__(self, required_ops: set[str] | None = None):
        self._required_ops = required_ops or _REQUIRED_OPERATION_TYPES

    def check_session(self, session: Any) -> IntegrityReport:
        report = IntegrityReport(session_id=getattr(session, "session_id", "unknown"))

        if session is None:
            report.issues.append(IntegrityIssue(
                severity="critical",
                category="missing_session",
                description="No active trace session found",
                suggestion="Start a trace session before operations",
            ))
            report.passed = False
            return report

        entries = getattr(session, "entries", [])
        report.total_entries = len(entries)

        found_ops: set[str] = set()
        entry_ids: set[str] = set()
        parent_ids: set[str] = set()

        for entry in entries:
            eid = getattr(entry, "id", "")
            op = getattr(entry, "operation", "")
            pid = getattr(entry, "parent_id", "")

            entry_ids.add(eid)
            found_ops.add(op)
            if pid:
                parent_ids.add(pid)

            if not getattr(entry, "success", True):
                err = getattr(entry, "error", "")
                if not err:
                    report.issues.append(IntegrityIssue(
                        severity="warning",
                        category="missing_error_detail",
                        description=f"Entry {eid} marked failed but no error message",
                        entry_id=eid,
                        suggestion="Record error details on failure",
                    ))

            params = getattr(entry, "parameters", {})
            if not params and op in ("db_query", "code_execution"):
                report.issues.append(IntegrityIssue(
                    severity="warning",
                    category="missing_parameters",
                    description=f"Entry {eid} ({op}) has no parameters recorded",
                    entry_id=eid,
                    suggestion="Ensure all operations record their parameters",
                ))

            duration = getattr(entry, "duration_ms", 0.0)
            if duration == 0.0 and op in ("db_query", "code_execution", "llm_call"):
                report.issues.append(IntegrityIssue(
                    severity="info",
                    category="missing_duration",
                    description=f"Entry {eid} ({op}) has no duration recorded",
                    entry_id=eid,
                    suggestion="Record operation duration for reproducibility",
                ))

        for pid in parent_ids:
            if pid and pid not in entry_ids:
                report.issues.append(IntegrityIssue(
                    severity="critical",
                    category="broken_parent_ref",
                    description=f"Parent entry {pid} referenced but not found",
                    suggestion="Ensure parent entries exist before referencing",
                ))

        missing_ops = self._required_ops - found_ops
        report.traced_operations = len(found_ops)
        if self._required_ops:
            report.coverage_percent = (len(found_ops & self._required_ops) / len(self._required_ops)) * 100
        else:
            report.coverage_percent = 100.0

        for missing in missing_ops:
            report.issues.append(IntegrityIssue(
                severity="warning",
                category="missing_operation_type",
                description=f"No '{missing}' operations recorded in this session",
                suggestion=f"Ensure {missing} operations are captured via EventBus",
            ))

        critical_count = sum(1 for i in report.issues if i.severity == "critical")
        if critical_count > 0 or report.coverage_percent < 50:
            report.passed = False

        logger.info(
            "Audit integrity check: session=%s, entries=%d, coverage=%.0f%%, issues=%d, passed=%s",
            report.session_id, report.total_entries, report.coverage_percent,
            len(report.issues), report.passed,
        )
        return report

    def check_provenance_chain(self, graph: Any) -> IntegrityReport:
        report = IntegrityReport(session_id="provenance")

        if graph is None:
            report.issues.append(IntegrityIssue(
                severity="critical",
                category="missing_graph",
                description="No provenance graph available",
            ))
            report.passed = False
            return report

        nodes = getattr(graph, "nodes", {})
        report.total_entries = len(nodes)

        for nid, node in nodes.items():
            inputs = getattr(node, "inputs", [])
            for input_id in inputs:
                if input_id not in nodes:
                    report.issues.append(IntegrityIssue(
                        severity="critical",
                        category="broken_lineage",
                        description=f"Node {nid} references missing input {input_id}",
                        entry_id=nid,
                        suggestion="Ensure all input nodes are recorded before transformation",
                    ))

            node_type = getattr(node, "type", "")
            if node_type == "output" and not inputs:
                report.issues.append(IntegrityIssue(
                    severity="warning",
                    category="orphan_output",
                    description=f"Output node {nid} has no inputs (orphan)",
                    entry_id=nid,
                    suggestion="Link output to its source transformation",
                ))

            if node_type == "transformation" and not inputs:
                report.issues.append(IntegrityIssue(
                    severity="info",
                    category="root_transformation",
                    description=f"Transformation {nid} has no inputs (acts as source)",
                    entry_id=nid,
                ))

        critical_count = sum(1 for i in report.issues if i.severity == "critical")
        report.passed = critical_count == 0
        report.traced_operations = report.total_entries
        report.coverage_percent = 100.0 if report.total_entries > 0 else 0.0

        logger.info(
            "Provenance integrity check: nodes=%d, issues=%d, passed=%s",
            report.total_entries, len(report.issues), report.passed,
        )
        return report
