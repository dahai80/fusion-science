from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fusion_science.database.chinese import CHINESE_CONNECTORS
from fusion_science.database.chinese.cnki import CNKIConnector
from fusion_science.database.chinese.ngdc import NGDCConnector
from fusion_science.database.chinese.scidb import ScienceDBConnector
from fusion_science.database.mirror import MirrorRouter

# ------------------------------------------------------------------
# NGDC
# ------------------------------------------------------------------


class TestNGDCConnector:

    def test_init_defaults(self):
        c = NGDCConnector()
        assert c.config.base_url == "https://ngdc.cncb.ac.cn"
        assert c.config.rate_limit == 1.0

    def test_init_env_override(self):
        with patch.dict("os.environ", {"FUSION_SCI_NGDC_URL": "http://custom-ngdc.local"}):
            c = NGDCConnector()
            assert c.config.base_url == "http://custom-ngdc.local"

    def test_offline_mode_env(self):
        with patch.dict("os.environ", {"FUSION_OFFLINE_MODE": "true"}):
            c = NGDCConnector()
            assert c.config.offline_mode is True

    @pytest.mark.asyncio
    async def test_search_returns_result(self):
        c = NGDCConnector()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "total": 2,
            "results": [
                {"accession": "GSA001", "title": "Test Dataset", "description": "desc", "organism": "Homo sapiens"},
                {"accession": "GSA002", "title": "Another", "description": "desc2"},
            ],
        }
        mock_resp.raise_for_status = MagicMock()
        c._request_with_retry = AsyncMock(return_value=mock_resp)
        result = await c.search("cancer genomics", max_results=10)
        assert result.source == "ngdc"
        assert result.total_count == 2
        assert len(result.items) == 2
        assert result.items[0]["accession"] == "GSA001"
        await c.close()

    @pytest.mark.asyncio
    async def test_search_gsa(self):
        c = NGDCConnector()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 0, "results": []}
        mock_resp.raise_for_status = MagicMock()
        c._request_with_retry = AsyncMock(return_value=mock_resp)
        result = await c.search_gsa("test")
        assert result.source == "ngdc"
        await c.close()

    @pytest.mark.asyncio
    async def test_search_gwh(self):
        c = NGDCConnector()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 0, "data": []}
        mock_resp.raise_for_status = MagicMock()
        c._request_with_retry = AsyncMock(return_value=mock_resp)
        result = await c.search_gwh("test")
        assert result.source == "ngdc"
        await c.close()

    @pytest.mark.asyncio
    async def test_fetch_detail(self):
        c = NGDCConnector()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "accession": "GSA001",
            "title": "Detail",
            "description": "Full desc",
            "sample_count": 100,
        }
        mock_resp.raise_for_status = MagicMock()
        c._request_with_retry = AsyncMock(return_value=mock_resp)
        result = await c.fetch("GSA001")
        assert result.items[0]["accession"] == "GSA001"
        assert result.items[0]["sample_count"] == 100
        await c.close()

    @pytest.mark.asyncio
    async def test_search_error(self):
        c = NGDCConnector()
        c._request_with_retry = AsyncMock(side_effect=Exception("network error"))
        result = await c.search("test")
        assert result.error == "network error"
        await c.close()

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        c = NGDCConnector()
        c._request_with_retry = AsyncMock(side_effect=Exception("fail"))
        result = await c.fetch("X")
        assert result.error == "fail"
        await c.close()

    @pytest.mark.asyncio
    async def test_search_cache_hit(self):
        c = NGDCConnector()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total": 1, "results": [{"accession": "A1", "title": "T"}]}
        mock_resp.raise_for_status = MagicMock()
        c._request_with_retry = AsyncMock(return_value=mock_resp)
        await c.search("cache_test")
        await c.search("cache_test")
        assert c._request_with_retry.call_count == 1
        await c.close()


# ------------------------------------------------------------------
# CNKI
# ------------------------------------------------------------------


class TestCNKIConnector:

    def test_init_defaults(self):
        c = CNKIConnector()
        assert c.config.base_url == "https://www.cnki.net"

    def test_init_env_override(self):
        with patch.dict("os.environ", {"FUSION_SCI_CNKI_URL": "http://custom-cnki.local"}):
            c = CNKIConnector()
            assert c.config.base_url == "http://custom-cnki.local"

    @pytest.mark.asyncio
    async def test_search_returns_result(self):
        c = CNKIConnector()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "total": 1,
            "results": [{"docId": "DOC001", "title": "Test Paper", "authors": "Zhang S", "journal": "Sci China"}],
        }
        mock_resp.raise_for_status = MagicMock()
        c._request_with_retry = AsyncMock(return_value=mock_resp)
        result = await c.search("基因编辑")
        assert result.source == "cnki"
        assert result.items[0]["doc_id"] == "DOC001"
        assert result.items[0]["journal"] == "Sci China"
        await c.close()

    @pytest.mark.asyncio
    async def test_fetch_detail(self):
        c = CNKIConnector()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "docId": "DOC001", "title": "Detail", "abstract": "Full abstract",
            "institution": "Peking University", "fund": "NSFC",
        }
        mock_resp.raise_for_status = MagicMock()
        c._request_with_retry = AsyncMock(return_value=mock_resp)
        result = await c.fetch("DOC001")
        assert result.items[0]["institution"] == "Peking University"
        assert result.items[0]["fund"] == "NSFC"
        await c.close()

    @pytest.mark.asyncio
    async def test_search_error(self):
        c = CNKIConnector()
        c._request_with_retry = AsyncMock(side_effect=Exception("timeout"))
        result = await c.search("test")
        assert result.error == "timeout"
        await c.close()


# ------------------------------------------------------------------
# ScienceDB
# ------------------------------------------------------------------


class TestScienceDBConnector:

    def test_init_defaults(self):
        c = ScienceDBConnector()
        assert c.config.base_url == "https://www.scidb.cn"

    def test_init_env_override(self):
        with patch.dict("os.environ", {"FUSION_SCI_SCIENCEDB_URL": "http://custom-scidb.local"}):
            c = ScienceDBConnector()
            assert c.config.base_url == "http://custom-scidb.local"

    @pytest.mark.asyncio
    async def test_search_returns_result(self):
        c = ScienceDBConnector()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "total": 1,
            "results": [{"id": "DS001", "title": "Climate Data", "doi": "10.1234/test", "file_count": 5}],
        }
        mock_resp.raise_for_status = MagicMock()
        c._request_with_retry = AsyncMock(return_value=mock_resp)
        result = await c.search("climate")
        assert result.source == "scidb"
        assert result.items[0]["dataset_id"] == "DS001"
        assert result.items[0]["doi"] == "10.1234/test"
        await c.close()

    @pytest.mark.asyncio
    async def test_fetch_detail(self):
        c = ScienceDBConnector()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "id": "DS001", "title": "Detail", "institution": "CAS",
        }
        mock_resp.raise_for_status = MagicMock()
        c._request_with_retry = AsyncMock(return_value=mock_resp)
        result = await c.fetch("DS001")
        assert result.items[0]["institution"] == "CAS"
        await c.close()

    @pytest.mark.asyncio
    async def test_search_error(self):
        c = ScienceDBConnector()
        c._request_with_retry = AsyncMock(side_effect=Exception("fail"))
        result = await c.search("test")
        assert result.error == "fail"
        await c.close()


# ------------------------------------------------------------------
# CHINESE_CONNECTORS registry
# ------------------------------------------------------------------


class TestChineseConnectorsRegistry:

    def test_registry_has_three(self):
        assert len(CHINESE_CONNECTORS) == 3
        assert "ngdc" in CHINESE_CONNECTORS
        assert "cnki" in CHINESE_CONNECTORS
        assert "scidb" in CHINESE_CONNECTORS

    def test_registry_classes(self):
        assert CHINESE_CONNECTORS["ngdc"] is NGDCConnector
        assert CHINESE_CONNECTORS["cnki"] is CNKIConnector
        assert CHINESE_CONNECTORS["scidb"] is ScienceDBConnector


# ------------------------------------------------------------------
# MirrorRouter smart routing (F-20)
# ------------------------------------------------------------------


class TestMirrorRouterSmartRouting:

    def test_auto_switch_default_off(self):
        mr = MirrorRouter()
        assert mr._auto_switch is False

    def test_enable_auto_switch(self):
        mr = MirrorRouter()
        mr.enable_auto_switch(True)
        assert mr._auto_switch is True
        mr.enable_auto_switch(False)
        assert mr._auto_switch is False

    @pytest.mark.asyncio
    async def test_test_latency_success(self):
        mr = MirrorRouter()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("fusion_science.database.mirror.time.time", return_value=100.0):
            results = await mr.test_latency("pubmed")
        assert "primary" in results
        assert "mirror" in results
        assert results["primary"] >= 0

    @pytest.mark.asyncio
    async def test_test_latency_unreachable(self):
        mr = MirrorRouter()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await mr.test_latency("pubmed")
        assert results["primary"] == -1.0

    def test_smart_get_url_no_latency(self):
        mr = MirrorRouter()
        url = mr.smart_get_url("pubmed")
        assert url

    def test_smart_get_url_with_latency_prefers_faster(self):
        mr = MirrorRouter()
        mr.enable_auto_switch(True)
        mr._latency_cache["pubmed"] = {"primary": 2.5, "mirror": 0.3}
        url = mr.smart_get_url("pubmed")
        endpoint = mr.get_endpoint("pubmed")
        assert url == endpoint.mirror_url

    def test_smart_get_url_prefers_primary_if_faster(self):
        mr = MirrorRouter()
        mr.enable_auto_switch(True)
        mr._latency_cache["pubmed"] = {"primary": 0.3, "mirror": 2.5}
        url = mr.smart_get_url("pubmed")
        endpoint = mr.get_endpoint("pubmed")
        assert url == endpoint.primary_url

    def test_smart_get_url_mirror_unreachable(self):
        mr = MirrorRouter()
        mr.enable_auto_switch(True)
        mr._latency_cache["pubmed"] = {"primary": 0.5, "mirror": -1.0}
        url = mr.smart_get_url("pubmed")
        endpoint = mr.get_endpoint("pubmed")
        assert url == endpoint.primary_url

    def test_smart_get_url_primary_unreachable(self):
        mr = MirrorRouter()
        mr.enable_auto_switch(True)
        mr._latency_cache["pubmed"] = {"primary": -1.0, "mirror": 0.5}
        url = mr.smart_get_url("pubmed")
        endpoint = mr.get_endpoint("pubmed")
        assert url == endpoint.mirror_url

    def test_get_latency_results(self):
        mr = MirrorRouter()
        mr._latency_cache = {"pubmed": {"primary": 1.0, "mirror": 0.5}}
        results = mr.get_latency_results()
        assert "pubmed" in results

    def test_status_report_includes_auto_switch(self):
        mr = MirrorRouter()
        mr.enable_auto_switch(True)
        report = mr.get_status_report()
        assert report["auto_switch"] is True


# ------------------------------------------------------------------
# API routes
# ------------------------------------------------------------------


class TestAPIRoutesPhase12:

    @pytest.fixture
    def client(self):
        from httpx import ASGITransport, AsyncClient

        from fusion_science.api.app import create_app
        from fusion_science.config import ScienceConfig
        from fusion_science.core.gateway import LLMGateway
        from fusion_science.session import MemorySessionStore, SessionManager

        app = create_app()
        config = ScienceConfig()
        app.state.config = config
        app.state.gateway = LLMGateway(config)
        app.state.session_manager = SessionManager(MemorySessionStore())
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_databases_list_includes_chinese(self, client):
        resp = await client.get("/api/v1/databases")
        data = resp.json()
        names = [d["name"] for d in data["databases"]]
        assert "ngdc" in names
        assert "cnki" in names
        assert "scidb" in names

    @pytest.mark.asyncio
    async def test_database_status_ngdc(self, client):
        resp = await client.get("/api/v1/databases/ngdc/status")
        data = resp.json()
        assert data["name"] == "ngdc"

    @pytest.mark.asyncio
    async def test_database_status_cnki(self, client):
        resp = await client.get("/api/v1/databases/cnki/status")
        data = resp.json()
        assert data["name"] == "cnki"

    @pytest.mark.asyncio
    async def test_database_status_scidb(self, client):
        resp = await client.get("/api/v1/databases/scidb/status")
        data = resp.json()
        assert data["name"] == "scidb"

    @pytest.mark.asyncio
    async def test_mirror_status_endpoint(self, client):
        resp = await client.get("/api/v1/system/mirrors/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "mirror_count" in data

    @pytest.mark.asyncio
    async def test_mirror_auto_switch_endpoint(self, client):
        resp = await client.post("/api/v1/system/mirrors/auto-switch", params={"enable": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_switch"] is True
