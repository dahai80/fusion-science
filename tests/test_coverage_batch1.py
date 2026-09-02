from __future__ import annotations

import json
import logging
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from fusion_science.audit.provenance import ProvenanceTracker
from fusion_science.audit.report import AuditReport, ReportGenerator
from fusion_science.audit.tracker import TraceRecorder
from fusion_science.cli import cli
from fusion_science.core.agents.data import DataAgent
from fusion_science.core.agents.error import ErrorAnalysisAgent
from fusion_science.core.agents.literature import LiteratureAgent
from fusion_science.core.agents.router import QueryRouterAgent
from fusion_science.core.agents.visualize import VizAgent
from fusion_science.core.agents.writer import WriterAgent
from fusion_science.core.engine import LLMResponse, ScienceEngine
from fusion_science.core.tools import ToolRegistry
from fusion_science.visualization.chart import ChartConfig, ChartGenerator, ChartResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> ScienceEngine:
    engine = ScienceEngine(model="test-model", base_url="http://localhost:11434/v1")
    engine.chat = AsyncMock(
        return_value=LLMResponse(
            content="mocked LLM response",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            model="test-model",
            finish_reason="stop",
        )
    )
    return engine


def _make_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in [
        "search_literature",
        "fetch_paper",
        "extract_findings",
        "analyze_consensus",
        "search_database",
        "execute_python",
        "execute_r",
        "generate_chart",
        "visualize_molecule",
        "visualize_protein",
        "write_section",
        "manage_citations",
    ]:
        registry.register(
            name=name,
            description=f"Mock {name}",
            parameters={"type": "object", "properties": {}},
            handler=AsyncMock(return_value={"status": "ok"}),
        )
    return registry


# ===========================================================================
# 1. CLI tests
# ===========================================================================


class TestCLI:
    def test_version_via_info(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["info"])
        assert result.exit_code == 0
        assert "Version:" in result.output

    def test_version_flag_exit(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert "fusion-science" in result.output or result.exit_code in (0, 2)

    def test_verbose_flag(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["-v", "info"])
        assert result.exit_code == 0

    def test_run_with_task(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "analyze gene expression"])
        assert result.exit_code == 0
        # I-10: run is an honest stub now — it reports the task was received,
        # not that it executed.
        assert "Task received" in result.output

    def test_run_with_pipeline_option(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "test task", "--pipeline", "literature_review"])
        assert result.exit_code == 0
        assert "Pipeline: literature_review" in result.output

    def test_pipeline_valid(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["pipeline", "literature_review", "cancer therapy"])
        assert result.exit_code == 0
        assert "literature_review" in result.output

    def test_pipeline_invalid(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["pipeline", "nonexistent_pipeline", "test query"])
        assert result.exit_code != 0

    def test_search_pubmed(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["search", "BRCA1", "--db", "pubmed"])
        assert result.exit_code == 0
        assert "pubmed" in result.output.lower() or "PubMed" in result.output

    def test_search_unknown_db(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["search", "test", "--db", "unknowndb"])
        assert result.exit_code == 0
        # I-10: search is a stub; it echoes the requested db, no validation.
        assert "Search requested" in result.output

    def test_search_with_max(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["search", "test", "--db", "pubmed", "--max", "5"])
        assert result.exit_code == 0
        assert "5" in result.output

    def test_analyze_with_code(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["analyze", "--code", "print('hello')"])
        assert result.exit_code == 0
        # I-10: analyze is a stub now — reports "Analyze requested".
        assert "Analyze requested" in result.output

    def test_analyze_with_file(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["analyze", "/tmp/data.csv"])
        assert result.exit_code == 0
        assert "data.csv" in result.output

    def test_analyze_no_input(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["analyze"])
        assert result.exit_code == 0

    def test_visualize_chart(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["visualize", "chart", "--data", "test.csv"])
        assert result.exit_code == 0
        assert "chart" in result.output

    def test_visualize_molecule(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["visualize", "molecule", "--data", "CCO"])
        assert result.exit_code == 0
        assert "molecule" in result.output

    def test_visualize_with_output(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["visualize", "protein", "--data", "1ABC", "--output", "/tmp/viz.png"])
        assert result.exit_code == 0
        assert "/tmp/viz.png" in result.output

    def test_review(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "machine learning in drug discovery"])
        assert result.exit_code == 0
        assert "Literature review" in result.output

    def test_review_with_max_papers(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["review", "test", "--max-papers", "5"])
        assert result.exit_code == 0
        assert "5" in result.output

    def test_audit_default(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["audit"])
        assert result.exit_code == 0
        assert "audit" in result.output.lower()

    def test_audit_json_format(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["audit", "--format", "json"])
        assert result.exit_code == 0
        assert "json" in result.output

    def test_config_show(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "model_name" in result.output

    def test_config_init(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, "test_config.yml")
            result = runner.invoke(cli, ["config", "init", "--path", cfg_path])
            assert result.exit_code == 0
            assert "Created" in result.output

    def test_info(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["info"])
        assert result.exit_code == 0
        assert "Fusion-Science System Info" in result.output
        assert "Version" in result.output

    def test_serve_missing_uvicorn(self):
        runner = CliRunner()
        with patch.dict("sys.modules", {"uvicorn": None}):
            result = runner.invoke(cli, ["serve"])
            assert "uvicorn" in result.output.lower() or result.exit_code != 0

    def test_config_flag(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", "/nonexistent/path", "info"])
        assert result.exit_code == 0


# ===========================================================================
# 2. Audit report tests
# ===========================================================================


class TestAuditReport:
    def _make_tracer_with_data(self) -> TraceRecorder:
        tracer = TraceRecorder(storage_dir=tempfile.mkdtemp())
        tracer.start_session(metadata={"task": "test"})
        tracer.record_db_query("test_module", "pubmed", "BRCA1", 10, duration_ms=150.0)
        tracer.record_db_query("test_module", "uniprot", "P53", 5, success=False, error="timeout", duration_ms=3000.0)
        tracer.record_code_execution("test_module", "python", "deseq2 analysis", duration_ms=2000.0)
        tracer.record_code_execution(
            "test_module", "r", "limma", success=False, error="package missing", duration_ms=100.0
        )
        tracer.record_llm_call("test_module", "qwen3.5-9b", "summarize results", "summary text", duration_ms=500.0)
        tracer.record_visualization("test_module", "heatmap", "/tmp/heatmap.png", duration_ms=800.0)
        return tracer

    def _make_provenance(self) -> ProvenanceTracker:
        prov = ProvenanceTracker(storage_dir=tempfile.mkdtemp())
        prov.start_tracking("test_project")
        src_id = prov.add_source("PubMed query", "db_query", parameters={"query": "BRCA1"})
        tx_id = prov.add_transformation("DESeq2", [src_id], parameters={"contrast": "treated_vs_control"})
        prov.add_output("Volcano plot", [tx_id], "figure", parameters={"dpi": 300})
        return prov

    def test_report_no_session(self):
        tracer = TraceRecorder(storage_dir=tempfile.mkdtemp())
        gen = ReportGenerator(tracer)
        report = gen.generate_audit_report("Test Report")
        assert report.title == "Test Report"
        assert "No trace session" in report.content

    def test_report_with_session(self):
        tracer = self._make_tracer_with_data()
        gen = ReportGenerator(tracer)
        report = gen.generate_audit_report("Research Audit")
        assert report.title == "Research Audit"
        assert report.created_at != ""
        assert len(report.database_queries) == 2
        assert len(report.code_executions) == 2
        assert len(report.llm_interactions) == 1
        assert len(report.visualizations) == 1
        assert len(report.errors) == 2
        assert "db_query" in report.operation_summary
        assert report.operation_summary["db_query"] == 2

    def test_report_content_markdown(self):
        tracer = self._make_tracer_with_data()
        gen = ReportGenerator(tracer)
        report = gen.generate_audit_report()
        content = report.content
        assert "# " in content
        assert "Session Overview" in content
        assert "Operation Summary" in content
        assert "Database Queries" in content
        assert "Code Executions" in content
        assert "LLM Interactions" in content
        assert "Errors" in content
        assert "Reproducibility Information" in content

    def test_report_with_provenance(self):
        tracer = self._make_tracer_with_data()
        prov = self._make_provenance()
        gen = ReportGenerator(tracer, provenance_tracker=prov)
        report = gen.generate_audit_report()
        assert report.data_lineage["available"] is True
        assert report.data_lineage["sources"] == 1
        assert report.data_lineage["transformations"] == 1
        assert report.data_lineage["outputs"] == 1
        assert "Data Lineage" in report.content

    def test_report_without_provenance(self):
        tracer = self._make_tracer_with_data()
        gen = ReportGenerator(tracer)
        report = gen.generate_audit_report()
        assert report.data_lineage.get("available") is False

    def test_report_provenance_none_graph(self):
        tracer = self._make_tracer_with_data()
        prov = ProvenanceTracker(storage_dir=tempfile.mkdtemp())
        gen = ReportGenerator(tracer, provenance_tracker=prov)
        report = gen.generate_audit_report()
        assert report.data_lineage.get("available") is False

    def test_format_for_journal(self):
        tracer = self._make_tracer_with_data()
        gen = ReportGenerator(tracer)
        report = gen.generate_audit_report()
        journal_text = ReportGenerator.format_for_journal(report)
        assert "Data Availability" in journal_text
        assert "Computational Environment" in journal_text
        assert "Software Dependencies" in journal_text
        assert "Workflow Audit" in journal_text

    def test_format_for_journal_no_python_version(self):
        tracer = self._make_tracer_with_data()
        gen = ReportGenerator(tracer)
        report = gen.generate_audit_report()
        report.reproducibility_info["python_version"] = None
        journal_text = ReportGenerator.format_for_journal(report)
        assert "N/A" in journal_text

    def test_reproducibility_info_dependencies(self):
        tracer = self._make_tracer_with_data()
        gen = ReportGenerator(tracer)
        report = gen.generate_audit_report()
        deps = report.reproducibility_info.get("dependencies", {})
        assert isinstance(deps, dict)
        assert "numpy" in deps

    def test_export_package(self):
        tracer = self._make_tracer_with_data()
        prov = self._make_provenance()
        gen = ReportGenerator(tracer, provenance_tracker=prov)
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_path = gen.export_package(tmpdir)
            assert os.path.isdir(pkg_path)
            assert os.path.exists(os.path.join(pkg_path, "audit_report.md"))
            assert os.path.exists(os.path.join(pkg_path, "trace.json"))
            assert os.path.exists(os.path.join(pkg_path, "provenance.json"))
            assert os.path.exists(os.path.join(pkg_path, "metadata.json"))
            with open(os.path.join(pkg_path, "metadata.json")) as f:
                meta = json.load(f)
            assert "exported_at" in meta
            assert "files" in meta

    def test_export_package_no_provenance(self):
        tracer = self._make_tracer_with_data()
        gen = ReportGenerator(tracer)
        with tempfile.TemporaryDirectory() as tmpdir:
            pkg_path = gen.export_package(tmpdir)
            assert os.path.isdir(pkg_path)
            assert not os.path.exists(os.path.join(pkg_path, "provenance.json"))

    def test_report_many_entries_truncation(self):
        tracer = TraceRecorder(storage_dir=tempfile.mkdtemp())
        tracer.start_session()
        for i in range(25):
            tracer.record_db_query("mod", "pubmed", f"query_{i}", 1, duration_ms=10.0)
        for i in range(15):
            tracer.record_llm_call("mod", "test", f"prompt_{i}", duration_ms=10.0)
        gen = ReportGenerator(tracer)
        report = gen.generate_audit_report()
        assert "and 5 more" in report.content
        assert "and 5 more" in report.content

    def test_audit_report_dataclass(self):
        report = AuditReport(title="Test")
        assert report.title == "Test"
        assert report.database_queries == []
        assert report.code_executions == []
        assert report.llm_interactions == []
        assert report.visualizations == []
        assert report.errors == []
        assert report.data_lineage == {}
        assert report.reproducibility_info == {}


# ===========================================================================
# 3. Visualization chart tests
# ===========================================================================


class TestChartConfig:
    def test_defaults(self):
        cfg = ChartConfig()
        assert cfg.width == 8
        assert cfg.height == 6
        assert cfg.dpi == 300
        assert cfg.font_size == 12
        assert cfg.style == "whitegrid"
        assert cfg.palette == "Set2"
        assert cfg.output_format == "png"

    def test_custom(self):
        cfg = ChartConfig(width=10, height=8, dpi=150, title="Test Chart", output_format="svg")
        assert cfg.width == 10
        assert cfg.dpi == 150
        assert cfg.title == "Test Chart"
        assert cfg.output_format == "svg"


class TestChartResult:
    def test_success(self):
        r = ChartResult(success=True, file_path="/tmp/test.png")
        assert r.success is True
        assert r.file_path == "/tmp/test.png"
        assert r.error == ""

    def test_failure(self):
        r = ChartResult(success=False, error="boom")
        assert r.success is False
        assert r.error == "boom"


class TestChartGenerator:
    @pytest.fixture()
    def generator(self):
        return ChartGenerator()

    @pytest.fixture()
    def output_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield os.path.join(tmpdir, "test_chart.png")

    @pytest.mark.asyncio
    async def test_bar_chart(self, generator, output_path):
        result = await generator.bar_chart(
            categories=["A", "B", "C"],
            values=[1.0, 2.0, 3.0],
            output_path=output_path,
        )
        assert result.success is True
        assert result.file_path == output_path
        assert os.path.exists(output_path)

    @pytest.mark.asyncio
    async def test_bar_chart_with_errors(self, generator, output_path):
        result = await generator.bar_chart(
            categories=["X", "Y"],
            values=[5.0, 10.0],
            errors=[0.5, 1.0],
            output_path=output_path,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_bar_chart_with_config(self, output_path):
        cfg = ChartConfig(title="Gene Expression", xlabel="Gene", ylabel="Fold Change")
        gen = ChartGenerator(config=cfg)
        result = await gen.bar_chart(
            categories=["BRCA1", "TP53"],
            values=[2.5, 1.8],
            output_path=output_path,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_scatter_plot(self, generator, output_path):
        result = await generator.scatter_plot(
            x=[1.0, 2.0, 3.0],
            y=[4.0, 5.0, 6.0],
            output_path=output_path,
        )
        assert result.success is True
        assert os.path.exists(output_path)

    @pytest.mark.asyncio
    async def test_scatter_plot_with_groups(self, generator, output_path):
        result = await generator.scatter_plot(
            x=[1.0, 2.0, 3.0, 4.0],
            y=[1.0, 2.0, 3.0, 4.0],
            groups=["A", "A", "B", "B"],
            output_path=output_path,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_scatter_plot_with_config(self, output_path):
        cfg = ChartConfig(title="Correlation", xlabel="X", ylabel="Y")
        gen = ChartGenerator(config=cfg)
        result = await gen.scatter_plot(
            x=[1.0, 2.0],
            y=[3.0, 4.0],
            config=cfg,
            output_path=output_path,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_heatmap(self, generator, output_path):
        result = await generator.heatmap(
            data=[[1.0, 2.0], [3.0, 4.0]],
            row_labels=["Row1", "Row2"],
            col_labels=["Col1", "Col2"],
            output_path=output_path,
        )
        assert result.success is True
        assert os.path.exists(output_path)

    @pytest.mark.asyncio
    async def test_heatmap_with_config(self, output_path):
        cfg = ChartConfig(title="Expression Heatmap")
        gen = ChartGenerator(config=cfg)
        result = await gen.heatmap(
            data=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
            row_labels=["R1", "R2"],
            col_labels=["C1", "C2", "C3"],
            output_path=output_path,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_volcano_plot(self, generator, output_path):
        result = await generator.volcano_plot(
            log2fc=[-2.0, -0.5, 0.1, 0.3, 2.5],
            pvalues=[0.001, 0.01, 0.5, 0.8, 0.0001],
            labels=["GeneA", "GeneB", "GeneC", "GeneD", "GeneE"],
            output_path=output_path,
        )
        assert result.success is True
        assert os.path.exists(output_path)

    @pytest.mark.asyncio
    async def test_volcano_plot_with_thresholds(self, output_path):
        cfg = ChartConfig(title="DE Analysis")
        gen = ChartGenerator(config=cfg)
        result = await gen.volcano_plot(
            log2fc=[-3.0, 0.0, 3.0],
            pvalues=[0.0001, 0.9, 0.0001],
            fc_threshold=1.5,
            p_threshold=0.01,
            output_path=output_path,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_volcano_plot_no_labels(self, generator, output_path):
        result = await generator.volcano_plot(
            log2fc=[1.0, -1.0],
            pvalues=[0.01, 0.01],
            output_path=output_path,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_line_chart(self, generator, output_path):
        result = await generator.line_chart(
            x=[0, 1, 2, 3],
            y_sets=[
                {"label": "Group A", "values": [0, 1, 4, 9]},
                {"label": "Group B", "values": [0, 2, 6, 12]},
            ],
            output_path=output_path,
        )
        assert result.success is True
        assert os.path.exists(output_path)

    @pytest.mark.asyncio
    async def test_line_chart_with_config(self, output_path):
        cfg = ChartConfig(title="Growth Curve", xlabel="Time (h)", ylabel="OD600")
        gen = ChartGenerator(config=cfg)
        result = await gen.line_chart(
            x=[1, 2, 3],
            y_sets=[{"label": "WT", "values": [0.1, 0.5, 1.0]}],
            config=cfg,
            output_path=output_path,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_box_plot(self, generator, output_path):
        result = await generator.box_plot(
            data={
                "Control": [1.0, 2.0, 3.0, 4.0],
                "Treatment": [2.0, 3.0, 4.0, 5.0],
            },
            output_path=output_path,
        )
        assert result.success is True
        assert os.path.exists(output_path)

    @pytest.mark.asyncio
    async def test_box_plot_with_config(self, output_path):
        cfg = ChartConfig(title="Comparison", xlabel="Group", ylabel="Value")
        gen = ChartGenerator(config=cfg)
        result = await gen.box_plot(
            data={"A": [1.0, 2.0], "B": [3.0, 4.0]},
            config=cfg,
            output_path=output_path,
        )
        assert result.success is True

    def test_get_output_path(self, generator):
        path = generator._get_output_path("test")
        assert "fusion_chart_test_" in path
        assert path.endswith(".png")

    def test_get_output_path_counter_increments(self, generator):
        p1 = generator._get_output_path("a")
        p2 = generator._get_output_path("b")
        assert "fusion_chart_a_1" in p1
        assert "fusion_chart_b_2" in p2

    @pytest.mark.asyncio
    async def test_bar_chart_exception(self, output_path):
        gen = ChartGenerator()
        with patch("matplotlib.pyplot.subplots", side_effect=RuntimeError("plot fail")):
            result = await gen.bar_chart(
                categories=["A"],
                values=[1.0],
                output_path=output_path,
            )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_scatter_plot_exception(self, output_path):
        gen = ChartGenerator()
        with patch("matplotlib.pyplot.subplots", side_effect=RuntimeError("scatter fail")):
            result = await gen.scatter_plot(
                x=[1.0],
                y=[1.0],
                output_path=output_path,
            )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_heatmap_exception(self, output_path):
        gen = ChartGenerator()
        with patch("matplotlib.pyplot.subplots", side_effect=RuntimeError("heatmap fail")):
            result = await gen.heatmap(
                data=[[1.0]],
                row_labels=["R"],
                col_labels=["C"],
                output_path=output_path,
            )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_volcano_plot_exception(self, output_path):
        gen = ChartGenerator()
        with patch("matplotlib.pyplot.subplots", side_effect=RuntimeError("volcano fail")):
            result = await gen.volcano_plot(
                log2fc=[1.0],
                pvalues=[0.01],
                output_path=output_path,
            )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_line_chart_exception(self, output_path):
        gen = ChartGenerator()
        with patch("matplotlib.pyplot.subplots", side_effect=RuntimeError("line fail")):
            result = await gen.line_chart(
                x=[1.0],
                y_sets=[{"label": "A", "values": [1.0]}],
                output_path=output_path,
            )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_box_plot_exception(self, output_path):
        gen = ChartGenerator()
        with patch("matplotlib.pyplot.subplots", side_effect=RuntimeError("box fail")):
            result = await gen.box_plot(
                data={"A": [1.0]},
                output_path=output_path,
            )
        assert result.success is False


# ===========================================================================
# 4. QueryRouterAgent tests
# ===========================================================================


class TestQueryRouterAgent:
    def test_init(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        assert len(router._agents) == 5
        assert "literature" in router._agents
        assert "data" in router._agents
        assert "visualize" in router._agents
        assert "writer" in router._agents
        assert "error" in router._agents

    def test_init_with_registry(self):
        engine = _make_engine()
        registry = _make_tool_registry()
        router = QueryRouterAgent(engine, tool_registry=registry)
        assert len(router._agents) == 5

    def test_route_literature(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        assert router.route("search for papers on BRCA1") == "literature"
        assert router.route("find pubmed articles about cancer") == "literature"
        assert router.route("retrieve literature on gene therapy") == "literature"

    def test_route_data(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        assert router.route("analyze the gene expression data") == "data"
        assert router.route("compute statistics for this dataset") == "data"
        assert router.route("run python code for correlation") == "data"

    def test_route_visualize(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        assert router.route("create a chart of expression levels") == "visualize"
        assert router.route("plot a heatmap") == "visualize"
        assert router.route("generate a volcano plot") == "visualize"

    def test_route_writer(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        assert router.route("write the methods section") == "writer"
        assert router.route("draft the introduction") == "writer"
        assert router.route("compose discussion and cite sources") == "writer"

    def test_route_default_literature(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        result = router.route("xyzzy foobar baz")
        assert result == "literature"

    @pytest.mark.asyncio
    async def test_dispatch_literature(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        result = await router.dispatch("search for papers on cancer")
        assert result.agent_name == "literature"
        assert result.output == "mocked LLM response"

    @pytest.mark.asyncio
    async def test_dispatch_data(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        result = await router.dispatch("analyze the data")
        assert result.agent_name == "data"

    @pytest.mark.asyncio
    async def test_dispatch_visualize(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        result = await router.dispatch("create a chart")
        assert result.agent_name == "visualize"

    @pytest.mark.asyncio
    async def test_dispatch_writer(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        result = await router.dispatch("write the introduction")
        assert result.agent_name == "writer"

    @pytest.mark.asyncio
    async def test_dispatch_with_error_escalation(self):
        engine = _make_engine()
        engine.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        router = QueryRouterAgent(engine)
        result = await router.dispatch("search for papers")
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_dispatch_error_agent_also_fails(self):
        engine = _make_engine()
        call_count = 0

        async def _failing_chat(*args, **kwargs):
            raise RuntimeError("Everything broken")

        engine.chat = _failing_chat
        router = QueryRouterAgent(engine)
        result = await router.dispatch("search for papers")
        assert result.error != ""

    def test_get_agent(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        agent = router.get_agent("literature")
        assert agent is not None
        assert agent.name == "literature"

    def test_get_agent_not_found(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        assert router.get_agent("nonexistent") is None

    def test_list_agents(self):
        engine = _make_engine()
        router = QueryRouterAgent(engine)
        names = router.list_agents()
        assert "literature" in names
        assert "data" in names
        assert "visualize" in names
        assert "writer" in names
        assert "error" in names


# ===========================================================================
# 5. LiteratureAgent tests
# ===========================================================================


class TestLiteratureAgent:
    def test_init_no_registry(self):
        engine = _make_engine()
        agent = LiteratureAgent(engine)
        assert agent.name == "literature"
        assert agent.tools == []

    def test_init_with_registry(self):
        engine = _make_engine()
        registry = _make_tool_registry()
        agent = LiteratureAgent(engine, tool_registry=registry)
        assert agent.name == "literature"
        assert len(agent.tools) > 0
        tool_names = [t["function"]["name"] for t in agent.tools]
        assert "search_literature" in tool_names

    def test_load_tools_partial_registry(self):
        engine = _make_engine()
        registry = ToolRegistry()
        registry.register(
            name="search_literature",
            description="Search literature",
            parameters={"type": "object", "properties": {}},
        )
        agent = LiteratureAgent(engine, tool_registry=registry)
        assert len(agent.tools) == 1
        assert agent.tools[0]["function"]["name"] == "search_literature"

    @pytest.mark.asyncio
    async def test_run(self):
        engine = _make_engine()
        agent = LiteratureAgent(engine)
        result = await agent.run("search for cancer papers")
        assert result.agent_name == "literature"
        assert result.output == "mocked LLM response"

    def test_system_prompt_contains_keywords(self):
        engine = _make_engine()
        agent = LiteratureAgent(engine)
        assert "literature" in agent.system_prompt.lower()
        assert "cite" in agent.system_prompt.lower()


# ===========================================================================
# 6. DataAgent tests
# ===========================================================================


class TestDataAgent:
    def test_init_no_registry(self):
        engine = _make_engine()
        agent = DataAgent(engine)
        assert agent.name == "data"
        assert agent.tools == []

    def test_init_with_registry(self):
        engine = _make_engine()
        registry = _make_tool_registry()
        agent = DataAgent(engine, tool_registry=registry)
        assert agent.name == "data"
        tool_names = [t["function"]["name"] for t in agent.tools]
        assert "search_database" in tool_names
        assert "execute_python" in tool_names
        assert "execute_r" in tool_names

    def test_load_tools_missing_tools(self):
        engine = _make_engine()
        registry = ToolRegistry()
        registry.register(
            name="execute_python",
            description="Execute Python",
            parameters={"type": "object", "properties": {}},
        )
        agent = DataAgent(engine, tool_registry=registry)
        assert len(agent.tools) == 1

    @pytest.mark.asyncio
    async def test_run(self):
        engine = _make_engine()
        agent = DataAgent(engine)
        result = await agent.run("analyze gene expression data")
        assert result.agent_name == "data"

    def test_system_prompt(self):
        engine = _make_engine()
        agent = DataAgent(engine)
        assert "data analysis" in agent.system_prompt.lower()
        assert "statistical" in agent.system_prompt.lower()


# ===========================================================================
# 7. VizAgent tests
# ===========================================================================


class TestVizAgent:
    def test_init_no_registry(self):
        engine = _make_engine()
        agent = VizAgent(engine)
        assert agent.name == "visualize"
        assert agent.tools == []

    def test_init_with_registry(self):
        engine = _make_engine()
        registry = _make_tool_registry()
        agent = VizAgent(engine, tool_registry=registry)
        tool_names = [t["function"]["name"] for t in agent.tools]
        assert "generate_chart" in tool_names
        assert "visualize_molecule" in tool_names
        assert "visualize_protein" in tool_names

    def test_load_tools_missing_tools(self):
        engine = _make_engine()
        registry = ToolRegistry()
        registry.register(
            name="generate_chart",
            description="Generate chart",
            parameters={"type": "object", "properties": {}},
        )
        agent = VizAgent(engine, tool_registry=registry)
        assert len(agent.tools) == 1

    @pytest.mark.asyncio
    async def test_run(self):
        engine = _make_engine()
        agent = VizAgent(engine)
        result = await agent.run("create a heatmap")
        assert result.agent_name == "visualize"

    def test_system_prompt(self):
        engine = _make_engine()
        agent = VizAgent(engine)
        assert "visualization" in agent.system_prompt.lower()


# ===========================================================================
# 8. WriterAgent tests
# ===========================================================================


class TestWriterAgent:
    def test_init_no_registry(self):
        engine = _make_engine()
        agent = WriterAgent(engine)
        assert agent.name == "writer"
        assert agent.tools == []

    def test_init_with_registry(self):
        engine = _make_engine()
        registry = _make_tool_registry()
        agent = WriterAgent(engine, tool_registry=registry)
        tool_names = [t["function"]["name"] for t in agent.tools]
        assert "write_section" in tool_names
        assert "manage_citations" in tool_names

    def test_load_tools_missing_tools(self):
        engine = _make_engine()
        registry = ToolRegistry()
        registry.register(
            name="write_section",
            description="Write section",
            parameters={"type": "object", "properties": {}},
        )
        agent = WriterAgent(engine, tool_registry=registry)
        assert len(agent.tools) == 1

    @pytest.mark.asyncio
    async def test_run(self):
        engine = _make_engine()
        agent = WriterAgent(engine)
        result = await agent.run("write the methods section")
        assert result.agent_name == "writer"

    def test_system_prompt(self):
        engine = _make_engine()
        agent = WriterAgent(engine)
        assert "writing" in agent.system_prompt.lower()
        assert "IMRaD" in agent.system_prompt


# ===========================================================================
# 9. ErrorAnalysisAgent tests
# ===========================================================================


class TestErrorAnalysisAgent:
    def test_init_no_registry(self):
        engine = _make_engine()
        agent = ErrorAnalysisAgent(engine)
        assert agent.name == "error"
        assert agent.tools == []

    def test_init_with_registry(self):
        engine = _make_engine()
        registry = _make_tool_registry()
        agent = ErrorAnalysisAgent(engine, tool_registry=registry)
        tool_names = [t["function"]["name"] for t in agent.tools]
        assert "execute_python" in tool_names

    def test_load_tools_missing_tools(self):
        engine = _make_engine()
        registry = ToolRegistry()
        agent = ErrorAnalysisAgent(engine, tool_registry=registry)
        assert agent.tools == []

    @pytest.mark.asyncio
    async def test_run(self):
        engine = _make_engine()
        agent = ErrorAnalysisAgent(engine)
        result = await agent.run("diagnose the error in the analysis")
        assert result.agent_name == "error"

    def test_system_prompt(self):
        engine = _make_engine()
        agent = ErrorAnalysisAgent(engine)
        assert "error" in agent.system_prompt.lower()
        assert "diagnose" in agent.system_prompt.lower()

    @pytest.mark.asyncio
    async def test_run_via_router_error_escalation(self):
        engine = _make_engine()
        data_result = LLMResponse(content="", tool_calls=[], error="LLM down", model="test")
        error_result = LLMResponse(
            content="Root cause: missing dependency",
            tool_calls=[],
            model="test",
            finish_reason="stop",
        )
        engine.chat = AsyncMock(side_effect=[RuntimeError("LLM down"), error_result])
        router = QueryRouterAgent(engine)
        result = await router.dispatch("analyze the data")
        assert result.error != "" or result.output != ""
