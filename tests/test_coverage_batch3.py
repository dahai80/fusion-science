from __future__ import annotations

import json
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from fusion_science.config import ScienceConfig, create_default_config, load_config, save_config
from fusion_science.core.agent import AgentResult, PipelineResult, ScienceAgent, SciencePipeline
from fusion_science.core.gateway import LLMGateway, LLMResponse, LLMResult
from fusion_science.core.pipeline import PipelineFactory
from fusion_science.core.tools import ToolRegistry, register_builtin_tools
from fusion_science.database.aggregator import AggregatedResult, DatabaseAggregator
from fusion_science.database.base import BaseConnector, ConnectorConfig, DatabaseResult
from fusion_science.literature.extractor import PICO, LiteratureExtractor, StructuredExtraction
from fusion_science.literature.reader import LiteratureReader
from fusion_science.literature.review import LiteratureReview, LiteratureReviewer, ReviewSection
from fusion_science.literature.search import LiteratureSearch, Paper, PRISMAFlow, SearchPreset, SearchResult
from fusion_science.literature.synthesizer import ConsensusAnalysis, LiteratureSynthesizer
from fusion_science.session import MemorySessionStore, SessionManager
from fusion_science.session.models import Artifact, ResearchSession

logger = logging.getLogger(__name__)


def _make_paper(**kwargs):
    defaults = dict(
        title="Test Paper",
        authors=["Author A"],
        abstract="Test abstract",
        journal="Nature",
        year="2024",
        doi="10.1234/test",
        pmid="12345",
        arxiv_id="",
        source="test",
        url="",
        keywords=["test"],
        mesh_terms=[],
        citations=0,
        pdf_url="",
        relevance_score=0.8,
        full_text="",
        sections={},
    )
    defaults.update(kwargs)
    return Paper(**defaults)


def _make_gateway():
    gw = MagicMock(spec=LLMGateway)
    gw.model = "qwen3.5-9b"
    gw.chat = AsyncMock(return_value=LLMResponse(content="test response", model="qwen3.5-9b"))
    gw.structured_output = AsyncMock(return_value=LLMResult(content="{}", parsed={}, model="qwen3.5-9b"))
    gw.chat_stream = AsyncMock()
    gw.health = AsyncMock(return_value=True)
    gw.set_model = MagicMock()
    gw.set_model_for_role = MagicMock()
    gw.get_model_for_role = MagicMock(return_value="qwen3.5-9b")
    gw.get_model_roles = MagicMock(return_value={"reasoning": "qwen3.5-9b"})
    gw.refresh_available_models = AsyncMock(return_value=[])
    gw.get_available_models = MagicMock(return_value=[])
    return gw


def _make_app_state(gateway=None, session_manager=None, tool_registry=None, config=None, recorder=None):
    state = MagicMock()
    state.gateway = gateway or _make_gateway()
    state.config = config or ScienceConfig()
    state.tool_registry = tool_registry
    state.recorder = recorder
    if session_manager is None:
        session_manager = SessionManager(store=MemorySessionStore())
    state.session_manager = session_manager
    return state


# ===================== SSE Tests =====================


class TestSSE:
    @pytest.mark.asyncio
    async def test_sse_generator_yields_tokens(self):
        from fusion_science.api.sse import _sse_generator

        async def token_gen():
            yield "hello"
            yield " world"

        chunks = []
        async for chunk in _sse_generator(token_gen()):
            chunks.append(chunk)
        assert len(chunks) == 3
        assert '"token": "hello"' in chunks[0]
        assert '"token": " world"' in chunks[1]
        assert '"done": true' in chunks[2]

    @pytest.mark.asyncio
    async def test_sse_generator_handles_error(self):
        from fusion_science.api.sse import _sse_generator

        async def failing_gen():
            yield "ok"
            raise ValueError("boom")

        chunks = []
        async for chunk in _sse_generator(failing_gen()):
            chunks.append(chunk)
        assert len(chunks) >= 2
        assert '"token": "ok"' in chunks[0]
        has_error = any('"error"' in c for c in chunks)
        assert has_error

    def test_sse_response_creation(self):
        from fusion_science.api.sse import sse_response

        async def token_gen():
            yield "test"

        resp = sse_response(token_gen())
        assert resp.media_type == "text/event-stream"
        assert resp.headers["cache-control"] == "no-cache"


# ===================== API Routes Tests =====================


class TestVisualizeExtRoutes:
    @pytest.fixture
    def client(self):
        from fusion_science.api.app import create_app

        app = create_app()
        app.state = _make_app_state()
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_molecule_smiles_success(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            instance = MockViz.return_value
            instance.from_smiles = AsyncMock(return_value={"html": "<svg>test</svg>", "smiles": "CCO"})
            resp = await client.post("/api/v1/viz/molecule/smiles", json={"smiles": "CCO"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_molecule_smiles_import_error_fallback(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            instance = MockViz.return_value
            instance.from_smiles = AsyncMock(side_effect=ImportError("no rdkit"))
            instance.from_smiles_2d_fallback = AsyncMock(return_value={"html": "fallback", "smiles": "CCO"})
            resp = await client.post("/api/v1/viz/molecule/smiles", json={"smiles": "CCO"})
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("fallback") is True or "html" in data

    @pytest.mark.asyncio
    async def test_molecule_smiles_generic_error(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            instance = MockViz.return_value
            instance.from_smiles = AsyncMock(side_effect=RuntimeError("fail"))
            resp = await client.post("/api/v1/viz/molecule/smiles", json={"smiles": "CCO"})
            assert resp.status_code == 200
            assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_molecule_pdb_success(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            instance = MockViz.return_value
            instance.from_pdb = AsyncMock(return_value={"html": "<svg>pdb</svg>"})
            resp = await client.post("/api/v1/viz/molecule/pdb", json={"pdb_id": "1ABC"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_molecule_pdb_error(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            instance = MockViz.return_value
            instance.from_pdb = AsyncMock(side_effect=Exception("pdb fail"))
            resp = await client.post("/api/v1/viz/molecule/pdb", json={"pdb_id": "1ABC"})
            assert resp.status_code == 200
            assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_protein_visualize(self, client):
        with patch("fusion_science.api.routes.visualize_ext.ProteinVisualizer") as MockViz:
            instance = MockViz.return_value
            instance.visualize = AsyncMock(return_value={"html": "<svg>protein</svg>"})
            resp = await client.post("/api/v1/viz/protein", json={"pdb_id": "1ABC"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_protein_visualize_error(self, client):
        with patch("fusion_science.api.routes.visualize_ext.ProteinVisualizer") as MockViz:
            instance = MockViz.return_value
            instance.visualize = AsyncMock(side_effect=Exception("viz fail"))
            resp = await client.post("/api/v1/viz/protein", json={"pdb_id": "1ABC"})
            assert resp.status_code == 200
            assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_protein_compare(self, client):
        with patch("fusion_science.api.routes.visualize_ext.ProteinVisualizer") as MockViz:
            instance = MockViz.return_value
            instance.compare_structures = AsyncMock(return_value={"html": "<svg>compare</svg>"})
            resp = await client.post("/api/v1/viz/protein/compare", json={"pdb_id_1": "1ABC", "pdb_id_2": "2DEF"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_protein_compare_error(self, client):
        with patch("fusion_science.api.routes.visualize_ext.ProteinVisualizer") as MockViz:
            instance = MockViz.return_value
            instance.compare_structures = AsyncMock(side_effect=Exception("cmp fail"))
            resp = await client.post("/api/v1/viz/protein/compare", json={"pdb_id_1": "1ABC", "pdb_id_2": "2DEF"})
            assert resp.status_code == 200
            assert "error" in resp.json()


class TestCitationsRoutes:
    @pytest.fixture
    def client(self):
        from fusion_science.api.app import create_app

        app = create_app()
        store = MemorySessionStore()
        session = ResearchSession(
            id="sess1",
            title="Test",
            artifacts=[
                Artifact(
                    id="a1",
                    type="citation",
                    content="",
                    metadata={
                        "title": "Cited Paper",
                        "authors": ["A"],
                        "year": "2024",
                        "journal": "J",
                        "doi": "10.1/x",
                        "pmid": "99",
                        "keywords": [],
                    },
                ),
            ],
        )
        store.save(session)
        sm = SessionManager(store=store)
        app.state = _make_app_state(session_manager=sm)
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_get_citation_graph(self, client):
        resp = await client.get("/api/v1/citations/graph", params={"session_id": "sess1"})
        assert resp.status_code == 200
        data = resp.json()
        assert "node_count" in data

    @pytest.mark.asyncio
    async def test_get_citation_graph_no_session(self, client):
        resp = await client.get("/api/v1/citations/graph", params={"session_id": "nonexist"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_add_citation(self, client):
        resp = await client.post(
            "/api/v1/citations/add",
            json={
                "title": "New Paper",
                "authors": ["B"],
                "year": "2023",
                "journal": "Science",
                "doi": "10.2/y",
                "pmid": "88",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "key" in data

    @pytest.mark.asyncio
    async def test_get_bibliography(self, client):
        resp = await client.get("/api/v1/citations/bibliography", params={"style": "apa", "session_id": "sess1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["style"] == "apa"
        assert "bibliography" in data


class TestComputeRoutes:
    @pytest.fixture
    def client_and_app(self):
        from fusion_science.api.app import create_app

        app = create_app()
        app.state = _make_app_state()
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        return client, app

    @pytest.fixture
    def client(self, client_and_app):
        return client_and_app[0]

    @pytest.mark.asyncio
    async def test_code_gen(self, client):
        with patch("fusion_science.api.routes.compute.CodeGenerator") as MockGen:
            instance = MockGen.return_value
            result = MagicMock(code="print(1)", language="python", confidence=0.9, packages=[])
            instance.generate = AsyncMock(return_value=result)
            resp = await client.post("/api/v1/compute/code-gen", json={"query": "hello", "language": "python"})
            assert resp.status_code == 200
            assert resp.json()["code"] == "print(1)"

    @pytest.mark.asyncio
    async def test_code_gen_error(self, client):
        with patch("fusion_science.api.routes.compute.CodeGenerator") as MockGen:
            instance = MockGen.return_value
            instance.generate = AsyncMock(side_effect=Exception("gen fail"))
            resp = await client.post("/api/v1/compute/code-gen", json={"query": "hello"})
            assert resp.status_code == 200
            assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_code_gen_batch(self, client):
        with patch("fusion_science.api.routes.compute.CodeGenerator") as MockGen:
            instance = MockGen.return_value
            r1 = MagicMock(code="c1", language="python", confidence=0.8)
            instance.generate_batch = AsyncMock(return_value=[r1])
            resp = await client.post("/api/v1/compute/code-gen/batch", json={"queries": ["q1"], "language": "python"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_jupyter_execute(self, client):
        with patch("fusion_science.api.routes.compute.JupyterKernelManager") as MockMgr:
            instance = MockMgr.return_value
            instance.start_kernel = AsyncMock(return_value=True)
            result = MagicMock(stdout="out", stderr="", outputs=[], success=True, execution_count=1)
            instance.execute = AsyncMock(return_value=result)
            instance.shutdown = AsyncMock()
            resp = await client.post("/api/v1/compute/jupyter/execute", json={"code": "1+1"})
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_jupyter_execute_kernel_start_fail(self, client):
        with patch("fusion_science.api.routes.compute.JupyterKernelManager") as MockMgr:
            instance = MockMgr.return_value
            instance.start_kernel = AsyncMock(return_value=False)
            resp = await client.post("/api/v1/compute/jupyter/execute", json={"code": "1+1"})
            assert resp.status_code == 200
            assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_jupyter_execute_exception(self, client):
        with patch("fusion_science.api.routes.compute.JupyterKernelManager") as MockMgr:
            instance = MockMgr.return_value
            instance.start_kernel = AsyncMock(return_value=True)
            instance.execute = AsyncMock(side_effect=Exception("exec error"))
            instance.shutdown = AsyncMock()
            resp = await client.post("/api/v1/compute/jupyter/execute", json={"code": "1+1"})
            assert resp.status_code == 200
            assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_list_jupyter_kernels(self, client):
        with patch("fusion_science.api.routes.compute.JupyterKernelManager") as MockMgr:
            k = MagicMock(name="python3", display_name="Python 3", language="python")
            MockMgr.list_available_kernels = MagicMock(return_value=[k])
            resp = await client.get("/api/v1/compute/jupyter/kernels")
            assert resp.status_code == 200
            assert len(resp.json()["kernels"]) == 1

    @pytest.mark.asyncio
    async def test_compliance_check(self, client):
        with patch("fusion_science.api.routes.compute.ComplianceChecker") as MockChk:
            instance = MockChk.return_value
            instance.check_report = MagicMock(return_value={"compliant": True})
            resp = await client.post("/api/v1/compute/compliance", json={"session_id": "", "usage_context": "personal"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_compliance_with_session(self, client_and_app):
        client, app = client_and_app
        store = MemorySessionStore()
        store.save(ResearchSession(id="s1", title="T"))
        app.state.session_manager = SessionManager(store=store)
        app.state.recorder = MagicMock()
        app.state.recorder.get_traces = MagicMock(return_value=[{"op": "test"}])
        with patch("fusion_science.api.routes.compute.ComplianceChecker") as MockChk:
            instance = MockChk.return_value
            instance.check_report = MagicMock(return_value={"compliant": True})
            resp = await client.post("/api/v1/compute/compliance", json={"session_id": "s1"})
            assert resp.status_code == 200


class TestModelsRoutes:
    @pytest.fixture
    def client_and_app(self):
        from fusion_science.api.app import create_app

        app = create_app()
        gw = _make_gateway()
        app.state = _make_app_state(gateway=gw)
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        return client, app

    @pytest.fixture
    def client(self, client_and_app):
        return client_and_app[0]

    @pytest.mark.asyncio
    async def test_list_models(self, client):
        resp = await client.get("/api/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data

    @pytest.mark.asyncio
    async def test_list_models_no_gateway(self, client_and_app):
        client, app = client_and_app
        app.state.gateway = None
        resp = await client.get("/api/v1/models")
        assert resp.status_code == 200
        assert resp.json()["error"] == "gateway not initialized"

    @pytest.mark.asyncio
    async def test_list_models_error(self, client_and_app):
        client, app = client_and_app
        app.state.gateway.refresh_available_models = AsyncMock(side_effect=Exception("fail"))
        resp = await client.get("/api/v1/models")
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_set_current_model(self, client_and_app):
        client, app = client_and_app
        resp = await client.put("/api/v1/models/current", json={"model": "new-model"})
        assert resp.status_code == 200
        app.state.gateway.set_model.assert_called_with("new-model")

    @pytest.mark.asyncio
    async def test_set_current_model_no_gateway(self, client_and_app):
        client, app = client_and_app
        app.state.gateway = None
        resp = await client.put("/api/v1/models/current", json={"model": "new-model"})
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_get_model_roles(self, client):
        resp = await client.get("/api/v1/models/roles")
        assert resp.status_code == 200
        assert "roles" in resp.json()

    @pytest.mark.asyncio
    async def test_set_model_role(self, client_and_app):
        client, app = client_and_app
        resp = await client.put("/api/v1/models/roles", json={"role": "reasoning", "model": "big-model"})
        assert resp.status_code == 200
        app.state.gateway.set_model_for_role.assert_called_with("reasoning", "big-model")


class TestPipelinesRoutes:
    @pytest.fixture
    def client_and_app(self):
        from fusion_science.api.app import create_app

        app = create_app()
        app.state = _make_app_state()
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        return client, app

    @pytest.fixture
    def client(self, client_and_app):
        return client_and_app[0]

    @pytest.mark.asyncio
    async def test_list_pipelines(self, client):
        resp = await client.get("/api/v1/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert len(data["pipelines"]) >= 3

    @pytest.mark.asyncio
    async def test_run_pipeline_not_found(self, client):
        resp = await client.post("/api/v1/pipelines/nonexist/run", json={"query": "test"})
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_run_pipeline_no_engine(self, client_and_app):
        client, app = client_and_app
        app.state.gateway = None
        resp = await client.post("/api/v1/pipelines/literature_review/run", json={"query": "test"})
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_run_pipeline_success(self, client_and_app):
        client, app = client_and_app
        gw = app.state.gateway
        gw.chat = AsyncMock(return_value=LLMResponse(content="result", model="qwen3.5-9b"))
        resp = await client.post("/api/v1/pipelines/literature_review/run", json={"query": "cancer"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline"] == "literature_review"


# ===================== MCP Server Tests =====================


class TestMCPServer:
    @pytest.fixture
    def client_and_app(self):
        from fastapi import FastAPI

        from fusion_science.mcp_server import router

        app = FastAPI()
        app.include_router(router, prefix="/mcp")
        registry = ToolRegistry()
        registry.register(
            "test_tool", "A test tool", {"type": "object", "properties": {}}, AsyncMock(return_value={"ok": True})
        )
        app.state = _make_app_state(tool_registry=registry)
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        return client, app

    @pytest.fixture
    def client(self, client_and_app):
        return client_and_app[0]

    @pytest.mark.asyncio
    async def test_initialize(self, client):
        resp = await client.post(
            "/mcp/",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {},
                }
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["protocolVersion"] == "2024-11-05"

    @pytest.mark.asyncio
    async def test_tools_list(self, client):
        resp = await client.post(
            "/mcp/",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["result"]["tools"]) >= 1

    @pytest.mark.asyncio
    async def test_tools_list_no_registry(self, client_and_app):
        client, app = client_and_app
        app.state.tool_registry = None
        resp = await client.post(
            "/mcp/",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/list",
                    "params": {},
                }
            ),
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["tools"] == []

    @pytest.mark.asyncio
    async def test_tools_call(self, client):
        resp = await client.post(
            "/mcp/",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "test_tool", "arguments": {}},
                }
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data["result"]

    @pytest.mark.asyncio
    async def test_tools_call_missing_name(self, client):
        resp = await client.post(
            "/mcp/",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"arguments": {}},
                }
            ),
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_tools_call_no_registry(self, client_and_app):
        client, app = client_and_app
        app.state.tool_registry = None
        resp = await client.post(
            "/mcp/",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {"name": "test_tool", "arguments": {}},
                }
            ),
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_method_not_found(self, client):
        resp = await client.post(
            "/mcp/",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "nonexistent",
                    "params": {},
                }
            ),
        )
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_parse_error(self, client):
        resp = await client.post("/mcp/", content="not json{{{")
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_missing_method(self, client):
        resp = await client.post("/mcp/", content=json.dumps({"jsonrpc": "2.0", "id": 8}))
        assert resp.status_code == 200
        assert resp.json()["error"]["code"] == -32600


# ===================== App Tests =====================


class TestApp:
    def test_create_app(self):
        from fusion_science.api.app import create_app

        app = create_app()
        assert app.title == "Fusion-Science API"

    def test_create_app_with_config(self):
        from fusion_science.api.app import create_app

        config = ScienceConfig(model_name="test-model")
        app = create_app(config=config)
        assert app.state.config.model_name == "test-model"

    @pytest.mark.asyncio
    async def test_lifespan_startup_shutdown(self):
        from fusion_science.api.app import create_app, lifespan

        app = create_app()
        app.state.config = ScienceConfig()
        async with lifespan(app):
            assert hasattr(app.state, "gateway")
            assert hasattr(app.state, "session_manager")
            assert hasattr(app.state, "recorder")
        assert app.state.gateway is not None

    @pytest.mark.asyncio
    async def test_lifespan_with_model_roles(self):
        from fusion_science.api.app import create_app, lifespan

        config = ScienceConfig(model_reasoning="r-model", model_summarization="s-model", model_code="c-model")
        app = create_app()
        app.state.config = config
        async with lifespan(app):
            assert app.state.gateway is not None

    def test_cors_middleware(self):
        from fusion_science.api.app import create_app

        app = create_app()
        has_cors = any("CORSMiddleware" in str(m) for m in app.user_middleware)
        assert has_cors

    @pytest.mark.asyncio
    async def test_audit_handler(self):
        from fusion_science.api.app import _audit_handler
        from fusion_science.audit.tracker import TraceRecorder

        recorder = TraceRecorder()
        recorder.start_session(metadata={"test": True})
        _audit_handler._recorder = recorder
        event = MagicMock()
        event.type = "db_query"
        event.source = "test"
        event.data = {"session_id": "s1"}
        await _audit_handler(event)
        _audit_handler._recorder = None


# ===================== Gateway Tests =====================


class TestLLMGateway:
    @pytest.fixture
    def gateway(self):
        return LLMGateway(model="test-model", base_url="http://localhost:11434/v1")

    def test_set_model(self, gateway):
        gateway.set_model("new-model")
        assert gateway.model == "new-model"

    def test_set_model_for_role(self, gateway):
        gateway.set_model_for_role("reasoning", "r-model")
        assert gateway.get_model_for_role("reasoning") == "r-model"

    def test_get_model_roles(self, gateway):
        roles = gateway.get_model_roles()
        assert "reasoning" in roles

    def test_get_avg_response_time_empty(self, gateway):
        assert gateway.get_avg_response_time() == 0.0

    def test_build_science_prompt(self, gateway):
        msgs = gateway.build_science_prompt("analyze data", "context info")
        assert len(msgs) == 2
        assert "context info" in msgs[1]["content"]

    def test_build_science_prompt_no_context(self, gateway):
        msgs = gateway.build_science_prompt("analyze data")
        assert len(msgs) == 2

    @pytest.mark.asyncio
    async def test_chat_success(self, gateway):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "hello", "tool_calls": []}, "finish_reason": "stop"}],
            "usage": {"tokens": 5},
            "model": "test-model",
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(gateway, "_get_client", new_callable=AsyncMock) as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_resp)
            mock_client.return_value = client
            result = await gateway.chat([{"role": "user", "content": "hi"}])
            assert result.content == "hello"

    @pytest.mark.asyncio
    async def test_chat_http_error(self, gateway):
        with patch.object(gateway, "_get_client", new_callable=AsyncMock) as mock_client:
            client = AsyncMock()
            resp = MagicMock()
            resp.status_code = 500
            resp.text = "server error"
            error = httpx.HTTPStatusError("err", request=MagicMock(), response=resp)
            client.post = AsyncMock(side_effect=error)
            mock_client.return_value = client
            result = await gateway.chat([{"role": "user", "content": "hi"}])
            assert result.error == "HTTP 500"

    @pytest.mark.asyncio
    async def test_chat_generic_error(self, gateway):
        with patch.object(gateway, "_get_client", new_callable=AsyncMock) as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(side_effect=ConnectionError("no conn"))
            mock_client.return_value = client
            result = await gateway.chat([{"role": "user", "content": "hi"}])
            assert "no conn" in result.error

    @pytest.mark.asyncio
    async def test_chat_stream(self, gateway):
        class FakeStreamResp:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"hi"}}]}'
                yield "data: [DONE]"

        with patch.object(gateway, "_get_client", new_callable=AsyncMock) as mock_client:
            client = MagicMock()
            client.stream = MagicMock(return_value=FakeStreamResp())
            mock_client.return_value = client
            tokens = []
            async for t in gateway.chat_stream([{"role": "user", "content": "hi"}]):
                tokens.append(t)
            assert "hi" in tokens

    @pytest.mark.asyncio
    async def test_chat_stream_error(self, gateway):
        with patch.object(gateway, "_get_client", new_callable=AsyncMock) as mock_client:
            client = AsyncMock()
            client.stream = MagicMock(side_effect=Exception("stream err"))
            mock_client.return_value = client
            tokens = []
            async for t in gateway.chat_stream([{"role": "user", "content": "hi"}]):
                tokens.append(t)
            assert any("stream error" in t for t in tokens)

    @pytest.mark.asyncio
    async def test_structured_output_success(self, gateway):
        with patch.object(gateway, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content='{"key": "val"}', model="test-model")
            result = await gateway.structured_output([{"role": "user", "content": "extract"}], {"type": "object"})
            assert result.parsed == {"key": "val"}

    @pytest.mark.asyncio
    async def test_structured_output_with_code_fence(self, gateway):
        with patch.object(gateway, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content='```json\n{"key": "val"}\n```', model="test-model")
            result = await gateway.structured_output([{"role": "user", "content": "extract"}], {"type": "object"})
            assert result.parsed == {"key": "val"}

    @pytest.mark.asyncio
    async def test_structured_output_error(self, gateway):
        with patch.object(gateway, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="", error="LLM failed", model="test-model")
            result = await gateway.structured_output([{"role": "user", "content": "extract"}], {"type": "object"})
            assert result.error == "LLM failed"

    @pytest.mark.asyncio
    async def test_structured_output_json_parse_fail(self, gateway):
        with patch.object(gateway, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="not json at all", model="test-model")
            result = await gateway.structured_output([{"role": "user", "content": "extract"}], {"type": "object"})
            assert "json_decode_error" in result.error

    @pytest.mark.asyncio
    async def test_health_success(self, gateway):
        with patch.object(gateway, "_get_client", new_callable=AsyncMock) as mock_client:
            client = AsyncMock()
            resp = MagicMock()
            resp.status_code = 200
            client.get = AsyncMock(return_value=resp)
            mock_client.return_value = client
            assert await gateway.health() is True

    @pytest.mark.asyncio
    async def test_health_failure(self, gateway):
        with patch.object(gateway, "_get_client", new_callable=AsyncMock) as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=Exception("no conn"))
            mock_client.return_value = client
            assert await gateway.health() is False

    @pytest.mark.asyncio
    async def test_refresh_available_models(self, gateway):
        with patch.object(gateway, "_get_client", new_callable=AsyncMock) as mock_client:
            client = AsyncMock()
            resp = MagicMock()
            resp.json.return_value = {"data": [{"id": "model-a"}]}
            resp.raise_for_status = MagicMock()
            client.get = AsyncMock(return_value=resp)
            mock_client.return_value = client
            models = await gateway.refresh_available_models()
            assert len(models) == 1

    @pytest.mark.asyncio
    async def test_refresh_available_models_error(self, gateway):
        with patch.object(gateway, "_get_client", new_callable=AsyncMock) as mock_client:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=Exception("fail"))
            mock_client.return_value = client
            models = await gateway.refresh_available_models()
            assert models == []

    @pytest.mark.asyncio
    async def test_close(self, gateway):
        gateway._client = MagicMock()
        gateway._client.is_closed = False
        gateway._client.aclose = AsyncMock()
        gateway._connection_monitor = MagicMock()
        gateway._connection_monitor.stop_monitor = AsyncMock()
        await gateway.close()
        gateway._client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager(self, gateway):
        gateway.close = AsyncMock()
        async with gateway as g:
            assert g is gateway
        gateway.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_sequence(self, gateway):
        with patch.object(gateway, "chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = LLMResponse(content="analysis result", model="test-model")
            result = await gateway.analyze_sequence("ATCG", "general")
            assert result == "analysis result"

    def test_connection_monitor_start(self, gateway):
        gateway.start_connection_monitor(interval=10.0)
        assert gateway._connection_monitor is not None

    def test_connection_monitor_already_started(self, gateway):
        gateway.start_connection_monitor(interval=10.0)
        first = gateway._connection_monitor
        gateway.start_connection_monitor(interval=20.0)
        assert gateway._connection_monitor is first

    def test_get_connection_stats(self, gateway):
        stats = gateway.get_connection_stats()
        assert stats is not None


# ===================== Agent Tests =====================


class TestScienceAgent:
    @pytest.mark.asyncio
    async def test_run_simple_response(self):
        engine = _make_gateway()
        engine.chat = AsyncMock(return_value=LLMResponse(content="final answer", model="test"))
        agent = ScienceAgent(name="test_agent", engine=engine)
        result = await agent.run("hello")
        assert result.output == "final answer"
        assert result.agent_name == "test_agent"

    @pytest.mark.asyncio
    async def test_run_with_tool_call(self):
        engine = _make_gateway()
        tc = {"id": "tc1", "function": {"name": "search", "arguments": '{"query": "test"}'}}
        engine.chat = AsyncMock(
            side_effect=[
                LLMResponse(content="", tool_calls=[tc], model="test"),
                LLMResponse(content="final after tool", model="test"),
            ]
        )
        registry = ToolRegistry()
        registry.register("search", "Search", {"type": "object"}, AsyncMock(return_value={"results": []}))
        agent = ScienceAgent(name="tool_agent", engine=engine, tool_registry=registry)
        result = await agent.run("search for test")
        assert result.output == "final after tool"

    @pytest.mark.asyncio
    async def test_run_tool_not_in_registry(self):
        engine = _make_gateway()
        tc = {"id": "tc2", "function": {"name": "unknown_tool", "arguments": "{}"}}
        engine.chat = AsyncMock(
            side_effect=[
                LLMResponse(content="", tool_calls=[tc], model="test"),
                LLMResponse(content="no tool result", model="test"),
            ]
        )
        agent = ScienceAgent(name="agent", engine=engine)
        result = await agent.run("use unknown tool")
        assert result.output == "no tool result"

    @pytest.mark.asyncio
    async def test_run_max_iterations(self):
        engine = _make_gateway()
        tc = {"id": "tc3", "function": {"name": "loop", "arguments": "{}"}}
        engine.chat = AsyncMock(return_value=LLMResponse(content="", tool_calls=[tc], model="test"))
        agent = ScienceAgent(name="loop_agent", engine=engine)
        result = await agent.run("loop forever", max_iterations=2)
        assert "Max iterations" in result.error

    @pytest.mark.asyncio
    async def test_run_tool_bad_json(self):
        engine = _make_gateway()
        tc = {"id": "tc4", "function": {"name": "bad", "arguments": "not json{{{"}}
        engine.chat = AsyncMock(
            side_effect=[
                LLMResponse(content="", tool_calls=[tc], model="test"),
                LLMResponse(content="recovered", model="test"),
            ]
        )
        agent = ScienceAgent(name="bad_json_agent", engine=engine)
        result = await agent.run("bad json tool")
        assert result.output == "recovered"


class TestSciencePipeline:
    @pytest.mark.asyncio
    async def test_sequential(self):
        engine = _make_gateway()
        engine.chat = AsyncMock(return_value=LLMResponse(content="step result", model="test"))
        agent1 = ScienceAgent(name="a1", engine=engine)
        agent2 = ScienceAgent(name="a2", engine=engine)
        pipeline = SciencePipeline(engine=engine)
        pipeline.register_agent(agent1)
        pipeline.register_agent(agent2)
        result = await pipeline.sequential(["a1", "a2"], "test task")
        assert len(result.agent_results) == 2
        assert result.summary != ""

    @pytest.mark.asyncio
    async def test_sequential_missing_agent(self):
        engine = _make_gateway()
        pipeline = SciencePipeline(engine=engine)
        result = await pipeline.sequential(["missing"], "task")
        assert "not found" in result.agent_results[0].error

    @pytest.mark.asyncio
    async def test_sequential_agent_exception(self):
        engine = _make_gateway()
        engine.chat = AsyncMock(side_effect=RuntimeError("boom"))
        agent1 = ScienceAgent(name="a1", engine=engine)
        pipeline = SciencePipeline(engine=engine)
        pipeline.register_agent(agent1)
        result = await pipeline.sequential(["a1"], "task")
        assert result.agent_results[0].error != ""

    @pytest.mark.asyncio
    async def test_parallel(self):
        engine = _make_gateway()
        engine.chat = AsyncMock(return_value=LLMResponse(content="parallel result", model="test"))
        agent1 = ScienceAgent(name="a1", engine=engine)
        agent2 = ScienceAgent(name="a2", engine=engine)
        pipeline = SciencePipeline(engine=engine)
        pipeline.register_agent(agent1)
        pipeline.register_agent(agent2)
        result = await pipeline.parallel(["a1", "a2"], "task")
        assert len(result.agent_results) == 2

    @pytest.mark.asyncio
    async def test_master_worker(self):
        engine = _make_gateway()
        decompose_resp = LLMResponse(content='["sub1", "sub2"]', model="test")
        summary_resp = LLMResponse(content="final summary", model="test")
        worker_resp = LLMResponse(content="worker result", model="test")
        engine.chat = AsyncMock(side_effect=[decompose_resp, worker_resp, worker_resp, summary_resp])
        master = ScienceAgent(name="master", engine=engine)
        w1 = ScienceAgent(name="w1", engine=engine)
        w2 = ScienceAgent(name="w2", engine=engine)
        pipeline = SciencePipeline(engine=engine)
        pipeline.register_agent(master)
        pipeline.register_agent(w1)
        pipeline.register_agent(w2)
        result = await pipeline.master_worker("master", ["w1", "w2"], "big task")
        assert result.summary == "final summary"

    @pytest.mark.asyncio
    async def test_master_worker_no_master(self):
        engine = _make_gateway()
        pipeline = SciencePipeline(engine=engine)
        result = await pipeline.master_worker("missing", ["w1"], "task")
        assert "not found" in result.agent_results[0].error

    def test_extract_sub_tasks_json(self):
        engine = _make_gateway()
        pipeline = SciencePipeline(engine=engine)
        tasks = pipeline._extract_sub_tasks('["task1", "task2", "task3"]', 2)
        assert len(tasks) == 2
        assert tasks[0] == "task1"

    def test_extract_sub_tasks_text(self):
        engine = _make_gateway()
        pipeline = SciencePipeline(engine=engine)
        tasks = pipeline._extract_sub_tasks("line1\nline2\nline3", 2)
        assert len(tasks) == 2

    def test_generate_summary(self):
        engine = _make_gateway()
        pipeline = SciencePipeline(engine=engine)
        result = PipelineResult(
            task="test",
            agent_results=[
                AgentResult(agent_name="a1", output="ok", duration=1.0),
                AgentResult(agent_name="a2", output="", duration=2.0, error="fail"),
            ],
        )
        summary = pipeline._generate_summary(result)
        assert "ok a1" in summary
        assert "FAIL a2" in summary


# ===================== Tools Tests =====================


class TestToolRegistry:
    def test_register_and_list(self):
        reg = ToolRegistry()
        reg.register("tool1", "First tool", {"type": "object", "properties": {}})
        assert reg.has_tool("tool1")
        assert "tool1" in reg.list_tools()

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register("tool1", "First tool", {"type": "object", "properties": {}})
        reg.unregister("tool1")
        assert not reg.has_tool("tool1")

    def test_unregister_nonexistent(self):
        reg = ToolRegistry()
        reg.unregister("nope")

    @pytest.mark.asyncio
    async def test_execute(self):
        handler = AsyncMock(return_value={"result": 42})
        reg = ToolRegistry()
        reg.register("calc", "Calculator", {"type": "object"}, handler)
        result = await reg.execute("calc", {"x": 1})
        assert result == {"result": 42}

    @pytest.mark.asyncio
    async def test_execute_not_found(self):
        reg = ToolRegistry()
        result = await reg.execute("missing", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_execute_no_handler(self):
        reg = ToolRegistry()
        reg.register("no_handler", "No handler", {"type": "object"}, handler=None)
        result = await reg.execute("no_handler", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_execute_handler_error(self):
        handler = AsyncMock(side_effect=ValueError("boom"))
        reg = ToolRegistry()
        reg.register("bad", "Bad", {"type": "object"}, handler)
        result = await reg.execute("bad", {})
        assert "error" in result

    def test_get_openai_tools(self):
        reg = ToolRegistry()
        reg.register("t1", "Tool 1", {"type": "object", "properties": {"x": {"type": "string"}}})
        tools = reg.get_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "t1"

    def test_get_mcp_tools(self):
        reg = ToolRegistry()
        reg.register("t1", "Tool 1", {"type": "object"}, mcp_exposed=True)
        reg.register("t2", "Tool 2", {"type": "object"}, mcp_exposed=False)
        tools = reg.get_mcp_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "t1"

    def test_get_tool(self):
        reg = ToolRegistry()
        reg.register("t1", "Tool 1", {"type": "object"})
        tool = reg.get_tool("t1")
        assert tool is not None
        assert tool.name == "t1"

    def test_get_tool_none(self):
        reg = ToolRegistry()
        assert reg.get_tool("nope") is None

    def test_register_builtin_tools(self):
        reg = ToolRegistry()
        register_builtin_tools(reg)
        assert reg.has_tool("search_literature")
        assert reg.has_tool("search_database")
        assert reg.has_tool("execute_python")
        assert reg.has_tool("generate_chart")
        assert reg.has_tool("fetch_paper")


# ===================== Pipeline Factory Tests =====================


class TestPipelineFactory:
    def test_list_templates(self):
        templates = PipelineFactory.list_templates()
        assert len(templates) >= 3
        names = [t["name"] for t in templates]
        assert "literature_review" in names
        assert "bioinformatics_analysis" in names
        assert "molecular_analysis" in names

    def test_create_pipeline(self):
        gw = _make_gateway()
        factory = PipelineFactory(engine=gw)
        pipeline = factory.create_pipeline("literature_review")
        assert "literature_search" in pipeline.agents

    def test_create_pipeline_unknown(self):
        gw = _make_gateway()
        factory = PipelineFactory(engine=gw)
        with pytest.raises(ValueError, match="Unknown template"):
            factory.create_pipeline("nonexistent")

    def test_create_custom_pipeline(self):
        gw = _make_gateway()
        factory = PipelineFactory(engine=gw)
        agent = ScienceAgent(name="custom", engine=gw)
        pipeline = factory.create_custom_pipeline([agent])
        assert "custom" in pipeline.agents

    def test_load_tools_with_registry(self):
        gw = _make_gateway()
        registry = ToolRegistry()
        registry.register("search_literature", "Search lit", {"type": "object"})
        factory = PipelineFactory(engine=gw, tool_registry=registry)
        tools = factory._load_tools(["search_literature", "nonexistent_in_registry"])
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "search_literature"

    def test_load_tools_fallback(self):
        gw = _make_gateway()
        factory = PipelineFactory(engine=gw, tool_registry=None)
        tools = factory._load_tools(["search_literature", "execute_python"])
        assert len(tools) == 2

    def test_load_tools_unknown_fallback(self):
        gw = _make_gateway()
        registry = ToolRegistry()
        factory = PipelineFactory(engine=gw, tool_registry=registry)
        tools = factory._load_tools(["search_literature"])
        assert len(tools) == 1


# ===================== Config Tests =====================


class TestConfig:
    def test_default_config(self):
        config = ScienceConfig()
        assert config.model_name == "qwen3.5-9b"
        assert config.api_port == 11464
        assert config.engine_base_url == "http://localhost:11432/v1"

    def test_load_config_defaults(self):
        with patch.dict(os.environ, {}, clear=True), patch("fusion_science.config._resolve_from_mlx"):
            config = load_config(path="/nonexistent/path.yml")
            assert config.model_name == "qwen3.5-9b"

    def test_load_config_env_overrides(self, tmp_path):
        env = {
            "FUSION_SCIENCE_MODEL_NAME": "custom-model",
            "FUSION_SCIENCE_API_PORT": "9000",
            "FUSION_SCIENCE_USE_MIRRORS": "true",
            "FUSION_OFFLINE_MODE": "true",
            "FUSION_SCI_PUBMED_MIRROR": "https://mirror.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config(path="/nonexistent")
            assert config.model_name == "custom-model"
            assert config.api_port == 9000
            assert config.use_mirrors is True
            assert config.offline_mode is True
            assert config.pubmed_mirror == "https://mirror.example.com"

    def test_load_config_yaml(self, tmp_path):
        yaml_path = tmp_path / "config.yml"
        yaml_path.write_text("model_name: yaml-model\napi_port: 7777\n")
        config = load_config(path=str(yaml_path))
        assert config.model_name == "yaml-model"
        assert config.api_port == 7777

    def test_load_config_json(self, tmp_path):
        json_path = tmp_path / "config.json"
        json_path.write_text('{"model_name": "json-model", "api_port": 8888}')
        config = load_config(path=str(json_path))
        assert config.model_name == "json-model"
        assert config.api_port == 8888

    def test_save_config_json(self, tmp_path):
        config = ScienceConfig(model_name="save-test")
        path = str(tmp_path / "out.json")
        save_config(config, path)
        import json

        with open(path) as f:
            data = json.load(f)
        assert data["model_name"] == "save-test"

    def test_save_config_yaml(self, tmp_path):
        config = ScienceConfig(model_name="yaml-save")
        path = str(tmp_path / "out.yml")
        save_config(config, path)
        with open(path) as f:
            content = f.read()
        assert "yaml-save" in content

    def test_create_default_config(self, tmp_path):
        path = str(tmp_path / "default.yml")
        result = create_default_config(path=path)
        assert os.path.exists(result)

    def test_load_config_bad_file(self, tmp_path):
        bad_path = tmp_path / "bad.yml"
        bad_path.write_text("{{{{invalid yaml")
        with patch("fusion_science.config._resolve_from_mlx"):
            config = load_config(path=str(bad_path))
            assert config.model_name == "qwen3.5-9b"

    def test_load_config_float_env(self):
        with patch.dict(os.environ, {"FUSION_SCIENCE_ENGINE_TEMPERATURE": "0.7"}, clear=False):
            config = load_config(path="/nonexistent")
            assert config.engine_temperature == 0.7


# ===================== Literature Search Tests =====================


class TestLiteratureSearch:
    @pytest.mark.asyncio
    async def test_search_with_mocked_sources(self):
        ls = LiteratureSearch()
        ls._search_pubmed = AsyncMock(
            return_value=SearchResult(
                query="test",
                papers=[_make_paper(title="Pub Paper", source="PubMed")],
                total_count=1,
            )
        )
        ls._search_arxiv = AsyncMock(
            return_value=SearchResult(
                query="test",
                papers=[_make_paper(title="ArXiv Paper", source="arXiv")],
                total_count=1,
            )
        )
        result = await ls.search("test", sources=["pubmed", "arxiv"])
        assert result.total_count >= 1

    @pytest.mark.asyncio
    async def test_search_preset(self):
        ls = LiteratureSearch()
        ls._search_pubmed = AsyncMock(return_value=SearchResult(query="test", papers=[]))
        ls._search_arxiv = AsyncMock(return_value=SearchResult(query="test", papers=[]))
        result = await ls.search_preset("test", preset=SearchPreset.QUICK)
        assert result.preset == SearchPreset.QUICK

    @pytest.mark.asyncio
    async def test_search_with_registry(self):
        registry = ToolRegistry()
        registry.register(
            "search_database",
            "Search DB",
            {"type": "object"},
            AsyncMock(
                return_value={
                    "items": [{"title": "DB Paper", "name": "DB Paper", "abstract": "a"}],
                    "source": "uniprot",
                }
            ),
        )
        ls = LiteratureSearch(tool_registry=registry)
        ls._search_pubmed = AsyncMock(return_value=SearchResult(query="test", papers=[]))
        ls._search_arxiv = AsyncMock(return_value=SearchResult(query="test", papers=[]))
        result = await ls.search("test", sources=["pubmed", "arxiv", "uniprot"])
        assert "uniprot" in result.sources_used

    @pytest.mark.asyncio
    async def test_search_via_registry_error(self):
        registry = ToolRegistry()
        registry.register("search_database", "Search DB", {"type": "object"}, AsyncMock(return_value={"error": "fail"}))
        ls = LiteratureSearch(tool_registry=registry)
        result = await ls._search_via_registry("uniprot", "test", 10)
        assert result.error == "fail"

    @pytest.mark.asyncio
    async def test_search_source_exception(self):
        ls = LiteratureSearch()
        ls._search_pubmed = AsyncMock(side_effect=Exception("pubmed down"))
        ls._search_arxiv = AsyncMock(return_value=SearchResult(query="test", papers=[]))
        result = await ls.search("test", sources=["pubmed", "arxiv"])
        assert "arxiv" in result.sources_used

    def test_deduplicate(self):
        ls = LiteratureSearch()
        p1 = _make_paper(doi="10.1/a", title="Same")
        p2 = _make_paper(doi="10.1/a", title="Same")
        p3 = _make_paper(doi="", title="Unique Paper")
        deduped = ls._deduplicate([p1, p2, p3])
        assert len(deduped) == 2

    def test_score_relevance(self):
        ls = LiteratureSearch()
        p = _make_paper(title="cancer treatment study", abstract="novel cancer therapy", keywords=["cancer"])
        score = ls._score_relevance(p, "cancer treatment")
        assert score > 0

    def test_score_relevance_empty_query(self):
        ls = LiteratureSearch()
        p = _make_paper()
        assert ls._score_relevance(p, "") == 0.0

    def test_build_prisma(self):
        ls = LiteratureSearch()
        prisma = ls._build_prisma(identification=100, after_dedup=80, after_screening=30, included=20)
        assert prisma.identification == 100
        assert prisma.included == 20
        assert prisma.excluded_after_screen == 20

    def test_extract_pmids(self):
        result = LiteratureSearch.extract_pmids("See PMID: 12345 and PMID: 67890")
        assert "12345" in result
        assert "67890" in result

    def test_extract_dois(self):
        result = LiteratureSearch.extract_dois("DOI: 10.1234/test.2024")
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_search_arxiv(self):
        ls = LiteratureSearch()
        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            resp = MagicMock()
            resp.text = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Test</title><summary>Abstract</summary><published>2024-01-01</published><id>http://arxiv.org/abs/2401.0001v1</id><author><name>Author</name></author></entry></feed>'
            resp.raise_for_status = MagicMock()
            instance.get = AsyncMock(return_value=resp)
            MockClient.return_value = instance
            result = await ls._search_arxiv("test", 5)
            assert result.total_count == 1


# ===================== Literature Reader Tests =====================


class TestLiteratureReader:
    @pytest.mark.asyncio
    async def test_read_paper_no_gateway(self):
        reader = LiteratureReader(gateway=None)
        paper = _make_paper()
        reading = await reader.read_paper(paper)
        assert reading.paper_id != ""
        assert "[No LLM]" in reading.tldr

    @pytest.mark.asyncio
    async def test_read_paper_with_gateway(self):
        gw = _make_gateway()
        gw.chat = AsyncMock(return_value=LLMResponse(content="TLDR sentence", model="test"))
        gw.structured_output = AsyncMock(
            return_value=LLMResult(
                content="{}",
                parsed={"summary": "s", "key_points": ["k1"], "confidence": 0.9},
                model="test",
            )
        )
        reader = LiteratureReader(gateway=gw)
        paper = _make_paper(abstract="Test abstract content", sections={"intro": "Intro content"})
        reading = await reader.read_paper(paper)
        assert reading.tldr == "TLDR sentence"
        assert len(reading.section_summaries) >= 1

    @pytest.mark.asyncio
    async def test_read_paper_tldr_error(self):
        gw = _make_gateway()
        gw.chat = AsyncMock(return_value=LLMResponse(content="", error="fail", model="test"))
        gw.structured_output = AsyncMock(
            return_value=LLMResult(
                content="{}",
                parsed={"summary": "s", "key_points": [], "confidence": 0.5},
                model="test",
            )
        )
        reader = LiteratureReader(gateway=gw)
        paper = _make_paper(abstract="Abstract here")
        reading = await reader.read_paper(paper)
        assert reading.tldr == ""

    @pytest.mark.asyncio
    async def test_read_paper_no_sections_no_abstract(self):
        gw = _make_gateway()
        gw.chat = AsyncMock(return_value=LLMResponse(content="tldr", model="test"))
        reader = LiteratureReader(gateway=gw)
        paper = _make_paper(abstract="", sections={})
        reading = await reader.read_paper(paper)
        assert reading.tldr == ""

    @pytest.mark.asyncio
    async def test_read_papers_batch(self):
        gw = _make_gateway()
        gw.chat = AsyncMock(return_value=LLMResponse(content="tldr", model="test"))
        gw.structured_output = AsyncMock(
            return_value=LLMResult(
                content="{}",
                parsed={"summary": "s", "key_points": [], "confidence": 0.5},
                model="test",
            )
        )
        reader = LiteratureReader(gateway=gw)
        papers = [_make_paper(title=f"P{i}", abstract=f"Abstract {i}") for i in range(3)]
        readings = await reader.read_papers(papers, max_concurrent=2)
        assert len(readings) == 3

    @pytest.mark.asyncio
    async def test_section_summary_failure(self):
        gw = _make_gateway()
        gw.chat = AsyncMock(return_value=LLMResponse(content="tldr", model="test"))
        gw.structured_output = AsyncMock(return_value=LLMResult(content="", error="parse fail", model="test"))
        reader = LiteratureReader(gateway=gw)
        paper = _make_paper(abstract="Content for section")
        reading = await reader.read_paper(paper)
        assert len(reading.section_summaries) == 1
        assert reading.section_summaries[0].confidence == 0.0

    @pytest.mark.asyncio
    async def test_overall_assessment_failure(self):
        gw = _make_gateway()
        gw.chat = AsyncMock(return_value=LLMResponse(content="tldr", model="test"))
        call_count = 0

        def structured_side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResult(
                    content="{}", parsed={"summary": "s", "key_points": [], "confidence": 0.5}, model="test"
                )
            return LLMResult(content="", error="overall fail", model="test")

        gw.structured_output = AsyncMock(side_effect=structured_side_effect)
        reader = LiteratureReader(gateway=gw)
        paper = _make_paper(abstract="Content")
        reading = await reader.read_paper(paper)
        assert reading.methodology_assessment == ""


# ===================== Literature Synthesizer Tests =====================


class TestLiteratureSynthesizer:
    @pytest.mark.asyncio
    async def test_synthesize_empty(self):
        synth = LiteratureSynthesizer(gateway=None)
        result = await synth.synthesize([], topic="test")
        assert result.total_papers == 0

    @pytest.mark.asyncio
    async def test_synthesize_rule_based(self):
        synth = LiteratureSynthesizer(gateway=None)
        papers = [
            _make_paper(
                title="RCT Study", abstract="randomized controlled trial of drug", keywords=["cancer"], year="2024"
            ),
            _make_paper(title="Cohort Study", abstract="cohort study of patients", keywords=["cancer"], year="2023"),
        ]
        result = await synth.synthesize(papers, topic="cancer")
        assert result.total_papers == 2
        assert result.consensus_score is not None

    @pytest.mark.asyncio
    async def test_synthesize_with_llm(self):
        gw = _make_gateway()
        gw.structured_output = AsyncMock(
            return_value=LLMResult(
                content="{}",
                parsed={
                    "key_findings": [{"statement": "Finding 1", "supporting_papers": ["p1"], "confidence": 0.8}],
                    "contradictions": [],
                    "research_gaps": ["gap1"],
                    "trends": ["trend1"],
                    "supporting_count": 1,
                    "contradicting_count": 0,
                    "inconclusive_count": 0,
                },
                model="test",
            )
        )
        with patch("fusion_science.literature.synthesizer.LiteratureExtractor") as MockExt:
            MockExt.return_value.extract_batch = AsyncMock(
                return_value=[
                    StructuredExtraction(study_type="rct"),
                ]
            )
            synth = LiteratureSynthesizer(gateway=gw, extractor=MockExt.return_value)
            papers = [_make_paper(title="P1")]
            result = await synth.synthesize(papers, topic="test")
            assert result.consensus_score > 0

    @pytest.mark.asyncio
    async def test_synthesize_llm_fallback(self):
        gw = _make_gateway()
        gw.structured_output = AsyncMock(return_value=LLMResult(content="", error="fail", model="test"))
        with patch("fusion_science.literature.synthesizer.LiteratureExtractor") as MockExt:
            MockExt.return_value.extract_batch = AsyncMock(return_value=[])
            synth = LiteratureSynthesizer(gateway=gw, extractor=MockExt.return_value)
            papers = [_make_paper(title="P1")]
            result = await synth.synthesize(papers, topic="test")
            assert result.total_papers == 1

    def test_consensus_analysis_to_dict(self):
        ca = ConsensusAnalysis(
            topic="test",
            total_papers=5,
            supporting=3,
            contradicting=1,
            inconclusive=1,
            consensus_score=0.4,
            key_findings=[],
            contradictions=[],
            research_gaps=["gap1"],
            trends=["trend1"],
        )
        d = ca.to_dict()
        assert d["total_papers"] == 5
        assert d["consensus_score"] == 0.4


# ===================== Literature Review Tests =====================


class TestLiteratureReviewer:
    @pytest.mark.asyncio
    async def test_analyze_papers_empty(self):
        reviewer = LiteratureReviewer(gateway=None)
        review = await reviewer.analyze_papers([], "test query")
        assert len(review.papers_reviewed) == 0
        assert "No papers" in review.summary

    @pytest.mark.asyncio
    async def test_analyze_papers_rule_based(self):
        reviewer = LiteratureReviewer(gateway=None)
        papers = [_make_paper(title="P1", keywords=["methodology"]), _make_paper(title="P2", keywords=["clinical"])]
        review = await reviewer.analyze_papers(papers, "test")
        assert len(review.sections) >= 3
        assert review.consensus is not None

    @pytest.mark.asyncio
    async def test_analyze_papers_with_llm(self):
        gw = _make_gateway()
        gw.structured_output = AsyncMock(
            return_value=LLMResult(
                content="{}",
                parsed={"content": "Section text", "citations": ["c1"]},
                model="test",
            )
        )
        with patch("fusion_science.literature.review.LiteratureSynthesizer") as MockSynth:
            instance = MockSynth.return_value
            instance.synthesize = AsyncMock(
                return_value=ConsensusAnalysis(
                    topic="test",
                    total_papers=1,
                    supporting=1,
                    contradicting=0,
                    inconclusive=0,
                    consensus_score=1.0,
                    key_findings=[],
                    contradictions=[],
                    research_gaps=[],
                )
            )
            reviewer = LiteratureReviewer(gateway=gw, synthesizer=instance)
            papers = [_make_paper(title="P1")]
            review = await reviewer.analyze_papers(papers, "test")
            assert len(review.sections) >= 2

    def test_cluster_by_theme(self):
        reviewer = LiteratureReviewer(gateway=None)
        papers = [
            _make_paper(title="Genomics Study", keywords=["genome", "dna"]),
            _make_paper(title="Drug Discovery", keywords=["drug", "pharma"]),
            _make_paper(title="General Study", keywords=[]),
        ]
        reviewer._cluster_by_theme(papers)
        assert len(reviewer._themes) >= 1

    def test_generate_bibliography_apa(self):
        papers = [_make_paper(authors=["Smith J", "Lee K"], year="2024", title="Test", journal="Nature", doi="10.1/x")]
        bib = LiteratureReviewer.generate_bibliography(papers, style="apa")
        assert "Smith J" in bib

    def test_generate_bibliography_vancouver(self):
        papers = [_make_paper(authors=["Smith J"], year="2024", title="Test", journal="Nature")]
        bib = LiteratureReviewer.generate_bibliography(papers, style="vancouver")
        assert "1." in bib

    def test_generate_bibliography_default(self):
        papers = [_make_paper(authors=["Smith J"], year="2024", title="Test")]
        bib = LiteratureReviewer.generate_bibliography(papers, style="other")
        assert "Test" in bib

    def test_review_section_to_dict(self):
        rs = ReviewSection(title="Intro", content="Content", citations=["c1"])
        d = rs.to_dict()
        assert d["title"] == "Intro"

    def test_literature_review_to_dict(self):
        lr = LiteratureReview(title="Test Review", query="q", prisma=PRISMAFlow(included=5))
        d = lr.to_dict()
        assert d["title"] == "Test Review"
        assert d["prisma"]["included"] == 5


# ===================== Literature Extractor Tests =====================


class TestLiteratureExtractor:
    @pytest.mark.asyncio
    async def test_extract_no_gateway(self):
        ext = LiteratureExtractor(gateway=None)
        paper = _make_paper(abstract="This randomized controlled trial included 100 patients with cancer.")
        result = await ext.extract(paper)
        assert result.study_type == "RCT"
        assert result.sample_size == 100

    @pytest.mark.asyncio
    async def test_extract_with_gateway(self):
        gw = _make_gateway()
        gw.structured_output = AsyncMock(
            return_value=LLMResult(
                content="{}",
                parsed={
                    "study_type": "cohort",
                    "population": "adults",
                    "intervention": "drug",
                    "comparator": "placebo",
                    "outcome": "survival",
                    "sample_size": 200,
                    "effect_size": 0.5,
                    "confidence_interval_lower": 0.1,
                    "confidence_interval_upper": 0.9,
                    "p_value": 0.01,
                    "limitations": ["small sample"],
                    "funding_source": "NIH",
                },
                model="test",
            )
        )
        ext = LiteratureExtractor(gateway=gw)
        paper = _make_paper(abstract="Abstract")
        result = await ext.extract(paper)
        assert result.study_type == "cohort"
        assert result.sample_size == 200
        assert result.confidence_interval == (0.1, 0.9)
        assert result.p_value == 0.01

    @pytest.mark.asyncio
    async def test_extract_llm_fallback(self):
        gw = _make_gateway()
        gw.structured_output = AsyncMock(return_value=LLMResult(content="", error="fail", model="test"))
        ext = LiteratureExtractor(gateway=gw)
        paper = _make_paper(abstract="A case-control study of 50 patients")
        result = await ext.extract(paper)
        assert result.study_type == "case_control"

    @pytest.mark.asyncio
    async def test_extract_pico(self):
        gw = _make_gateway()
        gw.structured_output = AsyncMock(
            return_value=LLMResult(
                content="{}",
                parsed={"study_type": "rct", "population": "P", "intervention": "I", "comparator": "C", "outcome": "O"},
                model="test",
            )
        )
        ext = LiteratureExtractor(gateway=gw)
        pico = await ext.extract_pico(_make_paper(abstract="A"))
        assert pico.population == "P"

    @pytest.mark.asyncio
    async def test_extract_batch(self):
        ext = LiteratureExtractor(gateway=None)
        papers = [_make_paper(title=f"P{i}", abstract="A meta-analysis of 1000 subjects") for i in range(3)]
        results = await ext.extract_batch(papers, max_concurrent=2)
        assert len(results) == 3

    def test_classify_study_type(self):
        ext = LiteratureExtractor(gateway=None)
        assert ext._classify_study_type("this is a meta-analysis of studies") == "meta_analysis"
        assert ext._classify_study_type("randomized controlled trial") == "RCT"
        assert ext._classify_study_type("cohort study") == "cohort"
        assert ext._classify_study_type("case-control study") == "case_control"
        assert ext._classify_study_type("cross-sectional survey") == "cross_sectional"
        assert ext._classify_study_type("case report") == "case_report"
        assert ext._classify_study_type("review article") == "review"
        assert ext._classify_study_type("something else") == "other"

    def test_extract_sample_size(self):
        ext = LiteratureExtractor(gateway=None)
        assert ext._extract_sample_size("n = 150 patients") == 150
        assert ext._extract_sample_size("200 participants were enrolled") == 200
        assert ext._extract_sample_size("sample size was 50") == 50
        assert ext._extract_sample_size("no size mentioned") == 0

    def test_extract_p_value(self):
        ext = LiteratureExtractor(gateway=None)
        assert ext._extract_p_value("p < 0.05") is not None
        assert ext._extract_p_value("p = 0.01") is not None
        assert ext._extract_p_value("no p-value") is None

    def test_extract_population(self):
        ext = LiteratureExtractor(gateway=None)
        result = ext._extract_population("patients with diabetes were enrolled")
        assert "diabetes" in result

    def test_extract_limitations(self):
        ext = LiteratureExtractor(gateway=None)
        result = ext._extract_limitations("Limitations include small sample size and short follow-up period")
        assert len(result) >= 1

    def test_structured_extraction_to_dict(self):
        se = StructuredExtraction(
            study_type="rct",
            pico=PICO(population="P"),
            sample_size=100,
            effect_size=0.5,
            confidence_interval=(0.1, 0.9),
            p_value=0.01,
            limitations=["lim1"],
            funding_source="NIH",
        )
        d = se.to_dict()
        assert d["study_type"] == "rct"
        assert d["confidence_interval"] == [0.1, 0.9]

    @pytest.mark.asyncio
    async def test_extract_no_content(self):
        gw = _make_gateway()
        ext = LiteratureExtractor(gateway=gw)
        paper = _make_paper(abstract="", full_text="", sections={})
        result = await ext.extract(paper)
        assert result.study_type == ""


# ===================== Database Aggregator Tests =====================


class TestDatabaseAggregator:
    @pytest.mark.asyncio
    async def test_search_all_fail(self):
        agg = DatabaseAggregator(databases=["pubmed"])
        with patch.object(agg, "_get_connector", new_callable=AsyncMock) as mock_gc:
            mock_gc.return_value = None
            result = await agg.search("test")
            assert len(result.errors) >= 1

    @pytest.mark.asyncio
    async def test_search_success(self):
        agg = DatabaseAggregator(databases=["pubmed"])
        connector = AsyncMock()
        connector.search = AsyncMock(
            return_value=DatabaseResult(
                source="pubmed",
                query="test",
                items=[{"title": "Paper 1"}],
                total_count=1,
            )
        )
        with patch.object(agg, "_get_connector", new_callable=AsyncMock, return_value=connector):
            result = await agg.search("test")
            assert "pubmed" in result.databases_used
            assert result.total_count >= 1

    @pytest.mark.asyncio
    async def test_search_exception(self):
        agg = DatabaseAggregator(databases=["pubmed"])
        connector = AsyncMock()
        connector.search = AsyncMock(side_effect=Exception("db error"))
        with patch.object(agg, "_get_connector", new_callable=AsyncMock, return_value=connector):
            result = await agg.search("test")
            assert "pubmed" in result.errors

    @pytest.mark.asyncio
    async def test_fetch(self):
        agg = DatabaseAggregator()
        connector = AsyncMock()
        connector.fetch = AsyncMock(return_value=DatabaseResult(source="pubmed", query="id", items=[], total_count=0))
        with patch.object(agg, "_get_connector", new_callable=AsyncMock, return_value=connector):
            result = await agg.fetch("12345", "pubmed")
            assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_unknown_db(self):
        agg = DatabaseAggregator()
        with patch.object(agg, "_get_connector", new_callable=AsyncMock, return_value=None):
            result = await agg.fetch("12345", "unknowndb")
            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_exception(self):
        agg = DatabaseAggregator()
        connector = AsyncMock()
        connector.fetch = AsyncMock(side_effect=Exception("fail"))
        with patch.object(agg, "_get_connector", new_callable=AsyncMock, return_value=connector):
            result = await agg.fetch("12345", "pubmed")
            assert result is None

    @pytest.mark.asyncio
    async def test_close_all(self):
        agg = DatabaseAggregator()
        c1 = AsyncMock()
        agg._connectors = {"pubmed": c1}
        await agg.close_all()
        c1.close.assert_called_once()
        assert len(agg._connectors) == 0

    def test_item_dedup_key_doi(self):
        agg = DatabaseAggregator()
        key = agg._item_dedup_key({"doi": "10.1/test"}, "pubmed")
        assert key == "doi:10.1/test"

    def test_item_dedup_key_title(self):
        agg = DatabaseAggregator()
        key = agg._item_dedup_key({"title": "My Paper"}, "pubmed")
        assert key == "title:my paper"

    def test_item_dedup_key_fallback(self):
        agg = DatabaseAggregator()
        key = agg._item_dedup_key({}, "pubmed")
        assert "db:pubmed" in key

    def test_aggregated_result_to_dict(self):
        ar = AggregatedResult(
            query="test",
            databases_used=["pubmed"],
            results_by_db={"pubmed": DatabaseResult(source="pubmed", query="test", total_count=1)},
            merged_items=[{"title": "P1"}],
            total_count=1,
        )
        d = ar.to_dict()
        assert d["total_count"] == 1

    @pytest.mark.asyncio
    async def test_get_connector_caching(self):
        agg = DatabaseAggregator(databases=["pubmed"])
        mock_connector = AsyncMock()
        mock_connector.close = AsyncMock()
        with patch("importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_cls = MagicMock(return_value=mock_connector)
            mock_module.PubMedConnector = mock_cls
            mock_import.return_value = mock_module
            c1 = await agg._get_connector("pubmed")
            c2 = await agg._get_connector("pubmed")
            assert c1 is c2

    @pytest.mark.asyncio
    async def test_get_connector_unknown(self):
        agg = DatabaseAggregator()
        result = await agg._get_connector("unknowndb")
        assert result is None


# ===================== Database Base Tests =====================


class TestBaseConnector:
    def _make_concrete_connector(self, config=None):
        class TestConnector(BaseConnector):
            async def search(self, query, **kwargs):
                return DatabaseResult(source="test", query=query)

            async def fetch(self, identifier, **kwargs):
                return DatabaseResult(source="test", query=identifier)

        return TestConnector(config=config)

    def test_client_offline_mode(self):
        config = ConnectorConfig(offline_mode=True)
        connector = self._make_concrete_connector(config)
        with pytest.raises(RuntimeError, match="离线模式"):
            _ = connector.client

    @pytest.mark.asyncio
    async def test_close(self):
        connector = self._make_concrete_connector()
        connector._client = AsyncMock()
        connector._client.aclose = AsyncMock()
        await connector.close()
        assert connector._client is None

    @pytest.mark.asyncio
    async def test_rate_limit(self):
        config = ConnectorConfig(rate_limit=0.01)
        connector = self._make_concrete_connector(config)
        connector._last_request_time = 0
        await connector._rate_limit()
        assert connector._last_request_time > 0

    @pytest.mark.asyncio
    async def test_request_with_retry_offline(self):
        config = ConnectorConfig(offline_mode=True)
        connector = self._make_concrete_connector(config)
        with pytest.raises(RuntimeError, match="离线模式"):
            await connector._request_with_retry("GET", "/test")

    @pytest.mark.asyncio
    async def test_request_with_retry_success(self):
        connector = self._make_concrete_connector()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        connector._client = mock_client
        result = await connector._request_with_retry("GET", "/test")
        assert result == mock_resp

    @pytest.mark.asyncio
    async def test_request_with_retry_429(self):
        config = ConnectorConfig(max_retries=2, rate_limit=0.0)
        connector = self._make_concrete_connector(config)
        resp_429 = MagicMock()
        resp_429.status_code = 429
        error_429 = httpx.HTTPStatusError("rate", request=MagicMock(), response=resp_429)
        resp_ok = MagicMock()
        resp_ok.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.request = AsyncMock(side_effect=[error_429, resp_ok])
        connector._client = mock_client
        result = await connector._request_with_retry("GET", "/test")
        assert result == resp_ok

    @pytest.mark.asyncio
    async def test_request_with_retry_timeout(self):
        config = ConnectorConfig(max_retries=2, rate_limit=0.0)
        connector = self._make_concrete_connector(config)
        resp_ok = MagicMock()
        resp_ok.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.request = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("timeout"),
                resp_ok,
            ]
        )
        connector._client = mock_client
        result = await connector._request_with_retry("GET", "/test")
        assert result == resp_ok

    @pytest.mark.asyncio
    async def test_request_with_retry_non_retryable_http_error(self):
        config = ConnectorConfig(max_retries=2, rate_limit=0.0)
        connector = self._make_concrete_connector(config)
        resp_404 = MagicMock()
        resp_404.status_code = 404
        error_404 = httpx.HTTPStatusError("not found", request=MagicMock(), response=resp_404)
        mock_client = MagicMock()
        mock_client.request = AsyncMock(side_effect=error_404)
        connector._client = mock_client
        with pytest.raises(httpx.HTTPStatusError):
            await connector._request_with_retry("GET", "/test")

    @pytest.mark.asyncio
    async def test_request_with_retry_exhausted(self):
        config = ConnectorConfig(max_retries=1, rate_limit=0.0)
        connector = self._make_concrete_connector(config)
        mock_client = MagicMock()
        mock_client.request = AsyncMock(side_effect=httpx.ConnectError("no conn"))
        connector._client = mock_client
        with pytest.raises(httpx.ConnectError):
            await connector._request_with_retry("GET", "/test")

    def test_check_cache_miss(self):
        connector = self._make_concrete_connector()
        assert connector._check_cache("nokey") is None

    def test_check_cache_hit(self):
        connector = self._make_concrete_connector()
        connector._set_cache("key1", {"data": "value"})
        result = connector._check_cache("key1")
        assert result == {"data": "value"}

    def test_check_cache_expired(self):
        config = ConnectorConfig(cache_ttl=0)
        connector = self._make_concrete_connector(config)
        connector._set_cache("key1", "old_data")
        result = connector._check_cache("key1")
        assert result is None

    def test_check_cache_disabled(self):
        config = ConnectorConfig(cache_enabled=False)
        connector = self._make_concrete_connector(config)
        connector._set_cache("key1", "data")
        assert connector._check_cache("key1") is None

    def test_cache_lru_eviction(self):
        config = ConnectorConfig()
        connector = self._make_concrete_connector(config)
        from fusion_science.database.base import _MAX_CACHE_SIZE

        for i in range(_MAX_CACHE_SIZE + 10):
            connector._set_cache(f"k{i}", f"v{i}")
        assert len(connector._cache) <= _MAX_CACHE_SIZE

    def test_clear_cache(self):
        connector = self._make_concrete_connector()
        connector._set_cache("k1", "v1")
        connector.clear_cache()
        assert len(connector._cache) == 0

    def test_safe_text(self):
        assert BaseConnector.safe_text(None) == ""
        assert BaseConnector.safe_text("  hello  ") == "hello"
        assert BaseConnector.safe_text(42) == "42"

    def test_database_result_defaults(self):
        dr = DatabaseResult(source="test", query="q")
        assert dr.items == []
        assert dr.total_count == 0
        assert dr.error == ""
