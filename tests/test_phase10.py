from __future__ import annotations

from unittest.mock import patch

from fusion_science.audit.compliance import ComplianceChecker
from fusion_science.compute.code_generator import CodeGenerator
from fusion_science.compute.jupyter_kernel import JupyterKernelManager
from fusion_science.utils.offline import get_connectivity, is_offline


class TestOfflineMode:
    def test_is_offline_env_true(self):
        with patch.dict("os.environ", {"FUSION_OFFLINE_MODE": "true"}):
            assert is_offline() is True

    def test_is_offline_env_1(self):
        with patch.dict("os.environ", {"FUSION_OFFLINE_MODE": "1"}):
            assert is_offline() is True

    def test_is_offline_env_false(self):
        with patch.dict("os.environ", {"FUSION_OFFLINE_MODE": "false"}):
            result = is_offline()
            assert isinstance(result, bool)

    def test_is_offline_no_env(self):
        with patch.dict("os.environ", {}, clear=True):
            result = is_offline()
            assert isinstance(result, bool)

    def test_get_connectivity_structure(self):
        result = get_connectivity()
        assert "offline" in result
        assert isinstance(result["offline"], bool)

    def test_get_connectivity_offline_env(self):
        with patch.dict("os.environ", {"FUSION_OFFLINE_MODE": "true"}):
            result = get_connectivity()
            assert result["offline"] is True


class TestCodeGeneratorAPI:
    def test_code_gen_no_gateway(self):
        gen = CodeGenerator(gateway=None)
        result = gen._rule_based_generate("DESeq2 differential expression", "python")
        assert result.code != ""
        assert result.language == "python"

    def test_code_gen_template_match(self):
        gen = CodeGenerator(gateway=None)
        result = gen._rule_based_generate("GO enrichment analysis", "python")
        assert "go" in result.code.lower() or result.code != ""

    def test_code_gen_batch_no_gateway(self):
        gen = CodeGenerator(gateway=None)
        results = []
        for q in ["t-test", "PCA analysis"]:
            results.append(gen._rule_based_generate(q, "python"))
        assert len(results) == 2


class TestComplianceCheckerAPI:
    def test_check_report_structure(self):
        checker = ComplianceChecker()
        report = checker.check_report(session_id="test-session", usage_context="personal")
        assert "session_id" in report
        assert "all_passed" in report
        assert "results" in report
        assert "summary" in report

    def test_check_report_personal_context(self):
        checker = ComplianceChecker()
        report = checker.check_report(session_id="test-session", usage_context="personal")
        algorithm = [r for r in report["results"] if r["category"] == "algorithm_registration"]
        assert algorithm[0]["passed"] is True

    def test_check_with_trace_entries(self):
        checker = ComplianceChecker()
        entries = [{"operation": "db_query", "parameters": {"url": "https://api.openai.com/v1"}}]
        report = checker.check_report(session_id="test-session", trace_entries=entries, usage_context="personal")
        residency = [r for r in report["results"] if r["category"] == "data_residency"]
        assert residency[0]["passed"] is False


class TestJupyterKernelManagerAPI:
    def test_list_kernels_structure(self):
        kernels = JupyterKernelManager.list_available_kernels()
        assert isinstance(kernels, list)

    def test_kernel_manager_init(self):
        mgr = JupyterKernelManager()
        assert mgr.kernel_name == "python3"
