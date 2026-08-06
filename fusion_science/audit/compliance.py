# audit/compliance.py — ComplianceChecker for Chinese research regulations (F-29)
# Importers: api/routes/audit_route.py, audit/__init__.py
# API: ComplianceChecker.check(), check_report(), ComplianceResult.to_dict()
# User instruction: "启动下一个阶段的任务实施"

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ComplianceResult:
    category: str
    passed: bool
    severity: str  # info | warning | critical
    details: str = ""
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "passed": self.passed,
            "severity": self.severity,
            "details": self.details,
            "recommendation": self.recommendation,
        }


_SENSITIVE_KEYWORDS = [
    "genome",
    "genomic",
    "dna sequencing",
    "gene expression",
    "clinical trial",
    "patient record",
    "health record",
    "pharmacogenomics",
    "biobank",
    "genetic data",
    "ehr ",
    "electronic health",
    "personal health",
]

_REMOTE_PATTERNS = [
    r"https?://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)",
    r"api\.openai\.com",
    r"api\.anthropic\.com",
    r"huggingface\.co",
    r"pubmed\.ncbi",
]


class ComplianceChecker:
    def check_data_residency(self, trace_entries: list[dict] | None) -> ComplianceResult:
        if not trace_entries:
            return ComplianceResult(
                category="data_residency",
                passed=True,
                severity="info",
                details="No trace entries to check",
                recommendation="OK",
            )
        for entry in trace_entries:
            desc = entry.get("description", "")
            params = str(entry.get("parameters", {}))
            for pattern in _REMOTE_PATTERNS:
                if re.search(pattern, desc + params, re.IGNORECASE):
                    logger.warning("Data residency violation: remote call detected in entry %s", entry.get("id", "?"))
                    return ComplianceResult(
                        category="data_residency",
                        passed=False,
                        severity="critical",
                        details=f"Remote API call detected: pattern '{pattern}' matched",
                        recommendation="Ensure all data processing stays on local infrastructure",
                    )
        return ComplianceResult(
            category="data_residency",
            passed=True,
            severity="info",
            details="All data processing is local",
            recommendation="OK",
        )

    def check_algorithm_registration(self, usage_context: str = "personal") -> ComplianceResult:
        exempt = {"personal", "lab_internal", "research", "education"}
        if usage_context.lower() in exempt:
            return ComplianceResult(
                category="algorithm_registration",
                passed=True,
                severity="info",
                details=f"Usage context '{usage_context}' is exempt from registration",
                recommendation="OK",
            )
        return ComplianceResult(
            category="algorithm_registration",
            passed=False,
            severity="warning",
            details=f"Usage context '{usage_context}' requires algorithm registration per regulations",
            recommendation="Register the algorithm with the appropriate regulatory body before deployment",
        )

    def check_ethics_review(self, trace_entries: list[dict] | None) -> ComplianceResult:
        if not trace_entries:
            return ComplianceResult(
                category="ethics_review",
                passed=True,
                severity="info",
                details="No trace entries to check",
                recommendation="OK",
            )
        for entry in trace_entries:
            desc = entry.get("description", "").lower()
            params = str(entry.get("parameters", {})).lower()
            combined = desc + " " + params
            for kw in ["human subject", "animal model", "clinical trial", "patient", "genome", "genomic"]:
                if kw in combined:
                    logger.warning("Ethics review required: keyword '%s' in entry %s", kw, entry.get("id", "?"))
                    return ComplianceResult(
                        category="ethics_review",
                        passed=False,
                        severity="warning",
                        details=f"Operations involving '{kw}' require ethics review approval",
                        recommendation="Obtain ethics review approval before proceeding with this research",
                    )
        return ComplianceResult(
            category="ethics_review",
            passed=True,
            severity="info",
            details="No ethics-sensitive operations detected",
            recommendation="OK",
        )

    def check_sensitive_data(self, trace_entries: list[dict] | None) -> ComplianceResult:
        if not trace_entries:
            return ComplianceResult(
                category="sensitive_data",
                passed=True,
                severity="info",
                details="No trace entries to check",
                recommendation="OK",
            )
        for entry in trace_entries:
            desc = entry.get("description", "").lower()
            params = str(entry.get("parameters", {})).lower()
            combined = desc + " " + params
            for kw in _SENSITIVE_KEYWORDS:
                if kw in combined:
                    logger.warning("Sensitive data detected: keyword '%s' in entry %s", kw, entry.get("id", "?"))
                    return ComplianceResult(
                        category="sensitive_data",
                        passed=True,
                        severity="warning",
                        details=f"Sensitive data type '{kw}' detected — handle per data protection regulations",
                        recommendation="Apply additional data protection measures: encryption, access control, audit logging",
                    )
        return ComplianceResult(
            category="sensitive_data",
            passed=True,
            severity="info",
            details="No sensitive data types detected",
            recommendation="OK",
        )

    def check(
        self,
        trace_entries: list[dict] | None = None,
        usage_context: str = "personal",
    ) -> list[ComplianceResult]:
        results = [
            self.check_data_residency(trace_entries),
            self.check_algorithm_registration(usage_context),
            self.check_ethics_review(trace_entries),
            self.check_sensitive_data(trace_entries),
        ]
        logger.info("Compliance check: %d/%d passed", sum(r.passed for r in results), len(results))
        return results

    def check_report(
        self,
        session_id: str,
        trace_entries: list[dict] | None = None,
        usage_context: str = "personal",
    ) -> dict:
        results = self.check(trace_entries, usage_context)
        passed_count = sum(r.passed for r in results)
        return {
            "session_id": session_id,
            "all_passed": passed_count == len(results),
            "results": [r.to_dict() for r in results],
            "summary": {
                "total_checks": len(results),
                "passed": passed_count,
                "failed": len(results) - passed_count,
            },
        }
