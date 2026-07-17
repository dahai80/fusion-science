"""Tests for the database connector modules."""

from __future__ import annotations

import pytest

from fusion_science.database.base import BaseConnector, ConnectorConfig, DatabaseResult
from fusion_science.database.mirror import ScienceCache, CacheConfig, MirrorRouter, MirrorEndpoint, DOMESTIC_MIRRORS, CHINESE_DATABASES


class TestConnectorConfig:
    """Test ConnectorConfig dataclass."""

    def test_default_config(self):
        config = ConnectorConfig()
        assert config.timeout == 30.0
        assert config.max_retries == 3
        assert config.rate_limit == 0.5
        assert config.cache_enabled is True

    def test_custom_config(self):
        config = ConnectorConfig(base_url="https://test.api", timeout=60.0)
        assert config.base_url == "https://test.api"
        assert config.timeout == 60.0


class TestDatabaseResult:
    """Test DatabaseResult dataclass."""

    def test_default_result(self):
        result = DatabaseResult(source="pubmed", query="cancer")
        assert result.source == "pubmed"
        assert result.query == "cancer"
        assert result.items == []
        assert result.total_count == 0
        assert result.error == ""

    def test_result_with_items(self):
        result = DatabaseResult(
            source="uniprot",
            query="TP53",
            items=[{"accession": "P04637", "name": "TP53"}],
            total_count=1,
        )
        assert len(result.items) == 1
        assert result.items[0]["accession"] == "P04637"


class TestScienceCache:
    """Test the offline cache system."""

    def test_cache_init(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), enabled=True)
        cache = ScienceCache(config)
        assert cache.config.enabled is True
        assert cache._conn is not None
        cache.close()

    def test_cache_set_get(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), enabled=True)
        cache = ScienceCache(config)
        cache.set("test_key", {"hello": "world"}, source="test")
        data = cache.get("test_key")
        assert data is not None
        assert data["hello"] == "world"
        cache.close()

    def test_cache_miss(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), enabled=True)
        cache = ScienceCache(config)
        data = cache.get("nonexistent_key")
        assert data is None
        cache.close()

    def test_cache_delete(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), enabled=True)
        cache = ScienceCache(config)
        cache.set("del_key", "data", source="test")
        cache.delete("del_key")
        assert cache.get("del_key") is None
        cache.close()

    def test_cache_clear(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), enabled=True)
        cache = ScienceCache(config)
        cache.set("key1", "data1", source="src1")
        cache.set("key2", "data2", source="src2")
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        cache.close()

    def test_cache_clear_by_source(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), enabled=True)
        cache = ScienceCache(config)
        cache.set("key1", "data1", source="pubmed")
        cache.set("key2", "data2", source="uniprot")
        cache.clear(source="pubmed")
        assert cache.get("key1") is None
        assert cache.get("key2") is not None  # uniprot entry should remain
        cache.close()

    def test_cache_disabled(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), enabled=False)
        cache = ScienceCache(config)
        assert cache.get("any_key") is None
        cache.set("any_key", "data")
        assert cache.get("any_key") is None
        cache.close()

    def test_cache_stats(self, tmp_path):
        config = CacheConfig(cache_dir=str(tmp_path), enabled=True)
        cache = ScienceCache(config)
        cache.set("k1", "v1", source="a")
        stats = cache.stats()
        assert stats["enabled"] is True
        assert stats["total_entries"] >= 1
        cache.close()


class TestMirrorRouter:
    """Test the mirror routing system."""

    def test_router_init(self):
        router = MirrorRouter()
        assert len(router.mirrors) > 0
        assert "pubmed" in router.mirrors

    def test_get_endpoint(self):
        router = MirrorRouter()
        endpoint = router.get_endpoint("pubmed")
        assert endpoint.name == "PubMed (E-utilities)"
        assert endpoint.primary_url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def test_get_url_primary(self):
        router = MirrorRouter()
        url = router.get_url("pubmed")
        assert url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def test_get_url_mirror(self):
        router = MirrorRouter()
        router.enable_mirrors(True)
        url = router.get_url("pubmed")
        assert url is not None

    def test_list_mirrors(self):
        router = MirrorRouter()
        mirrors = router.list_mirrors()
        assert len(mirrors) > 0
        assert all("name" in m for m in mirrors)
        assert all("db_key" in m for m in mirrors)

    def test_list_chinese_databases(self):
        router = MirrorRouter()
        databases = router.list_chinese_databases()
        assert len(databases) > 0
        names = [d["name"] for d in databases]
        assert "CNGB" in names
        assert "NGDC" in names


class TestDomesticMirrors:
    """Test the domestic mirror configuration."""

    def test_domestic_mirrors_defined(self):
        assert len(DOMESTIC_MIRRORS) >= 5
        assert "pubmed" in DOMESTIC_MIRRORS
        assert "uniprot" in DOMESTIC_MIRRORS
        assert "pdb" in DOMESTIC_MIRRORS
        assert "ensembl" in DOMESTIC_MIRRORS
        assert "chembl" in DOMESTIC_MIRRORS

    def test_chinese_databases_defined(self):
        assert len(CHINESE_DATABASES) >= 3
        assert "CNGB" in CHINESE_DATABASES
        assert "NGDC" in CHINESE_DATABASES
        assert "CNKI" in CHINESE_DATABASES