from __future__ import annotations

import json
import logging
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .provenance import ProvenanceTracker
from .tracker import TraceRecorder

logger = logging.getLogger(__name__)


@dataclass
class ReproducibilityPack:
    pack_id: str
    created_at: str
    fusion_science_version: str
    platform_info: dict[str, str]
    python_version: str
    dependencies: dict[str, str]
    trace_data: dict[str, Any] = field(default_factory=dict)
    provenance_data: dict[str, Any] = field(default_factory=dict)
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    checksum: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "created_at": self.created_at,
            "fusion_science_version": self.fusion_science_version,
            "platform_info": self.platform_info,
            "python_version": self.python_version,
            "dependencies": self.dependencies,
            "trace_data": self.trace_data,
            "provenance_data": self.provenance_data,
            "config_snapshot": self.config_snapshot,
            "checksum": self.checksum,
        }


@dataclass
class ComplianceCheck:
    check_id: str
    category: str
    name: str
    description: str
    passed: bool
    severity: str = "info"
    details: dict[str, Any] = field(default_factory=dict)


class ReproducibilityPackBuilder:
    def __init__(
        self,
        trace_recorder: TraceRecorder | None = None,
        provenance_tracker: ProvenanceTracker | None = None,
    ):
        self.tracer = trace_recorder
        self.provenance = provenance_tracker

    def build(self, config_snapshot: dict[str, Any] | None = None) -> ReproducibilityPack:
        logger.info("Building reproducibility pack")

        from fusion_science import __version__

        pack = ReproducibilityPack(
            pack_id=f"repro_{int(time.time())}",
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            fusion_science_version=__version__,
            platform_info=self._collect_platform_info(),
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            dependencies=self._collect_dependencies(),
            config_snapshot=config_snapshot or {},
        )

        if self.tracer:
            summary = self.tracer.get_session_summary()
            pack.trace_data = summary

        if self.provenance and self.provenance.get_graph():
            graph = self.provenance.get_graph()
            pack.provenance_data = {
                "name": graph.name,
                "node_count": len(graph.nodes),
                "sources": sum(1 for n in graph.nodes.values() if n.type == "source"),
                "transformations": sum(1 for n in graph.nodes.values() if n.type == "transformation"),
                "outputs": sum(1 for n in graph.nodes.values() if n.type == "output"),
            }

        import hashlib

        raw = json.dumps(pack.to_dict(), sort_keys=True, default=str)
        pack.checksum = hashlib.sha256(raw.encode()).hexdigest()[:16]

        logger.info("Reproducibility pack built: %s", pack.pack_id)
        return pack

    def export_to_dir(self, pack: ReproducibilityPack, output_dir: str) -> str:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        pack_data = pack.to_dict()
        with open(out / "reproducibility_pack.json", "w", encoding="utf-8") as f:
            json.dump(pack_data, f, indent=2, ensure_ascii=False, default=str)

        if self.tracer:
            trace_json = self.tracer.export_json(pretty=True)
            if trace_json:
                with open(out / "trace.json", "w", encoding="utf-8") as f:
                    f.write(trace_json)

        if self.provenance:
            prov_json = self.provenance.export_json(pretty=True)
            if prov_json:
                with open(out / "provenance.json", "w", encoding="utf-8") as f:
                    f.write(prov_json)

        logger.info("Exported reproducibility pack to %s", out)
        return str(out)

    @staticmethod
    def _collect_platform_info() -> dict[str, str]:
        return {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        }

    @staticmethod
    def _collect_dependencies() -> dict[str, str]:
        deps_to_check = [
            "numpy",
            "pandas",
            "scipy",
            "matplotlib",
            "seaborn",
            "sklearn",
            "bio",
            "rdkit",
            "httpx",
            "pydantic",
            "fastapi",
            "sqlite3",
        ]
        result = {}
        for dep in deps_to_check:
            try:
                mod = __import__(dep)
                result[dep] = getattr(mod, "__version__", "installed")
            except ImportError:
                pass
        return result


_COMPLIANCE_RULES: list[dict[str, Any]] = [
    {
        "id": "data_provenance",
        "category": "provenance",
        "name": "Data Provenance Tracking",
        "description": "All data sources must be tracked with provenance",
        "check": lambda pack: bool(pack.provenance_data.get("sources", 0) > 0),
        "severity": "error",
    },
    {
        "id": "execution_trace",
        "category": "audit",
        "name": "Execution Trace Available",
        "description": "Complete execution trace must be available",
        "check": lambda pack: bool(pack.trace_data and pack.trace_data.get("total_entries", 0) > 0),
        "severity": "warning",
    },
    {
        "id": "platform_recorded",
        "category": "environment",
        "name": "Platform Information Recorded",
        "description": "Platform and environment info must be recorded",
        "check": lambda pack: bool(pack.platform_info and pack.python_version),
        "severity": "warning",
    },
    {
        "id": "dependencies_recorded",
        "category": "environment",
        "name": "Dependencies Recorded",
        "description": "Software dependencies must be recorded for reproducibility",
        "check": lambda pack: bool(pack.dependencies),
        "severity": "warning",
    },
    {
        "id": "version_pinned",
        "category": "environment",
        "name": "Version Information Available",
        "description": "Fusion-science version must be recorded",
        "check": lambda pack: bool(pack.fusion_science_version),
        "severity": "info",
    },
    {
        "id": "checksum_integrity",
        "category": "integrity",
        "name": "Pack Integrity Checksum",
        "description": "Reproducibility pack must have integrity checksum",
        "check": lambda pack: bool(pack.checksum),
        "severity": "info",
    },
]


class ComplianceChecker:
    def __init__(self, custom_rules: list[dict[str, Any]] | None = None):
        self.rules = list(_COMPLIANCE_RULES)
        if custom_rules:
            self.rules.extend(custom_rules)

    def check(self, pack: ReproducibilityPack) -> list[ComplianceCheck]:
        logger.info("Running compliance checks on pack %s", pack.pack_id)
        results: list[ComplianceCheck] = []

        for rule in self.rules:
            try:
                passed = rule["check"](pack)
            except Exception as e:
                logger.error("Compliance rule %s failed: %s", rule["id"], e)
                passed = False

            results.append(
                ComplianceCheck(
                    check_id=rule["id"],
                    category=rule["category"],
                    name=rule["name"],
                    description=rule["description"],
                    passed=passed,
                    severity=rule.get("severity", "info"),
                )
            )

        logger.info(
            "Compliance check complete: %d/%d passed",
            sum(1 for r in results if r.passed),
            len(results),
        )
        return results

    def check_report(self, pack: ReproducibilityPack) -> dict[str, Any]:
        checks = self.check(pack)
        by_severity: dict[str, list[dict]] = {}
        for c in checks:
            by_severity.setdefault(c.severity, []).append(
                {
                    "id": c.check_id,
                    "name": c.name,
                    "passed": c.passed,
                }
            )

        return {
            "pack_id": pack.pack_id,
            "total_checks": len(checks),
            "passed": sum(1 for c in checks if c.passed),
            "failed": sum(1 for c in checks if not c.passed),
            "by_severity": by_severity,
            "compliant": all(c.passed or c.severity == "info" for c in checks),
        }
