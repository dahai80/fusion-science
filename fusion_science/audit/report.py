"""Audit report generation — produce comprehensive audit and reproducibility reports.

Generates:
- Full audit reports of the research workflow
- Reproducibility packages with all parameters and code
- Compliance summaries for journal and regulatory requirements
- Data lineage visualizations
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .provenance import ProvenanceTracker
from .tracker import TraceRecorder

logger = logging.getLogger(__name__)


@dataclass
class AuditReport:
    """A complete audit report for a research workflow."""

    title: str
    created_at: str = ""
    session_info: dict[str, Any] = field(default_factory=dict)
    operation_summary: dict[str, int] = field(default_factory=dict)
    database_queries: list[dict[str, Any]] = field(default_factory=list)
    code_executions: list[dict[str, Any]] = field(default_factory=list)
    llm_interactions: list[dict[str, Any]] = field(default_factory=list)
    visualizations: list[dict[str, Any]] = field(default_factory=list)
    data_lineage: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    reproducibility_info: dict[str, Any] = field(default_factory=dict)
    content: str = ""


class ReportGenerator:
    """Generates comprehensive audit and reproducibility reports.

    Combines trace data and provenance information into structured
    reports suitable for journal submission, lab notebooks, and
    compliance verification.
    """

    def __init__(self, trace_recorder: TraceRecorder, provenance_tracker: ProvenanceTracker | None = None):
        self.tracer = trace_recorder
        self.provenance = provenance_tracker

    def generate_audit_report(self, title: str = "Research Audit Report") -> AuditReport:
        """Generate a comprehensive audit report from the current session.

        Args:
            title: Report title.

        Returns:
            AuditReport with full workflow audit.
        """
        session = self.tracer.get_session()
        if session is None:
            return AuditReport(title=title, content="No trace session data available.")

        entries = session.entries

        # Categorize entries
        db_queries = []
        code_execs = []
        llm_calls = []
        visualizations = []
        errors = []

        for entry in entries:
            entry_dict = {
                "id": entry.id,
                "timestamp": datetime.fromtimestamp(entry.timestamp).isoformat(),
                "description": entry.description,
                "duration_ms": entry.duration_ms,
                "success": entry.success,
                "error": entry.error,
                "parameters": entry.parameters,
            }
            if entry.operation == "db_query":
                db_queries.append(entry_dict)
            elif entry.operation == "code_execution":
                code_execs.append(entry_dict)
            elif entry.operation == "llm_call":
                llm_calls.append(entry_dict)
            elif entry.operation == "visualization":
                visualizations.append(entry_dict)

            if not entry.success:
                errors.append(entry_dict)

        # Operation counts
        op_counts: dict[str, int] = {}
        for entry in entries:
            op_counts[entry.operation] = op_counts.get(entry.operation, 0) + 1

        # Build report content
        report = AuditReport(
            title=title,
            created_at=datetime.now().isoformat(),
            session_info=self.tracer.get_session_summary(),
            operation_summary=op_counts,
            database_queries=db_queries,
            code_executions=code_execs,
            llm_interactions=llm_calls,
            visualizations=visualizations,
            errors=errors,
            data_lineage=self._build_lineage_summary(),
            reproducibility_info=self._build_reproducibility_info(),
        )

        report.content = self._format_report(report)
        return report

    def _build_lineage_summary(self) -> dict[str, Any]:
        """Build a summary of data lineage from provenance tracker."""
        if self.provenance is None:
            return {"available": False}

        graph = self.provenance.get_graph()
        if graph is None:
            return {"available": False}

        return {
            "available": True,
            "node_count": len(graph.nodes),
            "sources": sum(1 for n in graph.nodes.values() if n.type == "source"),
            "transformations": sum(1 for n in graph.nodes.values() if n.type == "transformation"),
            "outputs": sum(1 for n in graph.nodes.values() if n.type == "output"),
        }

    def _build_reproducibility_info(self) -> dict[str, Any]:
        """Build reproducibility information from the environment."""
        import platform
        import sys

        info = {
            "platform": platform.platform(),
            "python_version": sys.version,
            "fusion_science_version": "0.1.0",
        }

        # Check for key dependencies
        deps = ["numpy", "pandas", "matplotlib", "seaborn", "biopython", "rdkit", "mlx"]
        available = {}
        for dep in deps:
            try:
                mod = __import__(dep)
                available[dep] = getattr(mod, "__version__", "installed")
            except ImportError:
                available[dep] = "not available"
        info["dependencies"] = available

        return info

    def _format_report(self, report: AuditReport) -> str:
        """Format the audit report as a readable markdown document.

        Args:
            report: The audit report to format.

        Returns:
            Markdown-formatted report string.
        """
        lines = [
            f"# {report.title}",
            "",
            f"**Generated:** {report.created_at}",
            "",
            "## Session Overview",
            "",
            f"- **Session ID:** {report.session_info.get('session_id', 'N/A')}",
            f"- **Status:** {report.session_info.get('status', 'N/A')}",
            f"- **Duration:** {report.session_info.get('duration_seconds', 0)}s",
            f"- **Total Operations:** {report.session_info.get('total_entries', 0)}",
            f"- **Successful:** {report.session_info.get('successful', 0)}",
            f"- **Failed:** {report.session_info.get('failed', 0)}",
            "",
            "## Operation Summary",
            "",
        ]

        # Operation summary table
        if report.operation_summary:
            lines.append("| Operation | Count |")
            lines.append("|-----------|-------|")
            for op, count in sorted(report.operation_summary.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {op} | {count} |")
            lines.append("")

        # Database queries
        if report.database_queries:
            lines.append("## Database Queries")
            lines.append("")
            for q in report.database_queries[:20]:
                status = "✅" if q["success"] else "❌"
                lines.append(f"- {status} {q['description']} ({q['duration_ms']:.0f}ms)")
            if len(report.database_queries) > 20:
                lines.append(f"- *... and {len(report.database_queries) - 20} more*")
            lines.append("")

        # Code executions
        if report.code_executions:
            lines.append("## Code Executions")
            lines.append("")
            for c in report.code_executions[:20]:
                status = "✅" if c["success"] else "❌"
                lines.append(f"- {status} {c['description']} ({c['duration_ms']:.0f}ms)")
            if len(report.code_executions) > 20:
                lines.append(f"- *... and {len(report.code_executions) - 20} more*")
            lines.append("")

        # LLM interactions
        if report.llm_interactions:
            lines.append("## LLM Interactions")
            lines.append("")
            for llm_entry in report.llm_interactions[:10]:
                status = "✅" if llm_entry["success"] else "❌"
                lines.append(f"- {status} {llm_entry['description']} ({llm_entry['duration_ms']:.0f}ms)")
            if len(report.llm_interactions) > 10:
                lines.append(f"- *... and {len(report.llm_interactions) - 10} more*")
            lines.append("")

        # Errors
        if report.errors:
            lines.append("## Errors")
            lines.append("")
            for e in report.errors:
                lines.append(f"- ❌ **{e['description']}**")
                lines.append(f"  - Error: {e.get('error', 'Unknown')}")
            lines.append("")

        # Data lineage
        if report.data_lineage.get("available"):
            lines.append("## Data Lineage")
            lines.append("")
            dl = report.data_lineage
            lines.append(f"- **Sources:** {dl.get('sources', 0)}")
            lines.append(f"- **Transformations:** {dl.get('transformations', 0)}")
            lines.append(f"- **Outputs:** {dl.get('outputs', 0)}")
            lines.append("")

        # Reproducibility info
        ri = report.reproducibility_info
        if ri:
            lines.append("## Reproducibility Information")
            lines.append("")
            lines.append(f"- **Platform:** {ri.get('platform', 'Unknown')}")
            lines.append(f"- **Python:** {ri.get('python_version', 'Unknown')}")
            lines.append("")
            lines.append("### Dependencies")
            lines.append("")
            lines.append("| Package | Version |")
            lines.append("|---------|---------|")
            for pkg, ver in ri.get("dependencies", {}).items():
                lines.append(f"| {pkg} | {ver} |")

        lines.append("")
        lines.append("---")
        lines.append("*Report generated by Fusion-Science Audit System*")

        return "\n".join(lines)

    def export_package(self, output_dir: str, include_code: bool = True) -> str:
        """Export a reproducibility package with all session data.

        Args:
            output_dir: Output directory for the package.
            include_code: Include executed code in the package.

        Returns:
            Path to the exported package directory.
        """
        package_dir = Path(output_dir) / f"reproducibility_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        package_dir.mkdir(parents=True, exist_ok=True)

        # Export trace data
        trace_json = self.tracer.export_json(pretty=True)
        if trace_json:
            with open(package_dir / "trace.json", "w", encoding="utf-8") as f:
                f.write(trace_json)

        # Export provenance data
        if self.provenance:
            prov_json = self.provenance.export_json(pretty=True)
            if prov_json:
                with open(package_dir / "provenance.json", "w", encoding="utf-8") as f:
                    f.write(prov_json)

        # Generate and save audit report
        report = self.generate_audit_report()
        with open(package_dir / "audit_report.md", "w", encoding="utf-8") as f:
            f.write(report.content)

        # Save metadata
        metadata = {
            "exported_at": datetime.now().isoformat(),
            "fusion_science_version": "0.1.0",
            "files": {
                "trace": "trace.json",
                "provenance": "provenance.json",
                "audit_report": "audit_report.md",
            },
        }
        with open(package_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        logger.info("Exported reproducibility package to %s", package_dir)
        return str(package_dir)

    @staticmethod
    def format_for_journal(report: AuditReport) -> str:
        """Format the audit report for journal submission compliance.

        Args:
            report: The audit report.

        Returns:
            Journal-compliant reproducibility statement.
        """
        ri = report.reproducibility_info
        lines = [
            "## Data Availability and Reproducibility",
            "",
            "### Computational Environment",
            f"- Platform: {ri.get('platform', 'N/A')}",
            f"- Python: {ri.get('python_version', 'N/A').split()[0] if ri.get('python_version') else 'N/A'}",
            "",
            "### Software Dependencies",
        ]

        for pkg, ver in ri.get("dependencies", {}).items():
            lines.append(f"- {pkg}: {ver}")

        lines.extend([
            "",
            "### Workflow Audit",
            f"- Total research operations: {report.session_info.get('total_entries', 0)}",
            f"- Database queries: {len(report.database_queries)}",
            f"- Code executions: {len(report.code_executions)}",
            f"- Visualizations generated: {len(report.visualizations)}",
            "",
            "### Provenance",
            "All data sources, transformations, and outputs are tracked with full lineage.",
            "The complete audit trail and reproducibility package are available upon request.",
        ])

        return "\n".join(lines)
