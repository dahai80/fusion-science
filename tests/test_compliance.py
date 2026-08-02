# tests/test_compliance.py — tests for ComplianceChecker (F-29)
# Tests: check_data_residency, check_algorithm_registration, check_ethics_review, check_sensitive_data, check_report
# Importers: pytest runner; API: ComplianceChecker.check*, ComplianceResult.to_dict(); Data: ComplianceResult dataclass
# User instruction: "启动下一个阶段的任务实施" — Phase 7 test file for ComplianceChecker

from __future__ import annotations

import pytest

from fusion_science.audit.compliance import ComplianceChecker, ComplianceResult


@pytest.fixture
def checker():
    return ComplianceChecker()


class TestComplianceResult:
    def test_to_dict(self):
        r = ComplianceResult(
            category="data_residency",
            passed=True,
            severity="info",
            details="All local",
            recommendation="OK",
        )
        d = r.to_dict()
        assert d["category"] == "data_residency"
        assert d["passed"] is True
        assert d["severity"] == "info"


class TestDataResidency:
    def test_no_entries(self, checker):
        result = checker.check_data_residency(None)
        assert result.passed is True
        assert result.category == "data_residency"

    def test_empty_entries(self, checker):
        result = checker.check_data_residency([])
        assert result.passed is True

    def test_local_only(self, checker):
        entries = [{"id": "1", "description": "local compute", "parameters": {}}]
        result = checker.check_data_residency(entries)
        assert result.passed is True

    def test_remote_call_detected(self, checker):
        entries = [{"id": "1", "description": "called api.openai.com", "parameters": {}}]
        result = checker.check_data_residency(entries)
        assert result.passed is False
        assert result.severity == "critical"


class TestAlgorithmRegistration:
    def test_personal_use(self, checker):
        result = checker.check_algorithm_registration("personal")
        assert result.passed is True

    def test_lab_internal(self, checker):
        result = checker.check_algorithm_registration("lab_internal")
        assert result.passed is True

    def test_public_service(self, checker):
        result = checker.check_algorithm_registration("public_service")
        assert result.passed is False
        assert result.severity == "warning"


class TestEthicsReview:
    def test_no_entries(self, checker):
        result = checker.check_ethics_review(None)
        assert result.passed is True

    def test_no_sensitive(self, checker):
        entries = [{"id": "1", "description": "simple analysis", "parameters": {}}]
        result = checker.check_ethics_review(entries)
        assert result.passed is True

    def test_genomic_data(self, checker):
        entries = [{"id": "1", "description": "genome-wide association", "parameters": {}}]
        result = checker.check_ethics_review(entries)
        assert result.passed is False
        assert result.severity == "warning"

    def test_clinical_data(self, checker):
        entries = [{"id": "1", "description": "patient clinical records", "parameters": {}}]
        result = checker.check_ethics_review(entries)
        assert result.passed is False


class TestSensitiveData:
    def test_no_sensitive(self, checker):
        entries = [{"id": "1", "description": "basic stats", "parameters": {}}]
        result = checker.check_sensitive_data(entries)
        assert result.passed is True
        assert result.severity == "info"

    def test_dna_data(self, checker):
        entries = [{"id": "1", "description": "DNA sequencing data", "parameters": {}}]
        result = checker.check_sensitive_data(entries)
        assert result.passed is True  # warning, not failure
        assert result.severity == "warning"


class TestCheckAll:
    def test_check_returns_4_results(self, checker):
        results = checker.check(trace_entries=[], usage_context="personal")
        assert len(results) == 4
        categories = {r.category for r in results}
        assert "data_residency" in categories
        assert "algorithm_registration" in categories
        assert "ethics_review" in categories
        assert "sensitive_data" in categories

    def test_check_report(self, checker):
        report = checker.check_report(session_id="test-123", trace_entries=[], usage_context="personal")
        assert report["session_id"] == "test-123"
        assert report["all_passed"] is True
        assert report["summary"]["total_checks"] == 4
        assert report["summary"]["passed"] == 4
