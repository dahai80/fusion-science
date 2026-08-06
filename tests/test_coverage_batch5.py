from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import fusion_science
from fusion_science.api.app import _audit_handler, create_app
from fusion_science.api.sse import _sse_generator, sse_response
from fusion_science.config import ScienceConfig
from fusion_science.core.gateway import LLMGateway, LLMResponse
from fusion_science.core.tools import ToolRegistry
from fusion_science.session import MemorySessionStore, SessionManager
from fusion_science.utils.events import reset_event_bus

logger = logging.getLogger(__name__)


def _make_app(**overrides):
    reset_event_bus()
    config = ScienceConfig()
    application = create_app(config=config)
    application.state.config = config
    gateway = LLMGateway(
        model=config.model_name,
        base_url=config.engine_base_url,
        api_key=config.engine_api_key,
        temperature=config.engine_temperature,
        max_tokens=config.engine_max_tokens,
        timeout=config.engine_timeout,
    )
    application.state.gateway = gateway
    application.state.session_manager = SessionManager(MemorySessionStore())
    for k, v in overrides.items():
        setattr(application.state, k, v)
    return application


@pytest.fixture
def app():
    application = _make_app()
    yield application
    reset_event_bus()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# 1. app.py — lifespan, create_app, _audit_handler
# ---------------------------------------------------------------------------


class TestAppCreation:
    def test_create_app_default(self):
        reset_event_bus()
        application = create_app()
        assert application.title == "Fusion-Science API"
        assert application.version == fusion_science.__version__
        reset_event_bus()

    def test_create_app_with_config(self):
        reset_event_bus()
        config = ScienceConfig(model_name="test-model")
        application = create_app(config=config)
        assert application.state.config.model_name == "test-model"
        reset_event_bus()

    def test_create_app_routes_registered(self):
        reset_event_bus()
        application = create_app()
        all_paths = set()
        for r in application.routes:
            if hasattr(r, "original_router"):
                orig = r.original_router
                for sr in orig.routes:
                    all_paths.add(sr.path)
            elif hasattr(r, "path"):
                all_paths.add(r.path)
        assert "/health" in all_paths
        assert len(all_paths) > 10
        reset_event_bus()


class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_sets_state(self):
        reset_event_bus()
        from fusion_science.api.app import lifespan

        application = create_app()
        async with lifespan(application):
            assert hasattr(application.state, "gateway")
            assert hasattr(application.state, "session_manager")
            assert hasattr(application.state, "recorder")
        reset_event_bus()


class TestAuditHandler:
    @pytest.mark.asyncio
    async def test_audit_handler_no_recorder(self):
        _audit_handler._recorder = None
        from fusion_science.utils.events import Event

        event = Event(type="db_query", data={"session_id": "s1"}, source="test")
        await _audit_handler(event)

    @pytest.mark.asyncio
    async def test_audit_handler_with_recorder(self):
        recorder = MagicMock()
        _audit_handler._recorder = recorder
        from fusion_science.utils.events import Event

        event = Event(type="db_query", data={"session_id": "s1"}, source="test")
        await _audit_handler(event)
        recorder.record.assert_called_once()
        _audit_handler._recorder = None


# ---------------------------------------------------------------------------
# 2. middleware.py — APIKeyMiddleware
# ---------------------------------------------------------------------------


class TestAPIKeyMiddleware:
    @pytest.mark.asyncio
    async def test_no_key_set_passes(self):
        reset_event_bus()
        application = _make_app()
        os.environ.pop("FUSION_SCIENCE_API_KEY", None)
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/health")
            assert resp.status_code == 200
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_invalid_key_rejected(self):
        reset_event_bus()
        application = _make_app()
        os.environ["FUSION_SCIENCE_API_KEY"] = "mykey"
        try:
            transport = ASGITransport(app=application)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post("/api/v1/sessions", json={"title": "T"})
                assert resp.status_code == 401
        finally:
            del os.environ["FUSION_SCIENCE_API_KEY"]
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_valid_key_passes(self):
        reset_event_bus()
        application = _make_app()
        os.environ["FUSION_SCIENCE_API_KEY"] = "mykey"
        try:
            transport = ASGITransport(app=application)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post(
                    "/api/v1/sessions",
                    json={"title": "T"},
                    headers={"X-API-Key": "mykey"},
                )
                assert resp.status_code == 200
        finally:
            del os.environ["FUSION_SCIENCE_API_KEY"]
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_health_exempt(self):
        reset_event_bus()
        application = _make_app()
        os.environ["FUSION_SCIENCE_API_KEY"] = "mykey"
        try:
            transport = ASGITransport(app=application)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/api/v1/health")
                assert resp.status_code == 200
        finally:
            del os.environ["FUSION_SCIENCE_API_KEY"]
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_non_api_path_passes(self):
        reset_event_bus()
        application = _make_app()
        os.environ["FUSION_SCIENCE_API_KEY"] = "mykey"
        try:
            transport = ASGITransport(app=application)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/docs")
                assert resp.status_code != 401
        finally:
            del os.environ["FUSION_SCIENCE_API_KEY"]
        reset_event_bus()


# ---------------------------------------------------------------------------
# 3. analysis.py — POST /api/v1/analyze
# ---------------------------------------------------------------------------


class TestAnalysisRoute:
    @pytest.mark.asyncio
    async def test_analyze_no_router_agent(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "A"})
        sid = create.json()["session_id"]
        resp = await client.post(f"/api/v1/sessions/{sid}/analyze", json={"query": "test"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "router_agent not available"

    @pytest.mark.asyncio
    async def test_analyze_no_data_agent(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "A"})
        sid = create.json()["session_id"]
        router_agent = MagicMock()
        router_agent.get_agent.return_value = None
        client._transport.app.state.router_agent = router_agent
        resp = await client.post(f"/api/v1/sessions/{sid}/analyze", json={"query": "test"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "data agent not available"

    @pytest.mark.asyncio
    async def test_analyze_success(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "A"})
        sid = create.json()["session_id"]
        result = MagicMock()
        result.agent_name = "data"
        result.output = "result output"
        result.error = ""
        result.duration = 1.5
        data_agent = MagicMock()
        data_agent.run = AsyncMock(return_value=result)
        router_agent = MagicMock()
        router_agent.get_agent.return_value = data_agent
        client._transport.app.state.router_agent = router_agent
        resp = await client.post(f"/api/v1/sessions/{sid}/analyze", json={"query": "test", "max_iterations": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "data"
        assert data["output"] == "result output"

    @pytest.mark.asyncio
    async def test_analyze_invalid_language(self, client):
        resp = await client.post("/api/v1/sessions/s1/analyze", json={"query": "test", "language": "java"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 4. audit_route.py — GET /api/v1/sessions/{session_id}/audit
# ---------------------------------------------------------------------------


class TestAuditRoute:
    @pytest.mark.asyncio
    async def test_get_audit_no_recorder(self, client):
        client._transport.app.state.recorder = None
        resp = await client.get("/api/v1/sessions/s1/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "s1"
        assert data["trace_count"] == 0

    @pytest.mark.asyncio
    async def test_get_audit_with_recorder(self, client):
        recorder = MagicMock()
        recorder.get_entries.return_value = [{"id": "e1"}]
        client._transport.app.state.recorder = recorder
        resp = await client.get("/api/v1/sessions/s1/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace_count"] == 1

    @pytest.mark.asyncio
    async def test_integrity_no_recorder(self, client):
        client._transport.app.state.recorder = None
        resp = await client.get("/api/v1/sessions/s1/audit/integrity")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_integrity_with_recorder(self, client):
        session_mock = MagicMock()
        recorder = MagicMock()
        recorder.get_session.return_value = session_mock
        client._transport.app.state.recorder = recorder
        resp = await client.get("/api/v1/sessions/s1/audit/integrity")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_provenance_integrity_no_tracker(self, client):
        client._transport.app.state.provenance_tracker = None
        resp = await client.get("/api/v1/sessions/0/audit/provenance-integrity")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_provenance_integrity_with_tracker(self, client):
        graph_mock = MagicMock()
        provenance = MagicMock()
        provenance.get_graph.return_value = graph_mock
        client._transport.app.state.provenance_tracker = provenance
        resp = await client.get("/api/v1/sessions/0/audit/provenance-integrity")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5. chat.py — POST /api/v1/chat
# ---------------------------------------------------------------------------


class TestChatRoute:
    @pytest.mark.asyncio
    async def test_chat_session_not_found(self, client):
        resp = await client.post("/api/v1/sessions/missing/chat", json={"message": "hi"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_chat_non_stream(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "Chat"})
        sid = create.json()["session_id"]
        with patch.object(
            client._transport.app.state.gateway,
            "chat",
            new_callable=AsyncMock,
            return_value=LLMResponse(content="hello", model="test"),
        ):
            resp = await client.post(f"/api/v1/sessions/{sid}/chat", json={"message": "hi"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "hello"
        assert data["model"] == "test"

    @pytest.mark.asyncio
    async def test_chat_stream(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "Stream"})
        sid = create.json()["session_id"]

        async def _token_gen():
            yield "hello"
            yield " world"

        with patch.object(
            client._transport.app.state.gateway,
            "chat_stream",
            return_value=_token_gen(),
        ):
            resp = await client.post(f"/api/v1/sessions/{sid}/chat", json={"message": "hi", "stream": True})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_chat_empty_message_rejected(self, client):
        resp = await client.post("/api/v1/sessions/s1/chat", json={"message": ""})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 6. citations.py — GET/POST /api/v1/citations
# ---------------------------------------------------------------------------


class TestCitationsRoute:
    @pytest.mark.asyncio
    async def test_get_citation_graph_empty(self, client):
        resp = await client.get("/api/v1/citations/graph?session_id=nosession")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] == 0

    @pytest.mark.asyncio
    async def test_get_citation_graph_with_session(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "Cit"})
        sid = create.json()["session_id"]
        from fusion_science.session.models import Artifact

        artifact = Artifact(
            id="a1",
            type="citation",
            metadata={
                "title": "Test Paper",
                "authors": ["Author A"],
                "year": "2024",
                "journal": "Nature",
                "doi": "10.1234/test",
                "pmid": "12345",
                "keywords": ["test"],
            },
        )
        await client._transport.app.state.session_manager.add_artifact(sid, artifact)
        resp = await client.get(f"/api/v1/citations/graph?session_id={sid}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_add_citation(self, client):
        resp = await client.post(
            "/api/v1/citations/add",
            json={
                "title": "Test Paper",
                "authors": ["Author A"],
                "year": "2024",
                "journal": "Science",
                "doi": "10.1/test",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "key" in data

    @pytest.mark.asyncio
    async def test_get_bibliography_default(self, client):
        resp = await client.get("/api/v1/citations/bibliography")
        assert resp.status_code == 200
        data = resp.json()
        assert data["style"] == "apa"

    @pytest.mark.asyncio
    async def test_get_bibliography_vancouver(self, client):
        resp = await client.get("/api/v1/citations/bibliography?style=vancouver")
        assert resp.status_code == 200
        data = resp.json()
        assert data["style"] == "vancouver"

    @pytest.mark.asyncio
    async def test_citation_graph_with_bad_artifact(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "Bad"})
        sid = create.json()["session_id"]
        from fusion_science.session.models import Artifact

        artifact = Artifact(id="a2", type="citation", metadata={"title": "OK"})
        await client._transport.app.state.session_manager.add_artifact(sid, artifact)
        resp = await client.get(f"/api/v1/citations/graph?session_id={sid}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. compute.py — POST /api/v1/compute
# ---------------------------------------------------------------------------


class TestComputeRoute:
    @pytest.mark.asyncio
    async def test_code_gen_no_gateway(self, client):
        client._transport.app.state.gateway = None
        resp = await client.post("/api/v1/compute/code-gen", json={"query": "correlation analysis"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_code_gen_with_gateway(self, client):
        resp = await client.post("/api/v1/compute/code-gen", json={"query": "correlation analysis"})
        assert resp.status_code == 200
        data = resp.json()
        assert "code" in data or "error" in data

    @pytest.mark.asyncio
    async def test_code_gen_batch(self, client):
        resp = await client.post(
            "/api/v1/compute/code-gen/batch",
            json={
                "queries": ["t-test", "pca"],
                "language": "python",
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_jupyter_execute_no_kernel(self, client):
        with patch("fusion_science.api.routes.compute.JupyterKernelManager") as MockMgr:
            mgr = MockMgr.return_value
            mgr.start_kernel = AsyncMock(return_value=False)
            resp = await client.post(
                "/api/v1/compute/jupyter/execute",
                json={
                    "code": "print('hello')",
                    "timeout": 30,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["error"] == "Failed to start Jupyter kernel"

    @pytest.mark.asyncio
    async def test_jupyter_execute_success(self, client):
        with patch("fusion_science.api.routes.compute.JupyterKernelManager") as MockMgr:
            mgr = MockMgr.return_value
            mgr.start_kernel = AsyncMock(return_value=True)
            result = MagicMock()
            result.stdout = "hello"
            result.stderr = ""
            result.outputs = []
            result.success = True
            result.execution_count = 1
            mgr.execute = AsyncMock(return_value=result)
            mgr.shutdown = AsyncMock()
            resp = await client.post(
                "/api/v1/compute/jupyter/execute",
                json={
                    "code": "print('hello')",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_jupyter_execute_exception(self, client):
        with patch("fusion_science.api.routes.compute.JupyterKernelManager") as MockMgr:
            mgr = MockMgr.return_value
            mgr.start_kernel = AsyncMock(side_effect=RuntimeError("kernel crash"))
            mgr.shutdown = AsyncMock()
            resp = await client.post(
                "/api/v1/compute/jupyter/execute",
                json={
                    "code": "print('hello')",
                },
            )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_list_jupyter_kernels(self, client):
        with patch(
            "fusion_science.api.routes.compute.JupyterKernelManager.list_available_kernels",
            return_value=[],
        ):
            resp = await client.get("/api/v1/compute/jupyter/kernels")
        assert resp.status_code == 200
        assert "kernels" in resp.json()

    @pytest.mark.asyncio
    async def test_compliance_check_no_session(self, client):
        resp = await client.post(
            "/api/v1/compute/compliance",
            json={
                "usage_context": "personal",
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_compliance_check_with_session(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "Comp"})
        sid = create.json()["session_id"]
        resp = await client.post(
            "/api/v1/compute/compliance",
            json={
                "session_id": sid,
                "usage_context": "personal",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 8. databases.py — GET /api/v1/databases
# ---------------------------------------------------------------------------


class TestDatabasesRoute:
    @pytest.mark.asyncio
    async def test_list_databases(self, client):
        resp = await client.get("/api/v1/databases")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 5
        assert any(d["name"] == "pubmed" for d in data["databases"])

    @pytest.mark.asyncio
    async def test_database_status_unknown(self, client):
        resp = await client.get("/api/v1/databases/unknown/status")
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_database_status_pubmed(self, client):
        with patch("fusion_science.database.pubmed.PubMedConnector") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.health_check = AsyncMock(return_value=True)
            mock_inst.close = AsyncMock()
            mock_cls.return_value = mock_inst
            resp = await client.get("/api/v1/databases/pubmed/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True

    @pytest.mark.asyncio
    async def test_database_status_import_error(self, client):
        with patch(
            "fusion_science.database.pubmed.PubMedConnector",
            side_effect=ImportError("no module"),
        ):
            resp = await client.get("/api/v1/databases/pubmed/status")
        assert resp.status_code == 200
        assert resp.json()["available"] is False


# ---------------------------------------------------------------------------
# 9. health.py — GET /api/v1/health
# ---------------------------------------------------------------------------


class TestHealthRoute:
    @pytest.mark.asyncio
    async def test_health_check(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "fusion-science"


# ---------------------------------------------------------------------------
# 10. math.py — POST /api/v1/math
# ---------------------------------------------------------------------------


class TestMathRoute:
    @pytest.mark.asyncio
    async def test_explain_formula_no_gateway(self, client):
        client._transport.app.state.gateway = None
        resp = await client.post("/api/v1/math/explain", json={"formula": "p < 0.05"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "p-value"

    @pytest.mark.asyncio
    async def test_explain_formula_with_gateway(self, client):
        with patch.object(
            client._transport.app.state.gateway,
            "structured_output",
            new_callable=AsyncMock,
            return_value=MagicMock(
                parsed={"name": "p-value", "explanation": "stat test", "plain_text": "p less than 0.05"},
                error="",
            ),
        ):
            resp = await client.post("/api/v1/math/explain", json={"formula": "p < 0.05"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_explain_text(self, client):
        resp = await client.post("/api/v1/math/explain-text", json={"text": "Result: $r = 0.8$"})
        assert resp.status_code == 200
        data = resp.json()
        assert "explanations" in data

    @pytest.mark.asyncio
    async def test_explain_formula_gateway_error(self, client):
        with patch.object(
            client._transport.app.state.gateway,
            "structured_output",
            new_callable=AsyncMock,
            return_value=MagicMock(parsed=None, error="timeout"),
        ):
            resp = await client.post("/api/v1/math/explain", json={"formula": "x = 1"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 11. models.py — GET/PUT /api/v1/models
# ---------------------------------------------------------------------------


class TestModelsRoute:
    @pytest.mark.asyncio
    async def test_list_models_no_gateway(self, client):
        client._transport.app.state.gateway = None
        resp = await client.get("/api/v1/models")
        assert resp.status_code == 200
        assert resp.json()["error"] == "gateway not initialized"

    @pytest.mark.asyncio
    async def test_list_models_with_gateway(self, client):
        with patch.object(
            client._transport.app.state.gateway,
            "refresh_available_models",
            new_callable=AsyncMock,
            return_value=[{"id": "model-a"}],
        ):
            resp = await client.get("/api/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["models"]) == 1

    @pytest.mark.asyncio
    async def test_list_models_error(self, client):
        with patch.object(
            client._transport.app.state.gateway,
            "refresh_available_models",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ):
            resp = await client.get("/api/v1/models")
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_set_current_model(self, client):
        resp = await client.put("/api/v1/models/current", json={"model": "new-model"})
        assert resp.status_code == 200
        assert resp.json()["model"] == "new-model"

    @pytest.mark.asyncio
    async def test_set_current_model_no_gateway(self, client):
        client._transport.app.state.gateway = None
        resp = await client.put("/api/v1/models/current", json={"model": "x"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "gateway not initialized"

    @pytest.mark.asyncio
    async def test_get_model_roles(self, client):
        resp = await client.get("/api/v1/models/roles")
        assert resp.status_code == 200
        assert "roles" in resp.json()

    @pytest.mark.asyncio
    async def test_set_model_role(self, client):
        resp = await client.put("/api/v1/models/roles", json={"role": "reasoning", "model": "deepseek"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "reasoning"
        assert data["model"] == "deepseek"


# ---------------------------------------------------------------------------
# 12. pipelines.py — GET/POST /api/v1/pipelines
# ---------------------------------------------------------------------------


class TestPipelinesRoute:
    @pytest.mark.asyncio
    async def test_list_pipelines(self, client):
        resp = await client.get("/api/v1/pipelines")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_run_pipeline_not_found(self, client):
        resp = await client.post("/api/v1/pipelines/nonexistent/run", json={"query": "test"})
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_run_pipeline_no_engine(self, client):
        client._transport.app.state.gateway = None
        resp = await client.post("/api/v1/pipelines/literature_review/run", json={"query": "test"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "LLM engine not available"

    @pytest.mark.asyncio
    async def test_run_pipeline_error(self, client):
        with patch("fusion_science.api.routes.pipelines.PipelineFactory") as MockFactory:
            factory = MockFactory.return_value
            pipeline = MagicMock()
            pipeline.sequential = AsyncMock(side_effect=RuntimeError("pipeline fail"))
            factory.create.return_value = pipeline
            MockFactory.TEMPLATES = {"literature_review": MagicMock(agents=[MagicMock(name="a")])}
            resp = await client.post("/api/v1/pipelines/literature_review/run", json={"query": "test"})
        assert resp.status_code == 200
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# 13. review.py — POST /api/v1/review
# ---------------------------------------------------------------------------


class TestReviewRoute:
    @pytest.mark.asyncio
    async def test_review_no_router_agent(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "R"})
        sid = create.json()["session_id"]
        resp = await client.post(f"/api/v1/sessions/{sid}/review", json={"query": "cancer"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "router_agent not available"

    @pytest.mark.asyncio
    async def test_review_no_lit_agent(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "R"})
        sid = create.json()["session_id"]
        router_agent = MagicMock()
        router_agent.get_agent.return_value = None
        client._transport.app.state.router_agent = router_agent
        resp = await client.post(f"/api/v1/sessions/{sid}/review", json={"query": "cancer"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "literature agent not available"

    @pytest.mark.asyncio
    async def test_review_success(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "R"})
        sid = create.json()["session_id"]
        result = MagicMock()
        result.agent_name = "literature"
        result.output = "review output"
        result.error = ""
        result.duration = 2.0
        lit_agent = MagicMock()
        lit_agent.run = AsyncMock(return_value=result)
        router_agent = MagicMock()
        router_agent.get_agent.return_value = lit_agent
        client._transport.app.state.router_agent = router_agent
        resp = await client.post(f"/api/v1/sessions/{sid}/review", json={"query": "cancer", "max_papers": 10})
        assert resp.status_code == 200
        assert resp.json()["agent"] == "literature"


# ---------------------------------------------------------------------------
# 14. search.py — POST /api/v1/search
# ---------------------------------------------------------------------------


class TestSearchRoute:
    @pytest.mark.asyncio
    async def test_search_no_tool_registry(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "S"})
        sid = create.json()["session_id"]
        client._transport.app.state.tool_registry = None
        resp = await client.post(f"/api/v1/sessions/{sid}/search", json={"query": "cancer"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "search_literature tool not available"

    @pytest.mark.asyncio
    async def test_search_no_search_tool(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "S"})
        sid = create.json()["session_id"]
        registry = ToolRegistry()
        client._transport.app.state.tool_registry = registry
        resp = await client.post(f"/api/v1/sessions/{sid}/search", json={"query": "cancer"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "search_literature tool not available"

    @pytest.mark.asyncio
    async def test_search_success(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "S"})
        sid = create.json()["session_id"]
        registry = ToolRegistry()

        async def mock_handler(query, max_results=20, sources=None):
            return {"papers": [], "total_count": 0}

        registry.register(
            name="search_literature",
            description="Search",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            handler=mock_handler,
        )
        client._transport.app.state.tool_registry = registry
        resp = await client.post(
            f"/api/v1/sessions/{sid}/search", json={"query": "cancer", "max_results": 5, "sources": ["pubmed"]}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_search_empty_query_rejected(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "S"})
        sid = create.json()["session_id"]
        client._transport.app.state.tool_registry = None
        resp = await client.post(f"/api/v1/sessions/{sid}/search", json={"query": ""})
        assert resp.status_code == 200
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# 15. security.py — GET/POST/DELETE /api/v1/security
# ---------------------------------------------------------------------------


class TestSecurityRoute:
    @pytest.mark.asyncio
    async def test_store_key(self, client):
        with patch("fusion_science.api.routes.security._secure_config") as mock_sc:
            mock_sc.store.return_value = True
            resp = await client.post(
                "/api/v1/security/keys",
                json={
                    "key_name": "test_key",
                    "value": "secret_val",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["stored"] == "test_key"

    @pytest.mark.asyncio
    async def test_store_key_failure(self, client):
        with patch("fusion_science.api.routes.security._secure_config") as mock_sc:
            mock_sc.store.return_value = False
            resp = await client.post(
                "/api/v1/security/keys",
                json={
                    "key_name": "fail_key",
                    "value": "val",
                },
            )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_list_keys(self, client):
        with patch("fusion_science.api.routes.security._secure_config") as mock_sc:
            mock_sc.list_stored_keys.return_value = ["key1", "key2"]
            resp = await client.get("/api/v1/security/keys")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_retrieve_key_found(self, client):
        with patch("fusion_science.api.routes.security._secure_config") as mock_sc:
            mock_sc.retrieve.return_value = "secret_value"
            resp = await client.get("/api/v1/security/keys/mykey")
        assert resp.status_code == 200
        assert resp.json()["exists"] is True

    @pytest.mark.asyncio
    async def test_retrieve_key_not_found(self, client):
        with patch("fusion_science.api.routes.security._secure_config") as mock_sc:
            mock_sc.retrieve.return_value = None
            resp = await client.get("/api/v1/security/keys/nokey")
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_delete_key_success(self, client):
        with patch("fusion_science.api.routes.security._secure_config") as mock_sc:
            mock_sc.delete.return_value = True
            resp = await client.delete("/api/v1/security/keys/mykey")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == "mykey"

    @pytest.mark.asyncio
    async def test_delete_key_failure(self, client):
        with patch("fusion_science.api.routes.security._secure_config") as mock_sc:
            mock_sc.delete.return_value = False
            resp = await client.delete("/api/v1/security/keys/nokey")
        assert resp.status_code == 200
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# 16. sessions.py — CRUD /api/v1/sessions
# ---------------------------------------------------------------------------


class TestSessionsRoute:
    @pytest.mark.asyncio
    async def test_create_session(self, client):
        resp = await client.post("/api/v1/sessions", json={"title": "Test"})
        assert resp.status_code == 200
        assert "session_id" in resp.json()

    @pytest.mark.asyncio
    async def test_list_sessions(self, client):
        await client.post("/api/v1/sessions", json={"title": "A"})
        resp = await client.get("/api/v1/sessions")
        assert resp.status_code == 200
        assert len(resp.json()["sessions"]) >= 1

    @pytest.mark.asyncio
    async def test_get_session(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "X"})
        sid = create.json()["session_id"]
        resp = await client.get(f"/api/v1/sessions/{sid}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_missing_session(self, client):
        resp = await client.get("/api/v1/sessions/nope")
        assert resp.status_code == 200
        assert resp.json()["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_delete_session(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "D"})
        sid = create.json()["session_id"]
        resp = await client.delete(f"/api/v1/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_missing_session(self, client):
        resp = await client.delete("/api/v1/sessions/nope")
        assert resp.status_code == 200
        assert resp.json()["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_update_session_title(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "Old"})
        sid = create.json()["session_id"]
        resp = await client.patch(f"/api/v1/sessions/{sid}", json={"title": "New"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New"

    @pytest.mark.asyncio
    async def test_update_missing_session(self, client):
        resp = await client.patch("/api/v1/sessions/nope", json={"title": "New"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "session_not_found"


# ---------------------------------------------------------------------------
# 17. system.py — GET/POST /api/v1/system
# ---------------------------------------------------------------------------


class TestSystemRoute:
    @pytest.mark.asyncio
    async def test_system_status_with_gateway(self, client):
        with patch("fusion_science.api.routes.system.is_offline", return_value=False):
            resp = await client.get("/api/v1/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "model" in data
        assert "connection" in data

    @pytest.mark.asyncio
    async def test_system_status_no_gateway(self, client):
        client._transport.app.state.gateway = None
        with patch("fusion_science.api.routes.system.is_offline", return_value=True):
            resp = await client.get("/api/v1/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "unknown"
        assert data["offline"] is True

    @pytest.mark.asyncio
    async def test_connectivity_check(self, client):
        with patch(
            "fusion_science.api.routes.system.get_connectivity",
            return_value={"offline": False},
        ):
            resp = await client.get("/api/v1/system/connectivity")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_mirror_latency_test(self, client):
        with patch("fusion_science.api.routes.system.MirrorRouter") as MockRouter:
            router = MockRouter.return_value
            router.test_all_latency = AsyncMock(return_value={"pubmed": {"primary": 0.5, "mirror": -1.0}})
            router._auto_switch = False
            resp = await client.get("/api/v1/system/mirrors/latency")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_mirror_status(self, client):
        with patch("fusion_science.api.routes.system.MirrorRouter") as MockRouter:
            router = MockRouter.return_value
            router.get_status_report.return_value = {"offline_mode": False}
            resp = await client.get("/api/v1/system/mirrors/status")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_mirror_auto_switch_enable(self, client):
        with patch("fusion_science.api.routes.system.MirrorRouter") as MockRouter:
            router = MockRouter.return_value
            router.enable_auto_switch = MagicMock()
            resp = await client.post("/api/v1/system/mirrors/auto-switch?enable=true")
        assert resp.status_code == 200
        assert resp.json()["auto_switch"] is True

    @pytest.mark.asyncio
    async def test_mirror_auto_switch_disable(self, client):
        with patch("fusion_science.api.routes.system.MirrorRouter") as MockRouter:
            router = MockRouter.return_value
            router.enable_auto_switch = MagicMock()
            resp = await client.post("/api/v1/system/mirrors/auto-switch?enable=false")
        assert resp.status_code == 200
        assert resp.json()["auto_switch"] is False


# ---------------------------------------------------------------------------
# 18. tools.py — GET/POST/DELETE /api/v1/tools
# ---------------------------------------------------------------------------


class TestToolsRoute:
    @pytest.mark.asyncio
    async def test_list_tools_no_registry(self, client):
        client._transport.app.state.tool_registry = None
        resp = await client.get("/api/v1/tools")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_list_tools_with_registry(self, client):
        registry = ToolRegistry()
        registry.register(
            name="test_tool",
            description="A test",
            parameters={"type": "object", "properties": {}},
        )
        client._transport.app.state.tool_registry = registry
        resp = await client.get("/api/v1/tools")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_register_tool(self, client):
        client._transport.app.state.tool_registry = ToolRegistry()
        resp = await client.post(
            "/api/v1/tools",
            json={
                "name": "new_tool",
                "description": "A new tool",
                "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
                "mcp_exposed": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["registered"] == "new_tool"

    @pytest.mark.asyncio
    async def test_register_tool_duplicate(self, client):
        registry = ToolRegistry()
        registry.register(name="dup", description="dup", parameters={})
        client._transport.app.state.tool_registry = registry
        resp = await client.post(
            "/api/v1/tools",
            json={
                "name": "dup",
                "description": "dup again",
                "parameters": {},
            },
        )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_register_tool_creates_registry(self, client):
        client._transport.app.state.tool_registry = None
        resp = await client.post(
            "/api/v1/tools",
            json={
                "name": "fresh",
                "description": "fresh tool",
                "parameters": {},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["registered"] == "fresh"

    @pytest.mark.asyncio
    async def test_unregister_tool(self, client):
        registry = ToolRegistry()
        registry.register(name="del_me", description="to delete", parameters={})
        client._transport.app.state.tool_registry = registry
        resp = await client.delete("/api/v1/tools/del_me")
        assert resp.status_code == 200
        assert resp.json()["unregistered"] == "del_me"

    @pytest.mark.asyncio
    async def test_unregister_tool_not_found(self, client):
        registry = ToolRegistry()
        client._transport.app.state.tool_registry = registry
        resp = await client.delete("/api/v1/tools/nope")
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_get_tool(self, client):
        registry = ToolRegistry()
        registry.register(
            name="my_tool",
            description="My tool",
            parameters={"type": "object"},
        )
        client._transport.app.state.tool_registry = registry
        resp = await client.get("/api/v1/tools/my_tool")
        assert resp.status_code == 200
        assert resp.json()["name"] == "my_tool"

    @pytest.mark.asyncio
    async def test_get_tool_not_found(self, client):
        client._transport.app.state.tool_registry = ToolRegistry()
        resp = await client.get("/api/v1/tools/nope")
        assert resp.status_code == 200
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# 19. visualize.py — POST /api/v1/visualize
# ---------------------------------------------------------------------------


class TestVisualizeRoute:
    @pytest.mark.asyncio
    async def test_visualize_no_router_agent(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "V"})
        sid = create.json()["session_id"]
        resp = await client.post(f"/api/v1/sessions/{sid}/visualize", json={"query": "scatter plot"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "router_agent not available"

    @pytest.mark.asyncio
    async def test_visualize_no_viz_agent(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "V"})
        sid = create.json()["session_id"]
        router_agent = MagicMock()
        router_agent.get_agent.return_value = None
        client._transport.app.state.router_agent = router_agent
        resp = await client.post(f"/api/v1/sessions/{sid}/visualize", json={"query": "scatter"})
        assert resp.status_code == 200
        assert resp.json()["error"] == "visualize agent not available"

    @pytest.mark.asyncio
    async def test_visualize_success(self, client):
        create = await client.post("/api/v1/sessions", json={"title": "V"})
        sid = create.json()["session_id"]
        result = MagicMock()
        result.agent_name = "visualize"
        result.output = "chart output"
        result.error = ""
        result.duration = 3.0
        viz_agent = MagicMock()
        viz_agent.run = AsyncMock(return_value=result)
        router_agent = MagicMock()
        router_agent.get_agent.return_value = viz_agent
        client._transport.app.state.router_agent = router_agent
        resp = await client.post(
            f"/api/v1/sessions/{sid}/visualize",
            json={
                "query": "scatter plot",
                "chart_type": "scatter",
                "max_iterations": 5,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["agent"] == "visualize"


# ---------------------------------------------------------------------------
# 20. visualize_ext.py — POST /api/v1/viz
# ---------------------------------------------------------------------------


class TestVisualizeExtRoute:
    @pytest.mark.asyncio
    async def test_molecule_smiles_fallback(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.from_smiles = AsyncMock(side_effect=ImportError("no rdkit"))
            viz.from_smiles_2d_fallback = AsyncMock(return_value={"html": "<p>fallback</p>", "smiles": "CCO"})
            resp = await client.post(
                "/api/v1/viz/molecule/smiles",
                json={
                    "smiles": "CCO",
                    "width": 400,
                    "height": 300,
                },
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_molecule_smiles_success(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.from_smiles = AsyncMock(return_value={"html": "<p>3d</p>", "smiles": "CCO"})
            resp = await client.post(
                "/api/v1/viz/molecule/smiles",
                json={
                    "smiles": "CCO",
                },
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_molecule_smiles_error(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.from_smiles = AsyncMock(side_effect=RuntimeError("crash"))
            resp = await client.post(
                "/api/v1/viz/molecule/smiles",
                json={
                    "smiles": "BAD",
                },
            )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_molecule_pdb_success(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.from_pdb = AsyncMock(return_value={"html": "<p>pdb</p>", "pdb_id": "1BNA"})
            resp = await client.post(
                "/api/v1/viz/molecule/pdb",
                json={
                    "pdb_id": "1BNA",
                },
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_molecule_pdb_error(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.from_pdb = AsyncMock(side_effect=RuntimeError("pdb fail"))
            resp = await client.post(
                "/api/v1/viz/molecule/pdb",
                json={
                    "pdb_id": "XXXX",
                },
            )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_protein_visualize_success(self, client):
        with patch("fusion_science.api.routes.visualize_ext.ProteinVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.visualize = AsyncMock(return_value={"html": "<p>protein</p>", "pdb_id": "6M0J"})
            resp = await client.post(
                "/api/v1/viz/protein",
                json={
                    "pdb_id": "6M0J",
                    "style": "cartoon",
                },
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_protein_visualize_error(self, client):
        with patch("fusion_science.api.routes.visualize_ext.ProteinVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.visualize = AsyncMock(side_effect=RuntimeError("protein fail"))
            resp = await client.post(
                "/api/v1/viz/protein",
                json={
                    "pdb_id": "6M0J",
                },
            )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_protein_compare_success(self, client):
        with patch("fusion_science.api.routes.visualize_ext.ProteinVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.compare_structures = AsyncMock(return_value={"html": "<p>compare</p>"})
            resp = await client.post(
                "/api/v1/viz/protein/compare",
                json={
                    "pdb_id_1": "6M0J",
                    "pdb_id_2": "1BNA",
                },
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_protein_compare_error(self, client):
        with patch("fusion_science.api.routes.visualize_ext.ProteinVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.compare_structures = AsyncMock(side_effect=RuntimeError("compare fail"))
            resp = await client.post(
                "/api/v1/viz/protein/compare",
                json={
                    "pdb_id_1": "6M0J",
                    "pdb_id_2": "1BNA",
                },
            )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_molecule_smiles_non_dict_result(self, client):
        with patch("fusion_science.api.routes.visualize_ext.MoleculeVisualizer") as MockViz:
            viz = MockViz.return_value
            viz.from_smiles = AsyncMock(return_value="<html>string</html>")
            resp = await client.post(
                "/api/v1/viz/molecule/smiles",
                json={
                    "smiles": "CCO",
                },
            )
        assert resp.status_code == 200
        assert "html" in resp.json()


# ---------------------------------------------------------------------------
# 21. sse.py — SSE streaming
# ---------------------------------------------------------------------------


class TestSSE:
    @pytest.mark.asyncio
    async def test_sse_generator_normal(self):
        async def token_gen():
            yield "hello"
            yield " world"

        chunks = []
        async for chunk in _sse_generator(token_gen()):
            chunks.append(chunk)
        assert len(chunks) == 3
        assert '"token": "hello"' in chunks[0]
        assert '"done": true' in chunks[2]

    @pytest.mark.asyncio
    async def test_sse_generator_error(self):
        async def failing_gen():
            yield "ok"
            raise RuntimeError("stream broke")

        chunks = []
        async for chunk in _sse_generator(failing_gen()):
            chunks.append(chunk)
        assert any("error" in c for c in chunks)

    def test_sse_response_headers(self):
        async def gen():
            yield "t"

        resp = sse_response(gen())
        assert resp.media_type == "text/event-stream"
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["connection"] == "keep-alive"
        assert resp.headers["x-accel-buffering"] == "no"
