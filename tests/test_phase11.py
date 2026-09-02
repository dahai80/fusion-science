from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from fusion_science.audit.integrity import (
    AuditIntegrityChecker,
    IntegrityIssue,
    IntegrityReport,
)
from fusion_science.audit.provenance import ProvenanceTracker
from fusion_science.audit.tracker import TraceRecorder
from fusion_science.core.gateway import LLMGateway
from fusion_science.core.retry import (
    ConnectionMonitor,
    RetryStats,
    retry_with_backoff,
)
from fusion_science.utils.keychain import SecureConfig
from fusion_science.utils.offline import get_connectivity, is_offline


class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        fn = AsyncMock(return_value="ok")
        result = await retry_with_backoff(fn, max_retries=3)
        assert result == "ok"
        assert fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        fn = AsyncMock(side_effect=[ConnectionError("fail"), "ok"])
        result = await retry_with_backoff(
            fn,
            max_retries=3,
            base_delay=0.01,
            jitter=False,
        )
        assert result == "ok"
        assert fn.call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        fn = AsyncMock(side_effect=ConnectionError("always fail"))
        with pytest.raises(ConnectionError):
            await retry_with_backoff(fn, max_retries=2, base_delay=0.01, jitter=False)

    @pytest.mark.asyncio
    async def test_non_retryable_exception(self):
        fn = AsyncMock(side_effect=ValueError("bad"))
        with pytest.raises(ValueError):
            await retry_with_backoff(
                fn,
                max_retries=3,
                retryable_exceptions=(ConnectionError,),
            )

    @pytest.mark.asyncio
    async def test_on_retry_callback(self):
        callback = MagicMock()
        err = ConnectionError("fail")
        fn = AsyncMock(side_effect=[err, "ok"])
        await retry_with_backoff(fn, max_retries=3, base_delay=0.01, on_retry=callback, jitter=False)
        callback.assert_called_once_with(1, err)


class TestConnectionMonitor:
    def test_initial_state(self):
        monitor = ConnectionMonitor()
        assert monitor.is_connected is True
        assert monitor.stats.connection_state == "unknown"

    @pytest.mark.asyncio
    async def test_health_check_pass(self):
        health = AsyncMock(return_value=True)
        monitor = ConnectionMonitor(health_check=health, check_interval=999)
        result = await monitor.check_health()
        assert result is True
        assert monitor.is_connected is True
        assert monitor.stats.connection_state == "connected"

    @pytest.mark.asyncio
    async def test_health_check_fail_triggers_disconnected(self):
        health = AsyncMock(return_value=False)
        monitor = ConnectionMonitor(health_check=health, max_failures=2, check_interval=999)
        await monitor.check_health()
        assert monitor.stats.connection_state == "reconnecting"
        await monitor.check_health()
        assert monitor.is_connected is False
        assert monitor.stats.connection_state == "disconnected"

    @pytest.mark.asyncio
    async def test_record_success_resets_failures(self):
        health = AsyncMock(return_value=False)
        monitor = ConnectionMonitor(health_check=health, max_failures=2, check_interval=999)
        await monitor.check_health()
        monitor.record_success()
        assert monitor.is_connected is True
        assert monitor.stats.successful == 1

    def test_record_failure(self):
        monitor = ConnectionMonitor(max_failures=3)
        monitor.record_failure("test error")
        assert monitor.stats.failed == 1
        assert monitor.stats.last_error == "test error"

    @pytest.mark.asyncio
    async def test_start_stop_monitor(self):
        health = AsyncMock(return_value=True)
        monitor = ConnectionMonitor(health_check=health, check_interval=0.1)
        await monitor.start_monitor()
        assert monitor._monitor_task is not None
        await monitor.stop_monitor()
        assert monitor._monitor_task.done()


class TestLLMGatewayRetry:
    @pytest.mark.asyncio
    async def test_gateway_connection_stats(self):
        gw = LLMGateway(model="test-model")
        stats = gw.get_connection_stats()
        assert isinstance(stats, RetryStats)
        assert stats.total_attempts == 0

    @pytest.mark.asyncio
    async def test_gateway_avg_response_time(self):
        gw = LLMGateway(model="test-model")
        assert gw.get_avg_response_time() == 0.0
        gw._request_times = [1.0, 2.0, 3.0]
        assert gw.get_avg_response_time() == 2.0

    @pytest.mark.asyncio
    async def test_gateway_start_stop_monitor(self):
        gw = LLMGateway(model="test-model")
        gw.start_connection_monitor(interval=999)
        assert gw._connection_monitor is not None
        gw.stop_connection_monitor()

    @pytest.mark.asyncio
    async def test_chat_with_retry_on_connect_error(self):
        gw = LLMGateway(model="test-model", base_url="http://localhost:1/v1")
        gw._memory_check_enabled = False
        call_count = 0

        class MockResponse:
            def __init__(self, data):
                self._data = data
                self.status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return self._data

        mock_client = MagicMock()
        mock_client.is_closed = False

        async def mock_post(url, json=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("connection refused")
            return MockResponse(
                {
                    "choices": [{"message": {"content": "retry ok"}, "finish_reason": "stop"}],
                    "usage": {},
                    "model": "test-model",
                }
            )

        mock_client.post = mock_post
        gw._client = mock_client
        gw._max_retries = 2
        resp = await gw.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "retry ok"

    @pytest.mark.asyncio
    async def test_chat_records_response_time(self):
        gw = LLMGateway(model="test-model", base_url="http://localhost:1/v1")
        gw._memory_check_enabled = False

        class MockResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                    "usage": {},
                    "model": "test-model",
                }

        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=MockResponse())
        gw._client = mock_client
        await gw.chat([{"role": "user", "content": "hi"}])
        assert len(gw._request_times) == 1
        assert gw._request_times[0] >= 0

    @pytest.mark.asyncio
    async def test_chat_empty_content_guard_dh3(self):
        gw = LLMGateway(model="test-model", base_url="http://localhost:1/v1")
        gw._memory_check_enabled = False

        class MockResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                    "usage": {},
                    "model": "test-model",
                }

        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=MockResponse())
        gw._client = mock_client
        resp = await gw.chat([{"role": "user", "content": "hi"}])
        assert resp.content == ""
        assert resp.error == "empty_content"

    @pytest.mark.asyncio
    async def test_chat_whitespace_content_guard_dh3(self):
        gw = LLMGateway(model="test-model", base_url="http://localhost:1/v1")
        gw._memory_check_enabled = False

        class MockResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [{"message": {"content": "   \n  "}, "finish_reason": "stop"}],
                    "usage": {},
                    "model": "test-model",
                }

        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=MockResponse())
        gw._client = mock_client
        resp = await gw.chat([{"role": "user", "content": "hi"}])
        assert resp.error == "empty_content"

    @pytest.mark.asyncio
    async def test_chat_real_content_passes_dh3(self):
        gw = LLMGateway(model="test-model", base_url="http://localhost:1/v1")
        gw._memory_check_enabled = False

        class MockResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [{"message": {"content": "valid summary"}, "finish_reason": "stop"}],
                    "usage": {},
                    "model": "test-model",
                }

        mock_client = MagicMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=MockResponse())
        gw._client = mock_client
        resp = await gw.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "valid summary"
        assert resp.error == ""


class TestAuditIntegrityChecker:
    def test_check_empty_session(self):
        checker = AuditIntegrityChecker()
        report = checker.check_session(None)
        assert report.passed is False
        assert any(i.category == "missing_session" for i in report.issues)

    def test_check_session_full_coverage(self):
        recorder = TraceRecorder()
        recorder.start_session()
        recorder.record(operation="db_query", source="test", description="query")
        recorder.record(operation="code_execution", source="test", description="code")
        recorder.record(operation="llm_call", source="test", description="llm")
        recorder.record(operation="visualization", source="test", description="viz")

        checker = AuditIntegrityChecker()
        report = checker.check_session(recorder.get_session())
        assert report.coverage_percent == 100.0
        assert report.total_entries == 4

    def test_check_session_missing_ops(self):
        recorder = TraceRecorder()
        recorder.start_session()
        recorder.record(operation="db_query", source="test", description="query")

        checker = AuditIntegrityChecker()
        report = checker.check_session(recorder.get_session())
        assert report.coverage_percent == 25.0
        missing_cats = [i.category for i in report.issues if i.category == "missing_operation_type"]
        assert len(missing_cats) == 3

    def test_check_session_broken_parent_ref(self):
        recorder = TraceRecorder()
        recorder.start_session()
        recorder.record(operation="db_query", source="test", description="q")
        recorder.set_parent("nonexistent_parent")
        recorder.record(operation="code_execution", source="test", description="c")

        checker = AuditIntegrityChecker()
        report = checker.check_session(recorder.get_session())
        assert any(i.category == "broken_parent_ref" for i in report.issues)

    def test_check_session_failed_no_error(self):
        recorder = TraceRecorder()
        recorder.start_session()
        recorder.record(
            operation="db_query",
            source="test",
            description="q",
            success=False,
            error="",
        )

        checker = AuditIntegrityChecker()
        report = checker.check_session(recorder.get_session())
        assert any(i.category == "missing_error_detail" for i in report.issues)

    def test_integrity_report_to_dict(self):
        report = IntegrityReport(
            session_id="test",
            total_entries=5,
            traced_operations=3,
            coverage_percent=75.0,
            issues=[IntegrityIssue(severity="warning", category="test", description="d")],
        )
        d = report.to_dict()
        assert d["session_id"] == "test"
        assert d["total_entries"] == 5
        assert len(d["issues"]) == 1

    def test_check_provenance_chain(self):
        tracker = ProvenanceTracker()
        tracker.start_tracking("test")
        src_id = tracker.add_source("pubmed", "db_query")
        tx_id = tracker.add_transformation("analyze", [src_id])
        tracker.add_output("figure", [tx_id], "figure")

        checker = AuditIntegrityChecker()
        report = checker.check_provenance_chain(tracker.get_graph())
        assert report.passed is True
        assert report.total_entries == 3

    def test_check_provenance_chain_broken_lineage(self):
        tracker = ProvenanceTracker()
        tracker.start_tracking("test")
        tracker.add_transformation("analyze", ["nonexistent_input"])

        checker = AuditIntegrityChecker()
        report = checker.check_provenance_chain(tracker.get_graph())
        assert report.passed is False
        assert any(i.category == "broken_lineage" for i in report.issues)

    def test_check_provenance_chain_orphan_output(self):
        tracker = ProvenanceTracker()
        tracker.start_tracking("test")
        tracker.add_output("figure", [], "figure")

        checker = AuditIntegrityChecker()
        report = checker.check_provenance_chain(tracker.get_graph())
        assert any(i.category == "orphan_output" for i in report.issues)


class TestSecureConfig:
    def test_store_and_retrieve_fallback(self):
        with (
            patch("fusion_science.utils.keychain.store_key", return_value=False),
            patch("fusion_science.utils.keychain.retrieve_key", return_value=None),
        ):
            sc = SecureConfig()
            sc.store("test_key", "test_val")
            val = sc.retrieve("test_key")
            assert val == "test_val"

    def test_delete_from_fallback(self):
        sc = SecureConfig()
        sc._fallback["del_me"] = "val"
        with patch("fusion_science.utils.keychain.delete_key", return_value=True):
            sc.delete("del_me")
        assert "del_me" not in sc._fallback

    def test_list_stored_keys(self):
        sc = SecureConfig()
        sc._fallback["key1"] = "val1"
        with patch("fusion_science.utils.keychain.list_keys", return_value=["key2"]):
            keys = sc.list_stored_keys()
        assert "key1" in keys
        assert "key2" in keys


class TestOfflineMode:
    def test_offline_env_override(self):
        with patch.dict("os.environ", {"FUSION_OFFLINE_MODE": "true"}):
            assert is_offline() is True

    def test_offline_env_false(self):
        with (
            patch.dict("os.environ", {"FUSION_OFFLINE_MODE": "false"}),
            patch("socket.create_connection", return_value=None),
        ):
            assert is_offline() is False

    def test_get_connectivity_offline(self):
        with patch.dict("os.environ", {"FUSION_OFFLINE_MODE": "true"}):
            result = get_connectivity()
            assert result["offline"] is True


class TestAPIRoutesPhase11:
    @pytest.fixture
    async def client(self):
        from httpx import ASGITransport, AsyncClient

        from fusion_science.api.app import create_app
        from fusion_science.config import ScienceConfig

        config = ScienceConfig(model_name="test-model")
        app = create_app(config)
        from fusion_science.audit.tracker import TraceRecorder
        from fusion_science.core.gateway import LLMGateway
        from fusion_science.session import MemorySessionStore, SessionManager

        gw = LLMGateway(model="test-model")
        app.state.gateway = gw
        app.state.config = config
        app.state.session_manager = SessionManager(store=MemorySessionStore())
        recorder = TraceRecorder()
        recorder.start_session(metadata={"api": True})
        app.state.recorder = recorder

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

        await gw.close()

    @pytest.mark.asyncio
    async def test_system_status_has_connection_stats(self, client):
        resp = await client.get("/api/v1/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "connection" in data
        assert "performance" in data

    @pytest.mark.asyncio
    async def test_tools_list(self, client):
        resp = await client.get("/api/v1/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_tools_register_and_get(self, client):
        body = {
            "name": "custom_tool",
            "description": "A custom tool",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
        resp = await client.post("/api/v1/tools", json=body)
        assert resp.status_code == 200
        assert resp.json()["registered"] == "custom_tool"

        resp = await client.get("/api/v1/tools/custom_tool")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "custom_tool"

    @pytest.mark.asyncio
    async def test_tools_duplicate_register(self, client):
        body = {
            "name": "dup_tool",
            "description": "dup",
            "parameters": {"type": "object"},
        }
        await client.post("/api/v1/tools", json=body)
        resp = await client.post("/api/v1/tools", json=body)
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_tools_unregister(self, client):
        body = {
            "name": "temp_tool",
            "description": "temp",
            "parameters": {"type": "object"},
        }
        await client.post("/api/v1/tools", json=body)
        resp = await client.delete("/api/v1/tools/temp_tool")
        assert resp.status_code == 200
        assert resp.json()["unregistered"] == "temp_tool"

    @pytest.mark.asyncio
    async def test_security_store_and_list(self, client):
        body = {"key_name": "test_api_key", "value": "sk-123"}
        resp = await client.post("/api/v1/security/keys", json=body)
        assert resp.status_code == 200

        resp = await client.get("/api/v1/security/keys")
        assert resp.status_code == 200
        assert "keys" in resp.json()

    @pytest.mark.asyncio
    async def test_security_key_exists(self, client):
        body = {"key_name": "check_key", "value": "val"}
        await client.post("/api/v1/security/keys", json=body)
        resp = await client.get("/api/v1/security/keys/check_key")
        assert resp.status_code == 200
        assert resp.json()["exists"] is True

    @pytest.mark.asyncio
    async def test_security_delete_key(self, client):
        body = {"key_name": "del_key", "value": "val"}
        await client.post("/api/v1/security/keys", json=body)
        resp = await client.delete("/api/v1/security/keys/del_key")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_audit_integrity_endpoint(self, client):
        resp = await client.get("/api/v1/sessions/test-session/audit/integrity")
        assert resp.status_code == 200
        data = resp.json()
        assert "passed" in data
        assert "total_entries" in data

    @pytest.mark.asyncio
    async def test_provenance_integrity_endpoint(self, client):
        resp = await client.get("/api/v1/sessions/test-session/audit/provenance-integrity")
        assert resp.status_code == 200
        data = resp.json()
        assert "passed" in data
