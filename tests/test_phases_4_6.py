"""Tests for Phase 4 (Compute+Viz), Phase 5 (Audit+Chinese DB), Phase 6 (Integration)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from fusion_science.audit.provenance import ProvenanceTracker
from fusion_science.audit.reproducibility import (
    ComplianceCheck,
    ComplianceChecker,
    ReproducibilityPack,
    ReproducibilityPackBuilder,
)
from fusion_science.audit.tracker import TraceRecorder
from fusion_science.compute.code_generator import CodeGenerator, CodeSuggestion
from fusion_science.compute.sandbox import SandboxConfig, SandboxManager
from fusion_science.database.chinese import (
    CNKIConnector,
    NGDCConnector,
    ScienceDBConnector,
)
from fusion_science.database.mirror import MirrorRouter
from fusion_science.visualization.smart_viz import SmartVisualizer, VizRecommendation

# =========================================================================
# Phase 4: Compute
# =========================================================================

class TestCodeGenerator:
    def test_rule_based_correlation(self):
        gen = CodeGenerator()
        result = gen._rule_based_generate("correlation analysis", "python")
        assert result.language == "python"
        assert result.confidence > 0.5
        assert "scipy" in result.packages_needed or "pandas" in result.packages_needed

    def test_rule_based_deseq2(self):
        gen = CodeGenerator()
        result = gen._rule_based_generate("deseq2 differential expression", "R")
        assert result.language.lower() == "r"
        assert "DESeq2" in result.packages_needed

    def test_rule_based_pca(self):
        gen = CodeGenerator()
        result = gen._rule_based_generate("PCA dimensionality reduction", "python")
        assert result.language == "python"
        assert "sklearn" in result.packages_needed

    def test_rule_based_ttest(self):
        gen = CodeGenerator()
        result = gen._rule_based_generate("t-test two sample comparison", "python")
        assert result.language == "python"
        assert result.confidence > 0.5

    def test_rule_based_clustering(self):
        gen = CodeGenerator()
        result = gen._rule_based_generate("kmeans clustering", "python")
        assert result.language == "python"
        assert "sklearn" in result.packages_needed

    def test_fallback_suggestion(self):
        gen = CodeGenerator()
        result = gen._rule_based_generate("custom unknown analysis", "python")
        assert result.language == "python"
        assert result.confidence < 0.5

    @pytest.mark.asyncio
    async def test_generate_no_gateway(self):
        gen = CodeGenerator()
        result = await gen.generate("correlation analysis", language="python")
        assert isinstance(result, CodeSuggestion)
        assert result.language == "python"

    @pytest.mark.asyncio
    async def test_generate_batch(self):
        gen = CodeGenerator()
        results = await gen.generate_batch(["correlation", "pca"], language="python")
        assert len(results) == 2
        assert all(isinstance(r, CodeSuggestion) for r in results)


class TestSandboxManager:
    def test_create_sandbox(self):
        mgr = SandboxManager()
        info = mgr.create_sandbox()
        assert "sandbox_id" in info
        assert "work_dir" in info
        assert os.path.isdir(info["work_dir"])
        mgr.cleanup_all()

    def test_cleanup_sandbox(self):
        mgr = SandboxManager()
        info = mgr.create_sandbox()
        sid = info["sandbox_id"]
        assert mgr.cleanup_sandbox(sid) is True
        assert mgr.cleanup_sandbox("nonexistent") is False

    def test_validate_code_safe(self):
        mgr = SandboxManager()
        result = mgr.validate_code("import numpy as np\nx = np.array([1,2,3])\n")
        assert result["valid"] is True
        assert result["risk_level"] == "low"

    def test_validate_code_eval_blocked(self):
        mgr = SandboxManager()
        result = mgr.validate_code("eval('print(1)')")
        assert result["valid"] is False
        assert result["risk_level"] == "high"

    def test_validate_code_subprocess_blocked(self):
        mgr = SandboxManager()
        result = mgr.validate_code("import subprocess\nsubprocess.run(['ls'])")
        assert result["valid"] is False

    def test_validate_code_os_system_blocked(self):
        mgr = SandboxManager()
        result = mgr.validate_code("import os\nos.system('rm -rf /')")
        assert result["valid"] is False

    def test_validate_code_syntax_error(self):
        mgr = SandboxManager()
        result = mgr.validate_code("def foo(")
        assert result["valid"] is False
        assert result["risk_level"] == "high"

    def test_get_resource_usage(self):
        mgr = SandboxManager()
        info = mgr.create_sandbox()
        usage = mgr.get_resource_usage(info["sandbox_id"])
        assert "work_dir_size_mb" in usage
        mgr.cleanup_all()

    def test_custom_config(self):
        cfg = SandboxConfig(timeout=60, max_memory_mb=512)
        mgr = SandboxManager(config=cfg)
        info = mgr.create_sandbox(config=cfg)
        assert info["env_vars"]["FUSION_SANDBOX_TIMEOUT"] == "60"
        mgr.cleanup_all()

    def test_cleanup_all(self):
        mgr = SandboxManager()
        mgr.create_sandbox()
        mgr.create_sandbox()
        count = mgr.cleanup_all()
        assert count == 2


# =========================================================================
# Phase 4: Visualization
# =========================================================================

class TestSmartVisualizer:
    def test_rule_based_volcano(self):
        viz = SmartVisualizer()
        recs = viz._rule_based_recommend("differential expression volcano plot", "")
        assert len(recs) >= 1
        assert any(r.chart_type == "volcano_plot" for r in recs)

    def test_rule_based_heatmap(self):
        viz = SmartVisualizer()
        recs = viz._rule_based_recommend("gene expression heatmap matrix", "")
        assert len(recs) >= 1
        assert any(r.chart_type == "heatmap" for r in recs)

    def test_rule_based_scatter(self):
        viz = SmartVisualizer()
        recs = viz._rule_based_recommend("correlation scatter", "")
        assert len(recs) >= 1

    def test_rule_based_default(self):
        viz = SmartVisualizer()
        recs = viz._rule_based_recommend("something completely unrelated", "")
        assert len(recs) >= 1

    @pytest.mark.asyncio
    async def test_recommend_no_gateway(self):
        viz = SmartVisualizer()
        recs = await viz.recommend("time series trend data")
        assert isinstance(recs, list)
        assert all(isinstance(r, VizRecommendation) for r in recs)

    def test_viz_recommendation_fields(self):
        rec = VizRecommendation(
            chart_type="scatter",
            title="Scatter Plot",
            description="Shows relationship",
            data_requirements="Two numeric columns",
            confidence=0.9,
        )
        assert rec.chart_type == "scatter"
        assert rec.confidence == 0.9


# =========================================================================
# Phase 5: Audit - Reproducibility & Compliance
# =========================================================================

class TestReproducibilityPack:
    def test_pack_to_dict(self):
        pack = ReproducibilityPack(
            pack_id="repro_001",
            created_at="2026-01-01T00:00:00",
            fusion_science_version="0.3.0",
            platform_info={"system": "Darwin"},
            python_version="3.12.0",
            dependencies={"numpy": "1.26.0"},
            checksum="abc123",
        )
        d = pack.to_dict()
        assert d["pack_id"] == "repro_001"
        assert d["checksum"] == "abc123"

    def test_pack_builder_minimal(self):
        builder = ReproducibilityPackBuilder()
        pack = builder.build()
        assert pack.pack_id.startswith("repro_")
        assert pack.python_version
        assert pack.platform_info
        assert pack.fusion_science_version
        assert pack.checksum

    def test_pack_builder_with_tracer(self):
        tracer = TraceRecorder()
        tracer.start_session()
        tracer.record("db_query", "test", "test query")
        builder = ReproducibilityPackBuilder(trace_recorder=tracer)
        pack = builder.build()
        assert pack.trace_data.get("total_entries") == 1
        tracer.end_session()

    def test_pack_builder_with_provenance(self):
        prov = ProvenanceTracker()
        prov.start_tracking("test")
        prov.add_source("test_source", "db_query")
        builder = ReproducibilityPackBuilder(provenance_tracker=prov)
        pack = builder.build()
        assert pack.provenance_data.get("sources") == 1

    def test_pack_export(self):
        builder = ReproducibilityPackBuilder()
        pack = builder.build()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = builder.export_to_dir(pack, tmpdir)
            assert os.path.isfile(os.path.join(out, "reproducibility_pack.json"))
            with open(os.path.join(out, "reproducibility_pack.json")) as f:
                data = json.load(f)
            assert data["pack_id"] == pack.pack_id


class TestComplianceChecker:
    def test_check_all_pass(self):
        pack = ReproducibilityPack(
            pack_id="repro_001",
            created_at="2026-01-01",
            fusion_science_version="0.3.0",
            platform_info={"system": "Darwin"},
            python_version="3.12",
            dependencies={"numpy": "1.26"},
            trace_data={"total_entries": 5},
            provenance_data={"sources": 2},
            checksum="abc123",
        )
        checker = ComplianceChecker()
        results = checker.check(pack)
        assert len(results) >= 4
        assert all(isinstance(r, ComplianceCheck) for r in results)

    def test_check_report(self):
        pack = ReproducibilityPack(
            pack_id="repro_002",
            created_at="2026-01-01",
            fusion_science_version="0.3.0",
            platform_info={"system": "Darwin"},
            python_version="3.12",
            dependencies={"numpy": "1.26"},
            checksum="abc",
        )
        checker = ComplianceChecker()
        report = checker.check_report(pack)
        assert "total_checks" in report
        assert "passed" in report
        assert "failed" in report
        assert isinstance(report["compliant"], bool)

    def test_custom_rules(self):
        custom = [{
            "id": "custom_check",
            "category": "custom",
            "name": "Custom Rule",
            "description": "Test custom rule",
            "check": lambda pack: True,
            "severity": "info",
        }]
        checker = ComplianceChecker(custom_rules=custom)
        pack = ReproducibilityPack(
            pack_id="repro_003",
            created_at="2026-01-01",
            fusion_science_version="0.3.0",
            platform_info={},
            python_version="",
            dependencies={},
        )
        results = checker.check(pack)
        ids = [r.check_id for r in results]
        assert "custom_check" in ids


# =========================================================================
# Phase 5: Chinese Databases
# =========================================================================

class TestChineseDBResult:
    def test_success_result(self):
        from fusion_science.database.base import DatabaseResult
        r = DatabaseResult(source="ngdc", query="test", items=[{"id": 1}], total_count=1)
        assert r.source == "ngdc"
        assert r.total_count == 1

    def test_error_result(self):
        from fusion_science.database.base import DatabaseResult
        r = DatabaseResult(source="cnki", query="test", error="timeout")
        assert r.error == "timeout"
        assert r.total_count == 0


class TestNGDCConnector:
    def test_init(self):
        conn = NGDCConnector()
        assert conn.config.base_url == "https://ngdc.cncb.ac.cn"

    def test_parse_search_results_dict(self):
        conn = NGDCConnector()
        data = {"total": 1, "results": [{"accession": "A1", "title": "test"}]}
        result = conn._parse_search_results(data, sub_db="gsa")
        assert len(result) == 1
        assert result[0]["accession"] == "A1"

    def test_parse_search_results_list(self):
        conn = NGDCConnector()
        data = {"total": 2, "results": [{"accession": "A1"}, {"accession": "A2"}]}
        result = conn._parse_search_results(data, sub_db="gsa")
        assert len(result) == 2

    def test_parse_search_results_empty(self):
        conn = NGDCConnector()
        result = conn._parse_search_results({}, sub_db="gsa")
        assert result == []


class TestCNKIConnector:
    def test_init(self):
        conn = CNKIConnector()
        assert "cnki.net" in conn.config.base_url

    def test_parse_search_results(self):
        conn = CNKIConnector()
        data = {"total": 1, "results": [{"docId": "D1", "title": "paper1"}]}
        result = conn._parse_search_results(data)
        assert len(result) == 1
        assert result[0]["doc_id"] == "D1"


class TestScienceDBConnector:
    def test_init(self):
        conn = ScienceDBConnector()
        assert "scidb.cn" in conn.config.base_url

    def test_parse_search_results(self):
        conn = ScienceDBConnector()
        data = {"total": 1, "results": [{"id": "DS1", "title": "dataset1"}]}
        result = conn._parse_search_results(data)
        assert len(result) == 1
        assert result[0]["dataset_id"] == "DS1"


class TestMirrorRouter:
    def test_init(self):
        router = MirrorRouter()
        assert isinstance(router.is_offline_mode(), bool)

    def test_get_url(self):
        router = MirrorRouter()
        url = router.get_url("pubmed")
        assert "ncbi" in url

    def test_get_url_unknown(self):
        router = MirrorRouter()
        url = router.get_url("nonexistent_db")
        assert url == ""

    def test_enable_mirrors(self):
        router = MirrorRouter()
        router.enable_mirrors(True)
        assert router._use_mirrors is True

    def test_list_mirrors(self):
        router = MirrorRouter()
        mirrors = router.list_mirrors()
        assert isinstance(mirrors, list)
        assert len(mirrors) > 0


# =========================================================================
# Phase 6: Integration - Cross-module tests
# =========================================================================

class TestCrossModuleIntegration:
    def test_tracer_to_reproducibility_pack(self):
        tracer = TraceRecorder()
        tracer.start_session(metadata={"task": "integration test"})
        tracer.record_db_query("test_module", "pubmed", "cancer", 10, duration_ms=100.0)
        tracer.record_code_execution("test_module", "python", "pca analysis", duration_ms=500.0)
        tracer.record_llm_call("test_module", "llama3", "summarize", "done", duration_ms=2000.0)

        prov = ProvenanceTracker()
        prov.start_tracking("integration test")
        src_id = prov.add_source("PubMed query", "db_query", parameters={"query": "cancer"})
        tx_id = prov.add_transformation("PCA analysis", [src_id], parameters={"n_components": 2})
        prov.add_output("PCA plot", [tx_id], output_type="figure")

        builder = ReproducibilityPackBuilder(
            trace_recorder=tracer,
            provenance_tracker=prov,
        )
        pack = builder.build(config_snapshot={"model": "llama3"})

        assert pack.trace_data.get("total_entries") == 3
        assert pack.provenance_data.get("sources") == 1
        assert pack.provenance_data.get("transformations") == 1
        assert pack.provenance_data.get("outputs") == 1
        assert pack.config_snapshot.get("model") == "llama3"
        assert pack.checksum

        checker = ComplianceChecker()
        report = checker.check_report(pack)
        assert report["total_checks"] >= 4

        tracer.end_session()

    def test_sandbox_validate_generated_code(self):
        gen = CodeGenerator()
        suggestion = gen._rule_based_generate("correlation analysis", "python")

        mgr = SandboxManager()
        result = mgr.validate_code(suggestion.code)
        assert result["valid"] is True

    def test_viz_recommendation_for_analysis(self):
        viz = SmartVisualizer()
        gen = CodeGenerator()

        code = gen._rule_based_generate("differential expression", "python")
        recs = viz._rule_based_recommend(code.description, "gene expression")

        assert len(recs) >= 1
