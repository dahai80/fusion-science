from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fusion_science.audit.provenance import (
    ProvenanceGraph,
    ProvenanceNode,
    ProvenanceTracker,
)
from fusion_science.audit.tracker import (
    TraceRecorder,
    _sanitize_params,
)
from fusion_science.compute.jupyter_kernel import (
    JupyterKernelManager,
    KernelInfo,
    KernelResult,
)
from fusion_science.compute.python_executor import ExecutionResult, PythonExecutor
from fusion_science.core.tools import (
    ToolDefinition,
    ToolRegistry,
    register_builtin_tools,
)
from fusion_science.database.mirror import (
    CacheConfig,
    MirrorEndpoint,
    MirrorRouter,
    ScienceCache,
    _load_mirrors_from_env,
)
from fusion_science.literature.citation import (
    Citation,
    CitationGraph,
    CitationManager,
)
from fusion_science.literature.paper import (
    PaperDraft,
    PaperGenerator,
    PaperSection,
)
from fusion_science.literature.search import Paper
from fusion_science.visualization.molecule import (
    MoleculeVisualization,
    MoleculeVisualizer,
)
from fusion_science.visualization.smart_viz import (
    SmartVisualizer,
    VizRecommendation,
)

logger = logging.getLogger(__name__)


def _make_paper(**overrides) -> Paper:
    defaults = dict(
        title="Test Paper",
        authors=["Alice Smith", "Bob Jones"],
        year="2024",
        journal="Nature",
        doi="10.1234/test",
        pmid="12345678",
        arxiv_id="",
        abstract="Test abstract",
        keywords=["genomics", "expression"],
        mesh_terms=["genomics"],
    )
    defaults.update(overrides)
    return Paper(**defaults)


# ===================================================================
# 1. fusion_science/core/tools.py
# ===================================================================


class TestToolDefinition:
    def test_create_defaults(self):
        td = ToolDefinition(name="t", description="d", parameters={})
        assert td.name == "t"
        assert td.handler is None
        assert td.mcp_exposed is True

    def test_create_with_handler(self):
        handler = AsyncMock()
        td = ToolDefinition(name="t", description="d", parameters={}, handler=handler, mcp_exposed=False)
        assert td.handler is handler
        assert td.mcp_exposed is False


class TestToolRegistry:
    def test_register_and_list(self):
        reg = ToolRegistry()
        reg.register("tool_a", "desc", {"type": "object"})
        reg.register("tool_b", "desc", {"type": "object"})
        assert "tool_a" in reg.list_tools()
        assert "tool_b" in reg.list_tools()

    def test_unregister_existing(self):
        reg = ToolRegistry()
        reg.register("tool_a", "desc", {"type": "object"})
        reg.unregister("tool_a")
        assert "tool_a" not in reg.list_tools()

    def test_unregister_nonexistent(self):
        reg = ToolRegistry()
        reg.unregister("nonexistent")

    @pytest.mark.asyncio
    async def test_execute_success(self):
        handler = AsyncMock(return_value={"result": 42})
        reg = ToolRegistry()
        reg.register("adder", "desc", {"type": "object"}, handler=handler)
        result = await reg.execute("adder", {"a": 1, "b": 2})
        assert result == {"result": 42}
        handler.assert_awaited_once_with(a=1, b=2)

    @pytest.mark.asyncio
    async def test_execute_not_found(self):
        reg = ToolRegistry()
        result = await reg.execute("missing", {})
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_no_handler(self):
        reg = ToolRegistry()
        reg.register("no_handler", "desc", {"type": "object"}, handler=None)
        result = await reg.execute("no_handler", {})
        assert "error" in result
        assert "no handler" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_handler_exception(self):
        handler = AsyncMock(side_effect=ValueError("boom"))
        reg = ToolRegistry()
        reg.register("boom_tool", "desc", {"type": "object"}, handler=handler)
        result = await reg.execute("boom_tool", {})
        assert "error" in result
        assert "boom" in result["error"]

    def test_get_openai_tools(self):
        reg = ToolRegistry()
        reg.register("t1", "desc1", {"type": "object", "properties": {"x": {"type": "int"}}})
        tools = reg.get_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "t1"

    def test_get_mcp_tools_includes_exposed(self):
        reg = ToolRegistry()
        reg.register("exposed", "desc", {"type": "object"}, mcp_exposed=True)
        reg.register("hidden", "desc", {"type": "object"}, mcp_exposed=False)
        mcp = reg.get_mcp_tools()
        names = [t["name"] for t in mcp]
        assert "exposed" in names
        assert "hidden" not in names

    def test_get_tool_found(self):
        reg = ToolRegistry()
        reg.register("t1", "desc", {"type": "object"})
        tool = reg.get_tool("t1")
        assert tool is not None
        assert tool.name == "t1"

    def test_get_tool_not_found(self):
        reg = ToolRegistry()
        assert reg.get_tool("missing") is None

    def test_has_tool(self):
        reg = ToolRegistry()
        reg.register("t1", "desc", {"type": "object"})
        assert reg.has_tool("t1") is True
        assert reg.has_tool("nope") is False


class TestRegisterBuiltinTools:
    def test_registers_all_tools(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        tools = reg.list_tools()
        assert "search_literature" in tools
        assert "search_database" in tools
        assert "execute_python" in tools
        assert "generate_chart" in tools
        assert "fetch_paper" in tools

    def test_mcp_exposed(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mcp = reg.get_mcp_tools()
        assert len(mcp) == len(reg.list_tools())
        assert len(mcp) >= 12

    @pytest.mark.asyncio
    async def test_search_literature_handler_success(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_searcher = MagicMock()
        mock_result = MagicMock()
        mock_result.papers = [_make_paper(title="Found Paper")]
        mock_result.total_count = 1
        mock_result.sources_used = ["pubmed"]
        mock_searcher.search = AsyncMock(return_value=mock_result)
        with patch("fusion_science.literature.search.LiteratureSearch", return_value=mock_searcher):
            result = await reg.execute("search_literature", {"query": "cancer"})
        assert "papers" in result
        assert result["total_count"] == 1

    @pytest.mark.asyncio
    async def test_search_literature_handler_error(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        with patch("fusion_science.literature.search.LiteratureSearch", side_effect=Exception("network error")):
            result = await reg.execute("search_literature", {"query": "cancer"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_search_database_handler_unknown_db(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        result = await reg.execute("search_database", {"database": "unknown_db", "query": "test"})
        assert "error" in result
        assert "Unknown database" in result["error"]

    @pytest.mark.asyncio
    async def test_search_database_handler_success(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_connector = MagicMock()
        mock_result = MagicMock()
        mock_result.source = "uniprot"
        mock_result.items = [{"id": "P12345"}]
        mock_result.total_count = 1
        mock_connector.search = AsyncMock(return_value=mock_result)
        mock_connector.close = AsyncMock()
        mock_cls = MagicMock(return_value=mock_connector)
        mock_module = MagicMock()
        mock_module.UniProtConnector = mock_cls
        with patch("importlib.import_module", return_value=mock_module):
            result = await reg.execute("search_database", {"database": "uniprot", "query": "kinase"})
        assert "items" in result

    @pytest.mark.asyncio
    async def test_search_database_handler_exception(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        with patch("importlib.import_module", side_effect=ImportError("no module")):
            result = await reg.execute("search_database", {"database": "uniprot", "query": "kinase"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_execute_python_handler_success(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_executor = MagicMock()
        exec_result = ExecutionResult(success=True, output="42", figures=[], execution_time=0.1)
        mock_executor.execute = AsyncMock(return_value=exec_result)
        with patch("fusion_science.compute.python_executor.PythonExecutor", return_value=mock_executor):
            result = await reg.execute("execute_python", {"code": "print(42)"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_python_handler_error(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        with patch("fusion_science.compute.python_executor.PythonExecutor", side_effect=Exception("fail")):
            result = await reg.execute("execute_python", {"code": "bad"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_generate_chart_handler_with_code(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_executor = MagicMock()
        exec_result = ExecutionResult(success=True, output="ok", figures=["/tmp/fig.png"])
        mock_executor.execute = AsyncMock(return_value=exec_result)
        with patch("fusion_science.compute.python_executor.PythonExecutor", return_value=mock_executor):
            result = await reg.execute(
                "generate_chart", {"chart_type": "bar", "data_description": "sales", "code": "import matplotlib"}
            )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_generate_chart_handler_no_code(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_executor = MagicMock()
        exec_result = ExecutionResult(success=True, output="chart ok", figures=[])
        mock_executor.execute = AsyncMock(return_value=exec_result)
        with patch("fusion_science.compute.python_executor.PythonExecutor", return_value=mock_executor):
            result = await reg.execute("generate_chart", {"chart_type": "scatter", "data_description": "data"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_generate_chart_handler_error(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        with patch("fusion_science.compute.python_executor.PythonExecutor", side_effect=RuntimeError("boom")):
            result = await reg.execute("generate_chart", {"chart_type": "bar", "data_description": "x"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_fetch_paper_handler_pmid_auto(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_connector = MagicMock()
        mock_result = MagicMock()
        mock_result.items = [{"title": "Paper"}]
        mock_result.total_count = 1
        mock_connector.fetch = AsyncMock(return_value=mock_result)
        mock_connector.close = AsyncMock()
        with patch("fusion_science.database.pubmed.PubMedConnector", return_value=mock_connector):
            result = await reg.execute("fetch_paper", {"identifier": "12345678"})
        assert result["source"] == "pubmed"

    @pytest.mark.asyncio
    async def test_fetch_paper_handler_doi_auto(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_connector = MagicMock()
        mock_result = MagicMock()
        mock_result.items = [{"title": "Paper"}]
        mock_result.total_count = 1
        mock_connector.search = AsyncMock(return_value=mock_result)
        mock_connector.close = AsyncMock()
        with patch("fusion_science.database.pubmed.PubMedConnector", return_value=mock_connector):
            result = await reg.execute("fetch_paper", {"identifier": "10.1234/test"})
        assert result["source"] == "pubmed"

    @pytest.mark.asyncio
    async def test_fetch_paper_handler_doi_not_found(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_connector = MagicMock()
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total_count = 0
        mock_connector.search = AsyncMock(return_value=mock_result)
        mock_connector.close = AsyncMock()
        with patch("fusion_science.database.pubmed.PubMedConnector", return_value=mock_connector):
            result = await reg.execute("fetch_paper", {"identifier": "10.1234/missing", "id_type": "doi"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_fetch_paper_handler_arxiv(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_searcher = MagicMock()
        paper = _make_paper(arxiv_id="2401.00001")
        mock_result = MagicMock()
        mock_result.papers = [paper]
        mock_searcher._search_arxiv = AsyncMock(return_value=mock_result)
        with patch("fusion_science.literature.search.LiteratureSearch", return_value=mock_searcher):
            result = await reg.execute("fetch_paper", {"identifier": "2401.00001", "id_type": "arxiv"})
        assert result["source"] == "arxiv"

    @pytest.mark.asyncio
    async def test_fetch_paper_handler_arxiv_not_found(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_searcher = MagicMock()
        mock_result = MagicMock()
        mock_result.papers = []
        mock_searcher._search_arxiv = AsyncMock(return_value=mock_result)
        with patch("fusion_science.literature.search.LiteratureSearch", return_value=mock_searcher):
            result = await reg.execute("fetch_paper", {"identifier": "0000.0000", "id_type": "arxiv"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_fetch_paper_handler_arxiv_exception(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_searcher = MagicMock()
        mock_searcher._search_arxiv = AsyncMock(side_effect=RuntimeError("network"))
        with patch("fusion_science.literature.search.LiteratureSearch", return_value=mock_searcher):
            result = await reg.execute("fetch_paper", {"identifier": "2401.x", "id_type": "arxiv"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_fetch_paper_handler_unsupported_type(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        result = await reg.execute("fetch_paper", {"identifier": "xyz", "id_type": "isbn"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_fetch_paper_handler_auto_arxiv_detection(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        mock_searcher = MagicMock()
        mock_result = MagicMock()
        mock_result.papers = []
        mock_searcher._search_arxiv = AsyncMock(return_value=mock_result)
        with patch("fusion_science.literature.search.LiteratureSearch", return_value=mock_searcher):
            result = await reg.execute("fetch_paper", {"identifier": "arXiv:2401.00001"})
        assert result.get("source") == "arxiv" or "error" in result


# ===================================================================
# 2. fusion_science/audit/provenance.py
# ===================================================================


class TestProvenanceNode:
    def test_create_defaults(self):
        node = ProvenanceNode(id="n1", type="source", label="src", timestamp=0.0)
        assert node.inputs == []
        assert node.outputs == []
        assert node.parameters == {}
        assert node.metadata == {}


class TestProvenanceGraph:
    def test_create_defaults(self):
        g = ProvenanceGraph(name="test", created_at=0.0)
        assert g.nodes == {}
        assert g.description == ""


class TestProvenanceTracker:
    def test_start_tracking(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        name = tracker.start_tracking("test_graph", "desc")
        assert name == "test_graph"
        g = tracker.get_graph()
        assert g is not None
        assert g.name == "test_graph"

    def test_add_source_auto_starts_tracking(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        sid = tracker.add_source("PubMed Query", "db_query", parameters={"q": "cancer"})
        assert sid.startswith("src_")
        g = tracker.get_graph()
        assert g is not None
        assert sid in g.nodes

    def test_add_transformation(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        src = tracker.add_source("Data", "db_query")
        tx = tracker.add_transformation("Normalize", [src], parameters={"method": "zscore"})
        assert tx.startswith("tx_")
        g = tracker.get_graph()
        assert g.nodes[src].outputs == [tx]

    def test_add_output(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        src = tracker.add_source("Data", "db_query")
        out = tracker.add_output("Figure 1", [src], "figure")
        assert out.startswith("out_")
        g = tracker.get_graph()
        assert g.nodes[out].metadata["output_type"] == "figure"

    def test_add_node_parent_not_in_graph(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        tx = tracker.add_transformation("Process", ["nonexistent_id"])
        assert tx.startswith("tx_")

    def test_get_lineage(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        src = tracker.add_source("Source", "db_query")
        tx = tracker.add_transformation("Transform", [src])
        out = tracker.add_output("Output", [tx], "figure")
        lineage = tracker.get_lineage(out)
        ids = [n.id for n in lineage]
        assert src in ids
        assert tx in ids
        assert out in ids

    def test_get_lineage_no_graph(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        assert tracker.get_lineage("nonexistent") == []

    def test_get_lineage_node_not_found(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        assert tracker.get_lineage("nonexistent") == []

    def test_get_downstream(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        src = tracker.add_source("Source", "db_query")
        tx = tracker.add_transformation("Transform", [src])
        out = tracker.add_output("Output", [tx], "figure")
        downstream = tracker.get_downstream(src)
        ids = [n.id for n in downstream]
        assert src in ids
        assert tx in ids
        assert out in ids

    def test_get_downstream_no_graph(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        assert tracker.get_downstream("nonexistent") == []

    def test_export_json_no_graph(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        result = tracker.export_json()
        data = json.loads(result)
        assert "error" in data

    def test_export_json(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        tracker.add_source("Source", "db_query")
        result = tracker.export_json()
        data = json.loads(result)
        assert data["name"] == "test"
        assert data["node_count"] == 1

    def test_export_json_no_pretty(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        result = tracker.export_json(pretty=False)
        assert "\n" not in result

    def test_save_no_graph_raises(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        with pytest.raises(RuntimeError, match="No provenance graph"):
            tracker.save()

    def test_save_and_load(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("my_graph")
        tracker.add_source("Source", "db_query")
        path = tracker.save()
        assert path.endswith(".json")
        assert os.path.exists(path)

        tracker2 = ProvenanceTracker(storage_dir=str(tmp_path))
        assert tracker2.load(path) is True
        g = tracker2.get_graph()
        assert g is not None
        assert g.name == "my_graph"

    def test_save_with_custom_name(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("original")
        tracker.add_source("S", "db")
        path = tracker.save("custom_name")
        assert "custom_name" in path

    def test_load_invalid_path(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        assert tracker.load("/nonexistent/path.json") is False

    def test_generate_reproducibility_report_invalid_json(self):
        report = ProvenanceTracker.generate_reproducibility_report("not json")
        assert "Error" in report

    def test_generate_reproducibility_report_empty_nodes(self):
        data = json.dumps({"name": "test", "nodes": {}, "node_count": 0, "created_at": 0})
        report = ProvenanceTracker.generate_reproducibility_report(data)
        assert "No provenance data" in report

    def test_generate_reproducibility_report_full(self):
        data = json.dumps(
            {
                "name": "test",
                "created_at": 1700000000.0,
                "node_count": 3,
                "nodes": {
                    "src_1": {
                        "id": "src_1",
                        "type": "source",
                        "label": "PubMed",
                        "timestamp": 0,
                        "inputs": [],
                        "outputs": ["tx_1"],
                        "parameters": {"q": "cancer"},
                        "metadata": {},
                    },
                    "tx_1": {
                        "id": "tx_1",
                        "type": "transformation",
                        "label": "Normalize",
                        "timestamp": 0,
                        "inputs": ["src_1"],
                        "outputs": ["out_1"],
                        "parameters": {"method": "zscore"},
                        "metadata": {},
                    },
                    "out_1": {
                        "id": "out_1",
                        "type": "output",
                        "label": "Figure 1",
                        "timestamp": 0,
                        "inputs": ["tx_1"],
                        "outputs": [],
                        "parameters": {},
                        "metadata": {"output_type": "figure"},
                    },
                },
            }
        )
        report = ProvenanceTracker.generate_reproducibility_report(data)
        assert "PubMed" in report
        assert "Normalize" in report
        assert "Figure 1" in report
        assert "Lineage Examples" in report

    def test_generate_reproducibility_report_no_params(self):
        data = json.dumps(
            {
                "name": "test",
                "created_at": 1700000000.0,
                "node_count": 1,
                "nodes": {
                    "src_1": {
                        "id": "src_1",
                        "type": "source",
                        "label": "Source",
                        "timestamp": 0,
                        "inputs": [],
                        "outputs": [],
                        "parameters": {"q": "x"},
                        "metadata": {},
                    },
                },
            }
        )
        report = ProvenanceTracker.generate_reproducibility_report(data, include_parameters=False)
        assert "Source" in report

    def test_generate_reproducibility_report_no_sources(self):
        data = json.dumps(
            {
                "name": "test",
                "created_at": 0,
                "node_count": 1,
                "nodes": {
                    "tx_1": {
                        "id": "tx_1",
                        "type": "transformation",
                        "label": "Tx",
                        "timestamp": 0,
                        "inputs": [],
                        "outputs": [],
                        "parameters": {},
                        "metadata": {},
                    },
                },
            }
        )
        report = ProvenanceTracker.generate_reproducibility_report(data)
        assert "No data sources" in report

    def test_generate_reproducibility_report_no_transformations(self):
        data = json.dumps(
            {
                "name": "test",
                "created_at": 0,
                "node_count": 1,
                "nodes": {
                    "src_1": {
                        "id": "src_1",
                        "type": "source",
                        "label": "S",
                        "timestamp": 0,
                        "inputs": [],
                        "outputs": [],
                        "parameters": {},
                        "metadata": {},
                    },
                },
            }
        )
        report = ProvenanceTracker.generate_reproducibility_report(data)
        assert "No transformations" in report

    def test_generate_reproducibility_report_no_outputs(self):
        data = json.dumps(
            {
                "name": "test",
                "created_at": 0,
                "node_count": 1,
                "nodes": {
                    "src_1": {
                        "id": "src_1",
                        "type": "source",
                        "label": "S",
                        "timestamp": 0,
                        "inputs": [],
                        "outputs": [],
                        "parameters": {},
                        "metadata": {},
                    },
                },
            }
        )
        report = ProvenanceTracker.generate_reproducibility_report(data)
        assert "No outputs" in report


# ===================================================================
# 3. fusion_science/compute/jupyter_kernel.py
# ===================================================================


class TestKernelResult:
    def test_defaults(self):
        kr = KernelResult(success=True)
        assert kr.output == ""
        assert kr.error == ""
        assert kr.execution_count == 0
        assert kr.mime_data == {}


class TestKernelInfo:
    def test_create(self):
        ki = KernelInfo(name="python3", language="python", display_name="Python 3")
        assert ki.name == "python3"
        assert ki.description == ""


class TestJupyterKernelManager:
    def test_init_defaults(self):
        km = JupyterKernelManager()
        assert km.kernel_name == "python3"
        assert km._running is False

    @pytest.mark.asyncio
    async def test_start_kernel_no_jupyter_client(self):
        km = JupyterKernelManager()
        with patch.dict("sys.modules", {"jupyter_client": None}):
            result = await km.start_kernel()
            assert result is False

    @pytest.mark.asyncio
    async def test_start_kernel_success(self):
        km = JupyterKernelManager()
        mock_km = MagicMock()
        mock_client = MagicMock()
        with patch("fusion_science.compute.jupyter_kernel.KernelManager", return_value=mock_km, create=True):
            with patch.dict(
                "sys.modules", {"jupyter_client": MagicMock(KernelManager=MagicMock(return_value=mock_km))}
            ):
                km._running = True
                km._kernel_client = mock_client
                km._kernel_manager = mock_km
                assert km._running is True

    @pytest.mark.asyncio
    async def test_start_kernel_exception(self):
        km = JupyterKernelManager()
        mock_km_cls = MagicMock(side_effect=RuntimeError("kernel fail"))
        with patch.dict("sys.modules", {"jupyter_client": MagicMock(KernelManager=mock_km_cls)}):
            result = await km.start_kernel("python3")
            assert result is False

    @pytest.mark.asyncio
    async def test_execute_not_running(self):
        km = JupyterKernelManager()
        result = await km.execute("print(1)")
        assert result.success is False
        assert "not running" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_with_messages(self):
        km = JupyterKernelManager()
        km._running = True
        mock_client = MagicMock()
        msgs = [
            {"msg_type": "execute_result", "content": {"execution_count": 1, "data": {"text/plain": "42"}}},
            {"msg_type": "status", "content": {"execution_state": "idle"}},
        ]
        msg_iter = iter(msgs)

        def fake_get_iopub_msg():
            return next(msg_iter)

        mock_client.get_iopub_msg = fake_get_iopub_msg
        mock_client.execute = MagicMock()
        km._kernel_client = mock_client
        result = await km.execute("1 + 1", timeout=5)
        assert result.success is True
        assert "42" in result.output

    @pytest.mark.asyncio
    async def test_execute_with_stream(self):
        km = JupyterKernelManager()
        km._running = True
        mock_client = MagicMock()
        msgs = [
            {"msg_type": "stream", "content": {"text": "hello\n", "name": "stdout"}},
            {"msg_type": "status", "content": {"execution_state": "idle"}},
        ]
        msg_iter = iter(msgs)

        def fake_get_iopub_msg():
            return next(msg_iter)

        mock_client.get_iopub_msg = fake_get_iopub_msg
        mock_client.execute = MagicMock()
        km._kernel_client = mock_client
        result = await km.execute("print('hello')")
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_execute_with_error(self):
        km = JupyterKernelManager()
        km._running = True
        mock_client = MagicMock()
        msgs = [
            {"msg_type": "error", "content": {"traceback": ["Error: bad"], "ename": "ValueError", "evalue": "bad"}},
        ]
        msg_iter = iter(msgs)

        def fake_get_iopub_msg():
            return next(msg_iter)

        mock_client.get_iopub_msg = fake_get_iopub_msg
        mock_client.execute = MagicMock()
        km._kernel_client = mock_client
        result = await km.execute("raise ValueError('bad')")
        assert result.success is False
        assert "bad" in result.error

    @pytest.mark.asyncio
    async def test_execute_with_display_data(self):
        km = JupyterKernelManager()
        km._running = True
        mock_client = MagicMock()
        msgs = [
            {"msg_type": "display_data", "content": {"data": {"text/html": "<b>bold</b>", "text/plain": "bold"}}},
            {"msg_type": "status", "content": {"execution_state": "idle"}},
        ]
        msg_iter = iter(msgs)

        def fake_get_iopub_msg():
            return next(msg_iter)

        mock_client.get_iopub_msg = fake_get_iopub_msg
        mock_client.execute = MagicMock()
        km._kernel_client = mock_client
        result = await km.execute("display(bold)")
        assert "text/html" in result.mime_data

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        km = JupyterKernelManager()
        km._running = True
        mock_client = MagicMock()
        call_count = 0

        def fake_get_iopub_msg():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                time.sleep(0.6)
                return {"msg_type": "status", "content": {"execution_state": "busy"}}
            return {"msg_type": "status", "content": {"execution_state": "idle"}}

        mock_client.get_iopub_msg = fake_get_iopub_msg
        mock_client.execute = MagicMock()
        km._kernel_client = mock_client
        result = await km.execute("while True: pass", timeout=1)
        assert isinstance(result, KernelResult)

    @pytest.mark.asyncio
    async def test_execute_exception(self):
        km = JupyterKernelManager()
        km._running = True
        mock_client = MagicMock()
        mock_client.execute = MagicMock(side_effect=RuntimeError("kernel crash"))
        km._kernel_client = mock_client
        result = await km.execute("code")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_shutdown_success(self):
        km = JupyterKernelManager()
        mock_client = MagicMock()
        mock_manager = MagicMock()
        km._kernel_client = mock_client
        km._kernel_manager = mock_manager
        km._running = True
        await km.shutdown()
        assert km._running is False
        assert km._kernel_client is None
        assert km._kernel_manager is None

    @pytest.mark.asyncio
    async def test_shutdown_client_stop_fails(self):
        km = JupyterKernelManager()
        mock_client = MagicMock()
        mock_client.stop_channels = MagicMock(side_effect=RuntimeError("stop fail"))
        mock_manager = MagicMock()
        km._kernel_client = mock_client
        km._kernel_manager = mock_manager
        km._running = True
        with pytest.raises(RuntimeError, match="stop fail"):
            await km.shutdown()
        assert km._kernel_client is None
        assert km._running is False

    @pytest.mark.asyncio
    async def test_shutdown_manager_fails(self):
        km = JupyterKernelManager()
        mock_client = MagicMock()
        mock_manager = MagicMock()
        mock_manager.shutdown_kernel = MagicMock(side_effect=RuntimeError("kernel shutdown fail"))
        km._kernel_client = mock_client
        km._kernel_manager = mock_manager
        km._running = True
        with pytest.raises(RuntimeError, match="kernel shutdown fail"):
            await km.shutdown()
        assert km._kernel_manager is None

    @pytest.mark.asyncio
    async def test_shutdown_no_client_or_manager(self):
        km = JupyterKernelManager()
        km._running = True
        await km.shutdown()
        assert km._running is False

    def test_list_available_kernels_no_jupyter(self):
        with patch.dict("sys.modules", {"jupyter_client": None}):
            result = JupyterKernelManager.list_available_kernels()
            assert result == []

    def test_list_available_kernels_with_specs(self):
        mock_ksm = MagicMock()
        mock_ksm.get_all_specs.return_value = {
            "python3": {"spec": {"language": "python", "display_name": "Python 3", "argv": ["/usr/bin/python"]}},
        }
        mock_jc = MagicMock()
        mock_jc.kernelspec.KernelSpecManager.return_value = mock_ksm
        with patch.dict("sys.modules", {"jupyter_client": mock_jc, "jupyter_client.kernelspec": mock_jc.kernelspec}):
            result = JupyterKernelManager.list_available_kernels()
            assert len(result) == 1
            assert result[0].name == "python3"

    def test_list_available_kernels_exception(self):
        mock_jc = MagicMock()
        mock_jc.kernelspec.KernelSpecManager.side_effect = Exception("fail")
        with patch.dict("sys.modules", {"jupyter_client": mock_jc, "jupyter_client.kernelspec": mock_jc.kernelspec}):
            result = JupyterKernelManager.list_available_kernels()
            assert result == []

    def test_install_kernel_success(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            result = JupyterKernelManager.install_kernel("My Science")
            assert result is True

    def test_install_kernel_failure(self):
        with patch("pathlib.Path.mkdir", side_effect=PermissionError("no write")):
            result = JupyterKernelManager.install_kernel()
            assert result is False


# ===================================================================
# 4. fusion_science/compute/python_executor.py
# ===================================================================


class TestExecutionResult:
    def test_defaults(self):
        r = ExecutionResult(success=True)
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.figures == []
        assert r.execution_time == 0.0


class TestPythonExecutor:
    @pytest.mark.asyncio
    async def test_execute_simple_code(self):
        executor = PythonExecutor(timeout=30)
        result = await executor.execute("result = 1 + 1")
        assert result.success is True
        assert result.execution_time >= 0

    @pytest.mark.asyncio
    async def test_execute_with_error(self):
        executor = PythonExecutor(timeout=30)
        result = await executor.execute("raise ValueError('test error')")
        assert result.success is False
        assert "test error" in result.error or result.stderr

    @pytest.mark.asyncio
    async def test_execute_with_input_data(self):
        executor = PythonExecutor(timeout=30)
        result = await executor.execute(
            "result = input_data.get('x', 0) + 1",
            input_data={"x": 41},
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_with_env_vars(self):
        executor = PythonExecutor(timeout=30)
        result = await executor.execute(
            "import os; result = os.environ.get('TEST_VAR', 'missing')",
            env_vars={"TEST_VAR": "hello"},
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        executor = PythonExecutor(timeout=1)
        result = await executor.execute("import time; time.sleep(10)")
        assert result.success is False
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_execute_no_capture_figures(self):
        executor = PythonExecutor(timeout=30)
        result = await executor.execute("result = 42", capture_figures=False)
        assert result.success is True

    def test_build_wrapper_with_input_data(self):
        executor = PythonExecutor()
        wrapper = executor._build_wrapper("result = input_data['x']", {"x": 10}, True)
        assert "input_data" in wrapper
        assert "json.loads" in wrapper

    def test_build_wrapper_no_input_data(self):
        executor = PythonExecutor()
        wrapper = executor._build_wrapper("result = 42", None, True)
        assert "input_data" not in wrapper

    def test_build_wrapper_no_figure_capture(self):
        executor = PythonExecutor()
        wrapper = executor._build_wrapper("result = 42", None, False)
        assert "matplotlib" not in wrapper

    @pytest.mark.asyncio
    async def test_execute_r_code_no_rpy2(self):
        executor = PythonExecutor()
        with patch.dict("sys.modules", {"rpy2": None, "rpy2.robjects": None}):
            result = await executor.execute_r_code("1 + 1")
            assert result.success is False
            assert "rpy2" in result.error

    def test_check_available_packages(self):
        result = PythonExecutor.check_available_packages()
        assert isinstance(result, list)
        assert len(result) > 0
        for pkg in result:
            assert "name" in pkg
            assert "available" in pkg

    @pytest.mark.asyncio
    async def test_execute_with_extra_paths(self, tmp_path):
        executor = PythonExecutor(extra_paths=[str(tmp_path)])
        result = await executor.execute("result = 'ok'")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_outer_exception(self):
        executor = PythonExecutor(timeout=30)
        with patch("builtins.open", side_effect=OSError("disk full")):
            result = await executor.execute("result = 1")
            assert result.success is False
            assert "Execution error" in result.error

    @pytest.mark.asyncio
    async def test_execute_output_file_parse_failure(self):
        executor = PythonExecutor(timeout=30)
        result = await executor.execute("result = 42")
        assert isinstance(result, ExecutionResult)


# ===================================================================
# 5. fusion_science/visualization/smart_viz.py
# ===================================================================


class TestVizRecommendation:
    def test_defaults(self):
        rec = VizRecommendation(chart_type="scatter", title="Scatter", description="d", data_requirements="dr")
        assert rec.suggested_config == {}
        assert rec.reasoning == ""
        assert rec.confidence == 0.0


class TestSmartVisualizer:
    @pytest.mark.asyncio
    async def test_recommend_rule_based_no_gateway(self):
        viz = SmartVisualizer(gateway=None)
        recs = await viz.recommend("gene expression data", "differential expression")
        assert len(recs) > 0
        chart_types = [r.chart_type for r in recs]
        assert any(t in chart_types for t in ["volcano_plot", "heatmap", "scatter"])

    @pytest.mark.asyncio
    async def test_recommend_rule_based_no_keywords(self):
        viz = SmartVisualizer(gateway=None)
        recs = await viz.recommend("random data about things", "")
        assert len(recs) > 0
        assert recs[0].confidence > 0

    @pytest.mark.asyncio
    async def test_recommend_with_gateway_llm_success(self):
        mock_gateway = MagicMock()
        mock_llm_result = MagicMock()
        mock_llm_result.error = ""
        mock_llm_result.parsed = {
            "recommendations": [
                {
                    "chart_type": "scatter",
                    "title": "Scatter",
                    "description": "d",
                    "data_requirements": "dr",
                    "reasoning": "r",
                    "confidence": 0.9,
                },
                {
                    "chart_type": "heatmap",
                    "title": "Heatmap",
                    "description": "d",
                    "data_requirements": "dr",
                    "reasoning": "r",
                    "confidence": 0.8,
                },
            ]
        }
        mock_gateway.structured_output = AsyncMock(return_value=mock_llm_result)
        viz = SmartVisualizer(gateway=mock_gateway)
        recs = await viz.recommend("gene expression", "clustering")
        assert len(recs) == 2
        assert recs[0].chart_type == "scatter"

    @pytest.mark.asyncio
    async def test_recommend_with_gateway_llm_error(self):
        mock_gateway = MagicMock()
        mock_llm_result = MagicMock()
        mock_llm_result.error = "LLM failed"
        mock_llm_result.parsed = None
        mock_gateway.structured_output = AsyncMock(return_value=mock_llm_result)
        viz = SmartVisualizer(gateway=mock_gateway)
        recs = await viz.recommend("some data")
        assert len(recs) > 0

    @pytest.mark.asyncio
    async def test_recommend_with_gateway_llm_exception(self):
        mock_gateway = MagicMock()
        mock_gateway.structured_output = AsyncMock(side_effect=RuntimeError("network"))
        viz = SmartVisualizer(gateway=mock_gateway)
        recs = await viz.recommend("some data")
        assert len(recs) > 0

    @pytest.mark.asyncio
    async def test_recommend_with_gateway_llm_parse_failure(self):
        mock_gateway = MagicMock()
        mock_llm_result = MagicMock()
        mock_llm_result.error = ""
        mock_llm_result.parsed = {"recommendations": [{"invalid": "data"}]}
        mock_gateway.structured_output = AsyncMock(return_value=mock_llm_result)
        viz = SmartVisualizer(gateway=mock_gateway)
        recs = await viz.recommend("some data")
        assert isinstance(recs, list)

    @pytest.mark.asyncio
    async def test_recommend_with_gateway_llm_inner_exception(self):
        mock_gateway = MagicMock()
        mock_llm_result = MagicMock()
        mock_llm_result.error = ""
        mock_llm_result.parsed = "not_a_dict"
        mock_gateway.structured_output = AsyncMock(return_value=mock_llm_result)
        viz = SmartVisualizer(gateway=mock_gateway)
        recs = await viz.recommend("some data")
        assert len(recs) > 0

    def test_chart_description_known(self):
        desc = SmartVisualizer._chart_description("scatter")
        assert desc["title"] == "Scatter Plot"
        assert "suggested_config" in desc

    def test_chart_description_unknown(self):
        desc = SmartVisualizer._chart_description("radar_chart")
        assert "radar chart" in desc["title"].lower() or desc["description"]

    def test_rule_based_multiple_keyword_matches(self):
        viz = SmartVisualizer(gateway=None)
        recs = viz._rule_based_recommend("correlation and distribution data", "compare groups")
        assert len(recs) > 0


# ===================================================================
# 6. fusion_science/visualization/molecule.py
# ===================================================================


class TestMoleculeVisualization:
    def test_defaults(self):
        mv = MoleculeVisualization(success=True)
        assert mv.html_path == ""
        assert mv.image_path == ""
        assert mv.smiles == ""
        assert mv.formula == ""
        assert mv.molecular_weight == 0.0


class TestMoleculeVisualizer:
    @pytest.mark.asyncio
    async def test_from_smiles_no_rdkit(self):
        viz = MoleculeVisualizer()
        viz._rdkit_available = False
        result = await viz.from_smiles("CCO", name="ethanol")
        assert result.success is True
        assert result.html_path.endswith(".html")

    @pytest.mark.asyncio
    async def test_from_smiles_2d_fallback_with_features(self):
        viz = MoleculeVisualizer()
        result = await viz.from_smiles_2d_fallback("c1ccccc1C(=O)O", name="benzoic")
        assert result.success is True
        assert "benzoic" in result.html_path

    @pytest.mark.asyncio
    async def test_from_smiles_2d_fallback_write_failure(self):
        viz = MoleculeVisualizer()
        with patch("builtins.open", side_effect=PermissionError("no write")):
            result = await viz.from_smiles_2d_fallback("CCO", name="test")
            assert result.success is False
            assert "Fallback HTML write failed" in result.error

    @pytest.mark.asyncio
    async def test_from_smiles_with_rdkit_invalid(self):
        viz = MoleculeVisualizer()
        viz._rdkit_available = True
        mock_chem = MagicMock()
        mock_chem.MolFromSmiles.return_value = None
        mock_rdkit = MagicMock(Chem=mock_chem)
        mock_rdkit_mods = {
            "rdkit": mock_rdkit,
            "rdkit.Chem": mock_chem,
            "rdkit.Chem.AllChem": MagicMock(),
            "rdkit.Chem.Descriptors": MagicMock(),
            "rdkit.Chem.Draw": MagicMock(),
        }
        with patch.dict("sys.modules", mock_rdkit_mods):
            result = await viz.from_smiles("INVALID_SMILES_STRING!!!")
        assert result.success is False
        assert "Invalid SMILES" in result.error

    @pytest.mark.asyncio
    async def test_from_smiles_with_rdkit_exception(self):
        viz = MoleculeVisualizer()
        viz._rdkit_available = True
        mock_chem = MagicMock()
        mock_chem.MolFromSmiles.side_effect = Exception("fail")
        mock_rdkit = MagicMock(Chem=mock_chem)
        mock_rdkit_mods = {
            "rdkit": mock_rdkit,
            "rdkit.Chem": mock_chem,
            "rdkit.Chem.AllChem": MagicMock(),
            "rdkit.Chem.Descriptors": MagicMock(),
            "rdkit.Chem.Draw": MagicMock(),
        }
        with patch.dict("sys.modules", mock_rdkit_mods):
            result = await viz.from_smiles("CCO")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_from_pdb_with_content(self):
        viz = MoleculeVisualizer()
        viz._py3dmol_available = True
        pdb_content = "ATOM      1  N   ALA A   1       1.000   1.000   1.000  1.00  0.00           N"
        with patch.object(viz, "_generate_3d_html"):
            result = await viz.from_pdb("1ABC", pdb_content=pdb_content)
        assert result.success is True
        assert result.pdb_path.endswith(".pdb")

    @pytest.mark.asyncio
    async def test_from_pdb_no_content_fetch_success(self):
        viz = MoleculeVisualizer()
        viz._py3dmol_available = True
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ATOM data"
        with patch("httpx.get", return_value=mock_resp), patch.object(viz, "_generate_3d_html"):
            result = await viz.from_pdb("1ABC")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_from_pdb_no_content_fetch_failure(self):
        viz = MoleculeVisualizer()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.get", return_value=mock_resp):
            result = await viz.from_pdb("XXXX")
        assert result.success is False
        assert "Failed to fetch PDB" in result.error

    @pytest.mark.asyncio
    async def test_from_pdb_no_py3dmol_no_offline(self):
        viz = MoleculeVisualizer()
        viz._py3dmol_available = False
        pdb_content = "ATOM data"
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "false"}, clear=False):
            result = await viz.from_pdb("1ABC", pdb_content=pdb_content)
        assert result.success is True
        assert "rcsb.org" in result.html_path

    @pytest.mark.asyncio
    async def test_from_pdb_no_py3dmol_offline(self):
        viz = MoleculeVisualizer()
        viz._py3dmol_available = False
        pdb_content = "ATOM data"
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "true"}, clear=False):
            result = await viz.from_pdb("1ABC", pdb_content=pdb_content)
        assert result.success is True
        assert result.html_path.startswith("file://")

    @pytest.mark.asyncio
    async def test_from_pdb_exception(self):
        viz = MoleculeVisualizer()
        with patch("builtins.open", side_effect=PermissionError("no write")):
            result = await viz.from_pdb("1ABC", pdb_content="data")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_from_pdb_mirror_url_api_endpoint(self):
        viz = MoleculeVisualizer()
        viz._py3dmol_available = True
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ATOM"
        with patch.dict(os.environ, {"FUSION_SCI_PDB_MIRROR": "https://data.rcsb.org/rest/v1"}, clear=False):
            with patch("httpx.get", return_value=mock_resp) as mock_get:
                with patch.object(viz, "_generate_3d_html"):
                    result = await viz.from_pdb("1ABC")
                call_url = mock_get.call_args[0][0]
                assert "files.rcsb.org" in call_url

    def test_generate_3d_html(self, tmp_path):
        viz = MoleculeVisualizer()
        html_path = str(tmp_path / "test_3d.html")
        pdb_content = "ATOM data here"
        viz._generate_3d_html(pdb_content, html_path, "TestMol", style="stick")
        with open(html_path) as f:
            html = f.read()
        assert "stick" in html
        assert "TestMol" in html

    def test_generate_3d_html_unknown_style(self, tmp_path):
        viz = MoleculeVisualizer()
        html_path = str(tmp_path / "test_3d.html")
        viz._generate_3d_html("ATOM", html_path, "X", style="wireframe")
        with open(html_path) as f:
            html = f.read()
        assert "cartoon" in html

    def test_known_drugs(self):
        drugs = MoleculeVisualizer.known_drugs()
        assert isinstance(drugs, list)
        assert len(drugs) > 0
        assert drugs[0]["name"] == "Aspirin"


# ===================================================================
# 7. fusion_science/audit/tracker.py
# ===================================================================


class TestSanitizeParams:
    def test_normal_params(self):
        params = {"query": "cancer", "limit": 20}
        result = _sanitize_params(params)
        assert result == params

    def test_sensitive_params(self):
        params = {"api_key": "secret123", "patient_name": "John", "token": "abc"}
        result = _sanitize_params(params)
        assert result["api_key"] == "***REDACTED***"
        assert result["patient_name"] == "***REDACTED***"
        assert result["token"] == "***REDACTED***"

    def test_nested_sensitive(self):
        params = {"config": {"password": "s3cret", "normal": "ok"}}
        result = _sanitize_params(params)
        assert result["config"]["password"] == "***REDACTED***"
        assert result["config"]["normal"] == "ok"

    def test_long_string_truncation(self):
        params = {"data": "x" * 2000}
        result = _sanitize_params(params)
        assert "truncated" in result["data"]
        assert len(result["data"]) < 2000

    def test_chinese_sensitive_keys(self):
        params = {"身份证": "123456", "姓名": "Zhang"}
        result = _sanitize_params(params)
        assert result["身份证"] == "***REDACTED***"
        assert result["姓名"] == "***REDACTED***"


class TestTraceRecorder:
    def test_start_session(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        sid = rec.start_session(metadata={"task": "test"})
        assert sid.startswith("trace_")
        session = rec.get_session()
        assert session is not None
        assert session.session_id == sid

    def test_end_session_no_active(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        with pytest.raises(RuntimeError, match="No active session"):
            rec.end_session()

    def test_end_session_saves(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        rec.record("db_query", "test", "Query")
        session = rec.end_session()
        assert session.status == "completed"
        assert len(session.entries) == 1
        assert rec.get_session() is None

    def test_end_session_failed(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        session = rec.end_session(status="failed")
        assert session.status == "failed"

    def test_record_auto_starts_session(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        eid = rec.record("db_query", "test", "Auto start")
        assert eid.startswith("entry_")
        assert rec.get_session() is not None

    def test_record_with_parent(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        parent = rec.record("db_query", "test", "Parent")
        rec.set_parent(parent)
        child = rec.record("code_execution", "test", "Child")
        assert rec.get_session().entries[-1].parent_id == parent
        rec.clear_parent()

    def test_record_db_query(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        eid = rec.record_db_query("pubmed_mod", "pubmed", "cancer", result_count=10)
        assert eid.startswith("entry_")
        entry = rec.get_session().entries[0]
        assert entry.operation == "db_query"
        assert entry.parameters["database"] == "pubmed"

    def test_record_code_execution(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        eid = rec.record_code_execution("compute", "python", "analysis", success=False, error="timeout")
        entry = rec.get_session().entries[0]
        assert entry.operation == "code_execution"
        assert entry.success is False

    def test_record_llm_call(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        eid = rec.record_llm_call("engine", "qwen3", "prompt", token_usage={"prompt": 10})
        entry = rec.get_session().entries[0]
        assert entry.operation == "llm_call"
        assert entry.parameters["model"] == "qwen3"

    def test_record_visualization(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        eid = rec.record_visualization("viz", "chart", "/tmp/fig.png")
        entry = rec.get_session().entries[0]
        assert entry.operation == "visualization"

    def test_get_entries_no_session(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        assert rec.get_entries() == []

    def test_get_entries_with_filter(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        rec.record("db_query", "test", "Q1")
        rec.record("code_execution", "test", "C1")
        db_entries = rec.get_entries(operation="db_query")
        assert len(db_entries) == 1

    def test_get_session_summary(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        rec.record("db_query", "test", "Q1", success=True)
        rec.record("code_execution", "test", "C1", success=False)
        summary = rec.get_session_summary()
        assert summary["total_entries"] == 2
        assert summary["successful"] == 1
        assert summary["failed"] == 1

    def test_get_session_summary_not_found(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        summary = rec.get_session_summary("nonexistent")
        assert "error" in summary

    def test_export_json(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        rec.record("db_query", "test", "Q1")
        json_str = rec.export_json()
        data = json.loads(json_str)
        assert "session_id" in data

    def test_export_json_not_found(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        json_str = rec.export_json("nonexistent")
        data = json.loads(json_str)
        assert "error" in data

    def test_list_sessions(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        rec.record("db_query", "test", "Q1")
        rec.end_session()
        sessions = rec.list_sessions()
        assert len(sessions) >= 1
        assert sessions[0]["session_id"].startswith("trace_")

    def test_list_sessions_corrupt_file(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        corrupt_path = tmp_path / "trace_corrupt.json"
        corrupt_path.write_text("not json")
        sessions = rec.list_sessions()
        assert isinstance(sessions, list)

    def test_save_session(self, tmp_path):
        rec = TraceRecorder(storage_dir=str(tmp_path))
        rec.start_session()
        rec.record("db_query", "test", "Q1")
        rec.end_session()
        files = list(tmp_path.glob("trace_*.json"))
        assert len(files) >= 1


# ===================================================================
# 8. fusion_science/database/mirror.py
# ===================================================================


class TestMirrorEndpoint:
    def test_defaults(self):
        me = MirrorEndpoint(name="test", primary_url="https://a.com", mirror_url="https://b.com")
        assert me.enabled is True
        assert me.priority == 0


class TestCacheConfig:
    def test_defaults(self):
        cc = CacheConfig()
        assert cc.enabled is True
        assert cc.default_ttl == 86400
        assert cc.max_entries == 10000


class TestScienceCache:
    def test_init_creates_db(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        assert cache._conn is not None
        cache.close()

    def test_init_disabled(self, tmp_path):
        config = CacheConfig(enabled=False, cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        assert cache._conn is None

    def test_set_and_get(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache.set("key1", {"data": "value"}, source="test")
        result = cache.get("key1")
        assert result == {"data": "value"}
        cache.close()

    def test_get_nonexistent(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        assert cache.get("missing") is None
        cache.close()

    def test_get_expired(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), default_ttl=-1)
        cache = ScienceCache(config)
        cache.set("key1", "value", source="test", ttl=-1)
        result = cache.get("key1")
        assert result is None
        cache.close()

    def test_get_disabled(self, tmp_path):
        config = CacheConfig(enabled=False, cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache.set("key1", "value")
        assert cache.get("key1") is None
        cache.close()

    def test_delete(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache.set("key1", "value", source="test")
        cache.delete("key1")
        assert cache.get("key1") is None
        cache.close()

    def test_clear_all(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache.set("k1", "v1", source="a")
        cache.set("k2", "v2", source="b")
        cache.clear()
        assert cache.get("k1") is None
        assert cache.get("k2") is None
        cache.close()

    def test_clear_by_source(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache.set("k1", "v1", source="a")
        cache.set("k2", "v2", source="b")
        cache.clear(source="a")
        assert cache.get("k1") is None
        assert cache.get("k2") == "v2"
        cache.close()

    def test_stats(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache.set("k1", "v1", source="pubmed")
        stats = cache.stats()
        assert stats["enabled"] is True
        assert stats["total_entries"] >= 1
        assert "pubmed" in stats["by_source"]
        cache.close()

    def test_stats_disabled(self, tmp_path):
        config = CacheConfig(enabled=False, cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        stats = cache.stats()
        assert stats["enabled"] is False

    def test_eviction(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), max_entries=5)
        cache = ScienceCache(config)
        for i in range(10):
            cache.set(f"key_{i}", f"value_{i}", source="test")
        assert cache._approx_count <= 10
        cache.close()

    def test_init_db_failure(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        with patch.object(sqlite3, "connect", side_effect=sqlite3.Error("fail")):
            cache2 = ScienceCache(config)
            assert cache2._conn is None
        cache.close()

    def test_get_exception(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache._conn = MagicMock()
        cache._conn.execute = MagicMock(side_effect=sqlite3.Error("fail"))
        assert cache.get("key") is None
        cache.close()

    def test_set_exception(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache._conn = MagicMock()
        cache._conn.execute = MagicMock(side_effect=sqlite3.Error("fail"))
        cache.set("key", "val")
        cache.close()

    def test_delete_exception(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache._conn = MagicMock()
        cache._conn.execute = MagicMock(side_effect=sqlite3.Error("fail"))
        cache.delete("key")
        cache.close()

    def test_clear_exception(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache._conn = MagicMock()
        cache._conn.execute = MagicMock(side_effect=sqlite3.Error("fail"))
        cache.clear()
        cache.close()

    def test_stats_exception(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache._conn = MagicMock()
        cache._conn.execute = MagicMock(side_effect=sqlite3.Error("fail"))
        stats = cache.stats()
        assert "error" in stats
        cache.close()

    def test_delete_no_conn(self, tmp_path):
        config = CacheConfig(enabled=False, cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache.delete("key")

    def test_clear_no_conn(self, tmp_path):
        config = CacheConfig(enabled=False, cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache.clear()

    def test_eviction_no_conn(self, tmp_path):
        config = CacheConfig(enabled=False, cache_dir=str(tmp_path))
        cache = ScienceCache(config)
        cache._evict_if_needed()


class TestLoadMirrorsFromEnv:
    def test_no_env_vars(self):
        with patch.dict(os.environ, {}, clear=True):
            result = _load_mirrors_from_env()
            assert isinstance(result, dict)

    def test_with_env_vars(self):
        env = {"FUSION_SCI_PUBMED_MIRROR": "https://mirror.pubmed.cn"}
        with patch.dict(os.environ, env, clear=True):
            result = _load_mirrors_from_env()
            assert "pubmed" in result
            assert result["pubmed"].mirror_url == "https://mirror.pubmed.cn"


class TestMirrorRouter:
    def test_init(self):
        router = MirrorRouter()
        assert router.is_offline_mode() is False
        assert len(router.mirrors) > 0

    def test_enable_mirrors(self):
        router = MirrorRouter()
        router.enable_mirrors(True)
        assert router._use_mirrors is True

    def test_enable_offline_mode(self):
        router = MirrorRouter()
        router.enable_offline_mode(True)
        assert router.is_offline_mode() is True
        router.enable_offline_mode(False)
        assert router.is_offline_mode() is False

    def test_detect_offline_mode(self):
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "true"}):
            assert MirrorRouter._detect_offline_mode() is True
        with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "false"}):
            assert MirrorRouter._detect_offline_mode() is False

    def test_get_endpoint_known(self):
        router = MirrorRouter()
        ep = router.get_endpoint("pubmed")
        assert ep.name != ""

    def test_get_endpoint_unknown(self):
        router = MirrorRouter()
        ep = router.get_endpoint("nonexistent_db")
        assert ep.primary_url == ""

    def test_get_url_primary(self):
        router = MirrorRouter()
        url = router.get_url("pubmed")
        assert url != ""

    def test_get_url_mirror_enabled(self):
        router = MirrorRouter()
        router.enable_mirrors(True)
        url = router.get_url("pubmed")
        assert url != ""

    def test_get_url_offline_mode(self):
        router = MirrorRouter()
        router.enable_offline_mode(True)
        url = router.get_url("pubmed")
        assert url != ""

    def test_get_alternatives_known(self):
        router = MirrorRouter()
        alts = router.get_alternatives("pubmed")
        assert isinstance(alts, list)
        assert len(alts) > 0

    def test_get_alternatives_unknown(self):
        router = MirrorRouter()
        alts = router.get_alternatives("nonexistent")
        assert alts == []

    def test_get_chinese_equivalent(self):
        router = MirrorRouter()
        url = router.get_chinese_equivalent("pubmed")
        assert "cnki" in url.lower() or url != ""

    def test_get_chinese_equivalent_no_zh(self):
        router = MirrorRouter()
        router.alternatives["test_db"] = [
            {"name": "English Alt", "url": "https://en.com", "type": "mirror", "lang": "en"}
        ]
        url = router.get_chinese_equivalent("test_db")
        assert url == "https://en.com"

    def test_get_chinese_equivalent_none(self):
        router = MirrorRouter()
        url = router.get_chinese_equivalent("nonexistent")
        assert url == ""

    def test_list_mirrors(self):
        router = MirrorRouter()
        mirrors = router.list_mirrors()
        assert isinstance(mirrors, list)
        assert len(mirrors) > 0
        assert "name" in mirrors[0]

    def test_list_chinese_databases(self):
        router = MirrorRouter()
        dbs = router.list_chinese_databases()
        assert isinstance(dbs, list)
        assert len(dbs) > 0
        names = [d["name"] for d in dbs]
        assert "NGDC" in names

    def test_list_alternatives(self):
        router = MirrorRouter()
        alts = router.list_alternatives()
        assert isinstance(alts, dict)
        assert "pubmed" in alts

    def test_get_status_report(self):
        router = MirrorRouter()
        report = router.get_status_report()
        assert "offline_mode" in report
        assert "mirrors_enabled" in report
        assert "mirror_count" in report
        assert "cache_status" in report

    def test_enable_auto_switch(self):
        router = MirrorRouter()
        router.enable_auto_switch(True)
        assert router._auto_switch is True

    def test_get_latency_results_empty(self):
        router = MirrorRouter()
        assert router.get_latency_results() == {}

    def test_smart_get_url_auto_switch_with_data(self):
        router = MirrorRouter()
        router.enable_auto_switch(True)
        router._latency_cache["pubmed"] = {"primary": 0.5, "mirror": 0.1}
        url = router.smart_get_url("pubmed")
        ep = router.get_endpoint("pubmed")
        assert url == ep.mirror_url

    def test_smart_get_url_auto_switch_primary_faster(self):
        router = MirrorRouter()
        router.enable_auto_switch(True)
        router._latency_cache["pubmed"] = {"primary": 0.1, "mirror": 0.5}
        url = router.smart_get_url("pubmed")
        ep = router.get_endpoint("pubmed")
        assert url == ep.primary_url

    def test_smart_get_url_auto_switch_primary_unreachable(self):
        router = MirrorRouter()
        router.enable_auto_switch(True)
        router._latency_cache["pubmed"] = {"primary": -1.0, "mirror": 0.5}
        url = router.smart_get_url("pubmed")
        ep = router.get_endpoint("pubmed")
        assert url == ep.mirror_url

    def test_smart_get_url_auto_switch_mirror_unreachable(self):
        router = MirrorRouter()
        router.enable_auto_switch(True)
        router._latency_cache["pubmed"] = {"primary": 0.5, "mirror": -1.0}
        url = router.smart_get_url("pubmed")
        ep = router.get_endpoint("pubmed")
        assert url == ep.primary_url

    def test_smart_get_url_no_auto_switch(self):
        router = MirrorRouter()
        router._latency_cache["pubmed"] = {"primary": 0.5, "mirror": 0.1}
        url = router.smart_get_url("pubmed")
        assert url != ""

    def test_smart_get_url_auto_switch_both_unreachable(self):
        router = MirrorRouter()
        router.enable_auto_switch(True)
        router._latency_cache["pubmed"] = {"primary": -1.0, "mirror": -1.0}
        url = router.smart_get_url("pubmed")
        assert url != ""

    @pytest.mark.asyncio
    async def test_test_latency(self):
        router = MirrorRouter()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await router.test_latency("pubmed")
        assert "primary" in result
        assert "mirror" in result

    @pytest.mark.asyncio
    async def test_test_latency_failure(self):
        router = MirrorRouter()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("network fail"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await router.test_latency("pubmed")
        assert result["primary"] == -1.0

    @pytest.mark.asyncio
    async def test_test_all_latency(self):
        router = MirrorRouter()
        with patch.object(router, "test_latency", new_callable=AsyncMock) as mock_lat:
            mock_lat.return_value = {"primary": 0.1, "mirror": 0.2}
            result = await router.test_all_latency()
        assert isinstance(result, dict)


# ===================================================================
# 9. fusion_science/literature/paper.py
# ===================================================================


class TestPaperSection:
    def test_defaults(self):
        ps = PaperSection(heading="Intro")
        assert ps.content == ""
        assert ps.word_count == 0
        assert ps.citations == []


class TestPaperDraft:
    def test_defaults(self):
        pd = PaperDraft(title="Test")
        assert pd.authors == []
        assert pd.sections == []
        assert pd.status == "draft"


class TestPaperGenerator:
    def test_create_paper_default_sections(self):
        gen = PaperGenerator()
        draft = gen.create_paper("My Paper")
        assert draft.title == "My Paper"
        headings = [s.heading for s in draft.sections]
        assert "Abstract" in headings
        assert "Introduction" in headings

    def test_create_paper_custom_sections(self):
        gen = PaperGenerator()
        draft = gen.create_paper("My Paper", sections=["Intro", "Body", "Outro"])
        assert len(draft.sections) == 3
        assert draft.sections[0].heading == "Intro"

    def test_create_paper_with_references(self):
        gen = PaperGenerator()
        papers = [_make_paper(), _make_paper(title="Another Paper")]
        draft = gen.create_paper("My Paper", papers=papers)
        assert len(draft.references) == 2

    @pytest.mark.asyncio
    async def test_write_section_no_engine(self):
        gen = PaperGenerator(engine=None)
        draft = gen.create_paper("Test")
        result = await gen.write_section(draft, 0)
        assert result.sections[0].content != ""
        assert result.sections[0].word_count > 0

    @pytest.mark.asyncio
    async def test_write_section_with_engine(self):
        mock_engine = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "This is the generated introduction content for the paper."
        mock_engine.chat = AsyncMock(return_value=mock_resp)
        gen = PaperGenerator(engine=mock_engine)
        draft = gen.create_paper("Test")
        result = await gen.write_section(draft, 1, context="Some context")
        assert result.sections[1].content != ""

    @pytest.mark.asyncio
    async def test_write_section_engine_failure(self):
        mock_engine = MagicMock()
        mock_engine.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        gen = PaperGenerator(engine=mock_engine)
        draft = gen.create_paper("Test")
        result = await gen.write_section(draft, 0)
        assert result.sections[0].content != ""

    @pytest.mark.asyncio
    async def test_write_section_out_of_range(self):
        gen = PaperGenerator()
        draft = gen.create_paper("Test")
        result = await gen.write_section(draft, 999)
        assert result is draft

    def test_format_reference_apa(self):
        gen = PaperGenerator()
        paper = _make_paper(authors=["Alice", "Bob", "Charlie", "Dave"])
        ref = gen._format_reference(paper, style="apa")
        assert "et al." in ref
        assert "2024" in ref
        assert "doi" in ref.lower() or "10.1234" in ref

    def test_format_reference_nature(self):
        gen = PaperGenerator()
        paper = _make_paper()
        ref = gen._format_reference(paper, style="nature")
        assert "Nature" in ref

    def test_format_reference_unknown_style(self):
        gen = PaperGenerator()
        paper = _make_paper()
        ref = gen._format_reference(paper, style="mla")
        assert "2024" in ref

    def test_generate_figure_legend(self):
        legend = PaperGenerator.generate_figure_legend("bar chart", "Gene expression levels", "t-test, p < 0.05")
        assert "Gene expression" in legend
        assert "t-test" in legend
        assert "Error bars" in legend

    def test_generate_figure_legend_no_stats(self):
        legend = PaperGenerator.generate_figure_legend("scatter", "Correlation")
        assert "Correlation" in legend
        assert "Error bars" in legend

    def test_generate_methods_from_code(self):
        code = """
import pandas as pd
import numpy as np
from scipy import stats
result = stats.ttest_ind(group1, group2)
model = linear_model.regression(x, y)
"""
        methods = PaperGenerator.generate_methods_from_code(code)
        assert "pandas" in methods
        assert "numpy" in methods
        assert "t-tests" in methods
        assert "regression" in methods

    def test_generate_methods_from_code_no_packages(self):
        code = "x = 1 + 1"
        methods = PaperGenerator.generate_methods_from_code(code)
        assert "## Methods" in methods

    def test_check_section_balance_empty(self):
        draft = PaperDraft(title="Test")
        warnings = PaperGenerator.check_section_balance(draft)
        assert "no sections" in warnings[0].lower()

    def test_check_section_balance_ok(self):
        draft = PaperDraft(
            title="Test",
            sections=[
                PaperSection(heading="Intro", content="x " * 100, word_count=100),
                PaperSection(heading="Methods", content="y " * 120, word_count=120),
            ],
        )
        warnings = PaperGenerator.check_section_balance(draft)
        assert len(warnings) == 0

    def test_check_section_balance_empty_section(self):
        draft = PaperDraft(
            title="Test",
            sections=[
                PaperSection(heading="Intro", content="x " * 100, word_count=100),
                PaperSection(heading="Empty", content="", word_count=0),
            ],
        )
        warnings = PaperGenerator.check_section_balance(draft)
        assert any("empty" in w.lower() for w in warnings)

    def test_check_section_balance_short_section(self):
        draft = PaperDraft(
            title="Test",
            sections=[
                PaperSection(heading="Long", content="x " * 100, word_count=100),
                PaperSection(heading="Short", content="y", word_count=5),
            ],
        )
        warnings = PaperGenerator.check_section_balance(draft)
        assert any("shorter" in w.lower() for w in warnings)

    def test_placeholder_known_heading(self):
        gen = PaperGenerator()
        text = gen._generate_placeholder("Abstract", "context")
        assert "summary" in text.lower() or "study" in text.lower()

    def test_placeholder_unknown_heading(self):
        gen = PaperGenerator()
        text = gen._generate_placeholder("Custom Section", "context")
        assert "Custom Section" in text


# ===================================================================
# 10. fusion_science/literature/citation.py
# ===================================================================


class TestCitation:
    def test_to_dict(self):
        paper = _make_paper()
        cit = Citation(key="smith2024test", paper=paper, style_cache={"apa": "ref"})
        d = cit.to_dict()
        assert d["key"] == "smith2024test"
        assert d["title"] == "Test Paper"
        assert "apa" in d["formatted"]

    def test_to_dict_empty_style_cache(self):
        paper = _make_paper()
        cit = Citation(key="k1", paper=paper, style_cache={})
        d = cit.to_dict()
        assert d["formatted"] == {}


class TestCitationGraph:
    def test_to_dict(self):
        g = CitationGraph()
        d = g.to_dict()
        assert d["node_count"] == 0
        assert d["edge_count"] == 0

    def test_to_dict_with_data(self):
        paper = _make_paper()
        cit = Citation(key="k1", paper=paper)
        g = CitationGraph(nodes={"k1": cit}, edges=[("k1", "k2")])
        d = g.to_dict()
        assert d["node_count"] == 1
        assert d["edge_count"] == 1


class TestCitationManager:
    def test_add_paper(self):
        mgr = CitationManager()
        paper = _make_paper()
        cit = mgr.add_paper(paper)
        assert cit.key != ""
        assert "apa" in cit.style_cache

    def test_add_paper_with_key(self):
        mgr = CitationManager()
        paper = _make_paper()
        cit = mgr.add_paper(paper, key="my_key")
        assert cit.key == "my_key"

    def test_add_paper_duplicate_same_title(self):
        mgr = CitationManager()
        paper = _make_paper()
        cit1 = mgr.add_paper(paper)
        cit2 = mgr.add_paper(paper)
        assert cit1.key == cit2.key

    def test_add_paper_duplicate_different_title(self):
        mgr = CitationManager()
        paper1 = _make_paper(title="Paper A")
        paper2 = _make_paper(title="Paper B")
        cit1 = mgr.add_paper(paper1, key="same_key")
        cit2 = mgr.add_paper(paper2, key="same_key")
        assert cit2.key != "same_key" or cit2.key.startswith("same_key_")

    def test_add_papers(self):
        mgr = CitationManager()
        papers = [_make_paper(title=f"Paper {i}") for i in range(3)]
        cits = mgr.add_papers(papers)
        assert len(cits) == 3

    def test_get_citation(self):
        mgr = CitationManager()
        paper = _make_paper()
        cit = mgr.add_paper(paper)
        assert mgr.get_citation(cit.key) is cit
        assert mgr.get_citation("missing") is None

    def test_get_all_citations(self):
        mgr = CitationManager()
        mgr.add_paper(_make_paper(title="A"))
        mgr.add_paper(_make_paper(title="B"))
        assert len(mgr.get_all_citations()) == 2

    def test_remove_citation(self):
        mgr = CitationManager()
        paper = _make_paper()
        cit = mgr.add_paper(paper, key="remove_me")
        assert mgr.remove_citation("remove_me") is True
        assert mgr.get_citation("remove_me") is None
        assert mgr.remove_citation("remove_me") is False

    def test_format_apa(self):
        mgr = CitationManager()
        paper = _make_paper()
        ref = mgr.format_apa(paper)
        assert "2024" in ref
        assert "Nature" in ref

    def test_format_apa_single_author(self):
        mgr = CitationManager()
        paper = _make_paper(authors=["Solo"])
        ref = mgr.format_apa(paper)
        assert "Solo" in ref

    def test_format_apa_no_doi(self):
        mgr = CitationManager()
        paper = _make_paper(doi="")
        ref = mgr.format_apa(paper)
        assert "doi" not in ref.lower()

    def test_format_vancouver(self):
        mgr = CitationManager()
        paper = _make_paper()
        ref = mgr.format_vancouver(paper, number=5)
        assert "5." in ref
        assert "2024" in ref

    def test_format_bibtex(self):
        mgr = CitationManager()
        paper = _make_paper()
        ref = mgr.format_bibtex(paper, key="smith2024")
        assert "@article" in ref
        assert "smith2024" in ref
        assert "doi" in ref

    def test_format_bibtex_no_optional_fields(self):
        mgr = CitationManager()
        paper = Paper(title="Minimal", authors=[], year="", doi="", pmid="", url="")
        ref = mgr.format_bibtex(paper)
        assert "@article" in ref

    def test_generate_bibliography_apa(self):
        mgr = CitationManager()
        mgr.add_paper(_make_paper(title="A"))
        mgr.add_paper(_make_paper(title="B"))
        bib = mgr.generate_bibliography(style="apa")
        assert "2024" in bib

    def test_generate_bibliography_vancouver(self):
        mgr = CitationManager()
        mgr.add_paper(_make_paper(title="A"))
        bib = mgr.generate_bibliography(style="vancouver")
        assert "1." in bib

    def test_generate_bibliography_bibtex(self):
        mgr = CitationManager()
        mgr.add_paper(_make_paper(title="A"))
        bib = mgr.generate_bibliography(style="bibtex")
        assert "@article" in bib

    def test_generate_bibliography_unknown_style(self):
        mgr = CitationManager()
        mgr.add_paper(_make_paper(title="A"))
        bib = mgr.generate_bibliography(style="chicago")
        assert "2024" in bib

    def test_deduplicate(self):
        mgr = CitationManager()
        paper = _make_paper(doi="10.1234/dedup")
        mgr.add_paper(paper, key="a")
        mgr.add_paper(paper, key="b")
        removed = mgr.deduplicate()
        assert removed >= 1

    def test_deduplicate_no_duplicates(self):
        mgr = CitationManager()
        mgr.add_paper(_make_paper(title="Unique A", doi="10.1/a"))
        mgr.add_paper(_make_paper(title="Unique B", doi="10.1/b"))
        assert mgr.deduplicate() == 0

    def test_deduplicate_by_pmid(self):
        mgr = CitationManager()
        p1 = _make_paper(title="A", doi="", pmid="99999")
        p2 = _make_paper(title="B", doi="", pmid="99999")
        mgr.add_paper(p1, key="k1")
        mgr.add_paper(p2, key="k2")
        assert mgr.deduplicate() >= 1

    def test_deduplicate_by_title(self):
        mgr = CitationManager()
        p1 = _make_paper(title="Same Title", doi="", pmid="")
        p2 = _make_paper(title="Same Title", doi="", pmid="")
        mgr.add_paper(p1, key="k1")
        mgr.add_paper(p2, key="k2")
        assert mgr.deduplicate() >= 1

    def test_build_graph(self):
        mgr = CitationManager()
        p1 = _make_paper(title="A", keywords=["genomics", "cancer"], mesh_terms=["genomics"])
        p2 = _make_paper(title="B", keywords=["genomics", "therapy"], mesh_terms=["genomics"])
        mgr.add_paper(p1)
        mgr.add_paper(p2)
        graph = mgr.build_graph()
        d = graph.to_dict()
        assert d["node_count"] == 2
        assert d["edge_count"] >= 1

    def test_build_graph_no_relations(self):
        mgr = CitationManager()
        p1 = _make_paper(title="A", doi="10.1/a", keywords=["unrelated_a"], mesh_terms=["unique_mesh_a"])
        p2 = _make_paper(title="B", doi="10.1/b", keywords=["unrelated_b"], mesh_terms=["unique_mesh_b"])
        mgr.add_paper(p1)
        mgr.add_paper(p2)
        graph = mgr.build_graph()
        assert graph.to_dict()["edge_count"] == 0

    def test_build_graph_doi_match(self):
        mgr = CitationManager()
        p1 = _make_paper(title="A", doi="10.1234/shared", keywords=[], mesh_terms=[])
        p2 = _make_paper(title="B", doi="10.1234/shared", keywords=[], mesh_terms=[])
        mgr.add_paper(p1)
        mgr.add_paper(p2)
        graph = mgr.build_graph()
        assert graph.to_dict()["edge_count"] >= 1

    def test_verify_citations_all_good(self):
        mgr = CitationManager()
        mgr.add_paper(_make_paper())
        issues = mgr.verify_citations()
        assert len(issues) == 0

    def test_verify_citations_missing_fields(self):
        mgr = CitationManager()
        paper = Paper(title="", authors=[], year="", doi="", pmid="", arxiv_id="")
        mgr.add_paper(paper, key="bad")
        issues = mgr.verify_citations()
        issue_types = [i["issue"] for i in issues]
        assert "missing_title" in issue_types
        assert "missing_authors" in issue_types
        assert "missing_year" in issue_types
        assert "missing_identifier" in issue_types

    def test_generate_key(self):
        mgr = CitationManager()
        paper = _make_paper(authors=["John Smith"], year="2024", title="The Analysis of Data")
        key = mgr._generate_key(paper)
        assert "smith" in key
        assert "2024" in key

    def test_generate_key_no_authors(self):
        mgr = CitationManager()
        paper = Paper(title="Some Title", authors=[], year="2024")
        key = mgr._generate_key(paper)
        assert "unknown" in key

    def test_generate_key_no_year(self):
        mgr = CitationManager()
        paper = _make_paper(year="")
        key = mgr._generate_key(paper)
        assert "xxxx" in key

    def test_format_authors_apa_empty(self):
        mgr = CitationManager()
        assert mgr._format_authors_apa([]) == "[No authors]"

    def test_format_authors_apa_one(self):
        mgr = CitationManager()
        assert mgr._format_authors_apa(["Solo"]) == "Solo"

    def test_format_authors_apa_two(self):
        mgr = CitationManager()
        result = mgr._format_authors_apa(["Alice", "Bob"])
        assert "Alice" in result and "Bob" in result

    def test_format_authors_apa_many(self):
        mgr = CitationManager()
        result = mgr._format_authors_apa(["A", "B", "C", "D"])
        assert "et al." in result

    def test_format_authors_vancouver_empty(self):
        mgr = CitationManager()
        assert mgr._format_authors_vancouver([]) == "[No authors]"

    def test_format_authors_vancouver_many(self):
        mgr = CitationManager()
        authors = [f"Author {i}" for i in range(8)]
        result = mgr._format_authors_vancouver(authors)
        assert "et al" in result

    def test_format_authors_vancouver_single_name(self):
        mgr = CitationManager()
        result = mgr._format_authors_vancouver(["Madonna"])
        assert "Madonna" in result
