# tests/test_issues_6_7.py — regression tests for GitHub issues #6 and #7
# Issue #6: pipeline route called factory.create (missing method) -> 500
# Issue #7: search->analyze->review context not threaded through session

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from fusion_science.api.app import create_app
from fusion_science.config import ScienceConfig
from fusion_science.core.agent import AgentResult
from fusion_science.session import MemorySessionStore, SessionManager
from fusion_science.utils.events import reset_event_bus


@asynccontextmanager
async def _noop_lifespan(app):
    yield


def _make_app(
    *,
    tool_registry: MagicMock | None = None,
    router_agent: MagicMock | None = None,
):
    reset_event_bus()
    config = ScienceConfig()
    app = create_app(config=config)
    # bypass real lifespan (which builds a live LLMGateway + QueryRouterAgent)
    app.router.lifespan_context = _noop_lifespan
    app.state.config = config
    app.state.gateway = MagicMock()
    app.state.session_manager = SessionManager(MemorySessionStore())
    app.state.tool_registry = tool_registry or MagicMock()
    app.state.router_agent = router_agent or MagicMock()
    return app


@pytest.fixture
async def client_factory():
    apps = []

    async def _make(**kwargs):
        app = _make_app(**kwargs)
        apps.append(app)
        transport = ASGITransport(app=app)
        return app, AsyncClient(transport=transport, base_url="http://test")

    yield _make
    reset_event_bus()


# ===================== Issue #6: pipeline route uses create_pipeline =====================


class TestIssue6PipelineRoute:
    @pytest.mark.asyncio
    async def test_pipeline_run_does_not_raise_attribute_error(self, client_factory, monkeypatch):
        from fusion_science.api.routes import pipelines as pipelines_mod

        fake_pipeline = MagicMock()
        fake_result = MagicMock(task="q", summary="s", total_duration=0.1, agent_results=[])
        fake_pipeline.sequential = AsyncMock(return_value=fake_result)

        monkeypatch.setattr(
            pipelines_mod.PipelineFactory,
            "create_pipeline",
            lambda self, name: fake_pipeline,
        )

        _, client_cm = await client_factory()
        async with client_cm as client:
            resp = await client.post(
                "/api/v1/pipelines/literature_review/run",
                json={"query": "cancer biology"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline"] == "literature_review"

    @pytest.mark.asyncio
    async def test_pipeline_unknown_name(self, client_factory):
        _, client_cm = await client_factory()
        async with client_cm as client:
            resp = await client.post(
                "/api/v1/pipelines/nonexistent/run",
                json={"query": "x"},
            )
        data = resp.json()
        assert "not found" in data["error"]


# ===================== Issue #7: sequential search->analyze->review context =====================


class TestIssue7SequentialContext:
    @pytest.mark.asyncio
    async def test_search_stores_papers_into_session_context(self, client_factory):
        tool_registry = MagicMock()
        tool_registry.has_tool = MagicMock(return_value=True)
        tool_registry.execute = AsyncMock(
            return_value={
                "papers": [
                    {"title": "CRISPR in cancer", "year": 2024, "journal": "Nature", "doi": "10.1/a"},
                    {"title": "Gene therapy", "year": 2023, "journal": "Cell", "doi": "10.1/b"},
                ],
                "total_count": 2,
                "sources_used": ["pubmed"],
            }
        )

        app, client_cm = await client_factory(tool_registry=tool_registry)
        async with client_cm as client:
            session = await app.state.session_manager.create_session("seq test")
            resp = await client.post(
                f"/api/v1/sessions/{session.id}/search",
                json={"query": "cancer crispr"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["context_papers"] == 2
        reloaded = app.state.session_manager.get_session(session.id)
        assert len(reloaded.context.papers) == 2
        assert reloaded.context.papers[0]["title"] == "CRISPR in cancer"
        assert any(a.type == "search_result" for a in reloaded.artifacts)

    @pytest.mark.asyncio
    async def test_analyze_uses_prior_search_context(self, client_factory):
        tool_registry = MagicMock()
        tool_registry.has_tool = MagicMock(return_value=True)
        tool_registry.execute = AsyncMock(
            return_value={
                "papers": [{"title": "Prior paper", "year": 2024, "journal": "Nature"}],
                "total_count": 1,
                "sources_used": ["pubmed"],
            }
        )

        data_agent = MagicMock()
        data_agent.run = AsyncMock(
            return_value=AgentResult(
                agent_name="data",
                output="analysis output",
                error="",
                duration=0.1,
            )
        )
        router_agent = MagicMock()
        router_agent.get_agent = MagicMock(return_value=data_agent)

        app, client_cm = await client_factory(tool_registry=tool_registry, router_agent=router_agent)
        async with client_cm as client:
            session = await app.state.session_manager.create_session("analyze ctx")
            await client.post(f"/api/v1/sessions/{session.id}/search", json={"query": "prior"})
            resp = await client.post(
                f"/api/v1/sessions/{session.id}/analyze",
                json={"query": "compute stats"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["context_used"] is True
        called_task = data_agent.run.call_args.args[0]
        assert "Prior paper" in called_task
        reloaded = app.state.session_manager.get_session(session.id)
        assert any(a.type == "analysis_result" for a in reloaded.artifacts)

    @pytest.mark.asyncio
    async def test_review_uses_prior_search_and_analyze_context(self, client_factory):
        tool_registry = MagicMock()
        tool_registry.has_tool = MagicMock(return_value=True)
        tool_registry.execute = AsyncMock(
            return_value={
                "papers": [{"title": "Review paper", "year": 2024, "journal": "Science"}],
                "total_count": 1,
                "sources_used": ["pubmed"],
            }
        )

        data_agent = MagicMock()
        data_agent.run = AsyncMock(
            return_value=AgentResult(
                agent_name="data",
                output="prior analysis findings",
                error="",
                duration=0.1,
            )
        )
        lit_agent = MagicMock()
        lit_agent.run = AsyncMock(
            return_value=AgentResult(
                agent_name="literature",
                output="review output",
                error="",
                duration=0.1,
            )
        )
        router_agent = MagicMock()
        router_agent.get_agent = MagicMock(
            side_effect=lambda name: {"data": data_agent, "literature": lit_agent}.get(name)
        )

        app, client_cm = await client_factory(tool_registry=tool_registry, router_agent=router_agent)
        async with client_cm as client:
            session = await app.state.session_manager.create_session("review ctx")
            await client.post(f"/api/v1/sessions/{session.id}/search", json={"query": "topic"})
            await client.post(f"/api/v1/sessions/{session.id}/analyze", json={"query": "analyze it"})
            resp = await client.post(
                f"/api/v1/sessions/{session.id}/review",
                json={"query": "summarize literature"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["context_used"] is True
        called_task = lit_agent.run.call_args.args[0]
        assert "Review paper" in called_task
        assert "prior analysis findings" in called_task
        reloaded = app.state.session_manager.get_session(session.id)
        assert any(a.type == "review_result" for a in reloaded.artifacts)

    @pytest.mark.asyncio
    async def test_full_sequential_search_analyze_review(self, client_factory):
        tool_registry = MagicMock()
        tool_registry.has_tool = MagicMock(return_value=True)
        tool_registry.execute = AsyncMock(
            return_value={
                "papers": [{"title": "P1", "year": 2024, "journal": "J"}],
                "total_count": 1,
                "sources_used": ["pubmed"],
            }
        )
        data_agent = MagicMock()
        data_agent.run = AsyncMock(return_value=AgentResult(agent_name="data", output="D", error="", duration=0.0))
        lit_agent = MagicMock()
        lit_agent.run = AsyncMock(return_value=AgentResult(agent_name="literature", output="R", error="", duration=0.0))
        router_agent = MagicMock()
        router_agent.get_agent = MagicMock(side_effect=lambda n: {"data": data_agent, "literature": lit_agent}.get(n))

        app, client_cm = await client_factory(tool_registry=tool_registry, router_agent=router_agent)
        async with client_cm as client:
            session = await app.state.session_manager.create_session("e2e seq")
            sid = session.id
            r1 = await client.post(f"/api/v1/sessions/{sid}/search", json={"query": "q"})
            r2 = await client.post(f"/api/v1/sessions/{sid}/analyze", json={"query": "q"})
            r3 = await client.post(f"/api/v1/sessions/{sid}/review", json={"query": "q"})
        assert r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 200
        final = app.state.session_manager.get_session(sid)
        types = {a.type for a in final.artifacts}
        assert {"search_result", "analysis_result", "review_result"}.issubset(types)

    @pytest.mark.asyncio
    async def test_session_not_found_no_regression(self, client_factory):
        tool_registry = MagicMock()
        tool_registry.has_tool = MagicMock(return_value=True)
        tool_registry.execute = AsyncMock(return_value={"papers": []})
        app, client_cm = await client_factory(tool_registry=tool_registry)
        async with client_cm as client:
            r1 = await client.post("/api/v1/sessions/nope/search", json={"query": "q"})
            r2 = await client.post("/api/v1/sessions/nope/analyze", json={"query": "q"})
            r3 = await client.post("/api/v1/sessions/nope/review", json={"query": "q"})
        for r in (r1, r2, r3):
            assert r.status_code == 404
            assert r.json()["detail"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_analyze_no_prior_context_uses_raw_query(self, client_factory):
        data_agent = MagicMock()
        data_agent.run = AsyncMock(return_value=AgentResult(agent_name="data", output="o", error="", duration=0.0))
        router_agent = MagicMock()
        router_agent.get_agent = MagicMock(return_value=data_agent)
        app, client_cm = await client_factory(router_agent=router_agent)
        async with client_cm as client:
            session = await app.state.session_manager.create_session("no ctx")
            resp = await client.post(
                f"/api/v1/sessions/{session.id}/analyze",
                json={"query": "fresh query"},
            )
        assert resp.status_code == 200
        assert resp.json()["context_used"] is False
        assert data_agent.run.call_args.args[0] == "fresh query"
