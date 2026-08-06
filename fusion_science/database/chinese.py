from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .base import BaseConnector, ConnectorConfig, DatabaseResult

logger = logging.getLogger(__name__)


@dataclass
class ChineseDBResult:
    database: str
    query: str
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "query": self.query,
            "items": self.items,
            "total": self.total,
            "error": self.error,
        }


class NGDCConnector(BaseConnector):
    _BASE_URL = "https://ngdc.cncb.ac.cn"

    def __init__(self, config: ConnectorConfig | None = None):
        cfg = config or ConnectorConfig(
            base_url=self._BASE_URL,
            mirror_url="https://ngdc.cncb.ac.cn",
        )
        super().__init__(cfg)
        logger.info("NGDCConnector initialized")

    async def search(self, query: str, **kwargs: Any) -> DatabaseResult:
        logger.info("NGDC search: %r", query[:80])
        try:
            url = f"{self.config.base_url}/api/search"
            params = {"q": query, "page": kwargs.get("page", 1), "size": kwargs.get("size", 20)}
            resp = await self._request_with_retry("GET", url, params=params)
            data = resp.json() if hasattr(resp, "json") else resp
            items = self._parse_ngdc_response(data)
            logger.info("NGDC search returned %d items", len(items))
            return DatabaseResult(source="ngdc", query=query, items=items, total_count=len(items))
        except Exception as e:
            logger.error("NGDC search error: %s", e)
            return DatabaseResult(source="ngdc", query=query, error=str(e))

    async def fetch(self, record_id: str, **kwargs: Any) -> DatabaseResult:
        logger.info("NGDC fetch: %s", record_id)
        try:
            url = f"{self.config.base_url}/api/record/{record_id}"
            resp = await self._request_with_retry("GET", url)
            data = resp.json() if hasattr(resp, "json") else resp
            items = [data] if isinstance(data, dict) else []
            return DatabaseResult(source="ngdc", query=record_id, items=items, total_count=len(items))
        except Exception as e:
            logger.error("NGDC fetch error: %s", e)
            return DatabaseResult(source="ngdc", query=record_id, error=str(e))

    @staticmethod
    def _parse_ngdc_response(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            raw_items = data.get("data", data.get("items", data.get("results", [])))
            if isinstance(raw_items, list):
                return raw_items
        if isinstance(data, list):
            return data
        return []


class CNKIConnector(BaseConnector):
    _BASE_URL = "https://www.cnki.net"

    def __init__(self, config: ConnectorConfig | None = None):
        cfg = config or ConnectorConfig(
            base_url=self._BASE_URL,
            mirror_url="https://www.cnki.net",
        )
        super().__init__(cfg)
        logger.info("CNKIConnector initialized")

    async def search(self, query: str, **kwargs: Any) -> DatabaseResult:
        logger.info("CNKI search: %r", query[:80])
        try:
            url = f"{self.config.base_url}/kns/brief/default_result.aspx"
            params = {
                "txt_1_sel": "SU$%=|",
                "txt_1_value1": query,
                "dbPrefix": kwargs.get("db_prefix", "CJFQ"),
                "page": kwargs.get("page", 1),
            }
            resp = await self._request_with_retry("GET", url, params=params)
            data = resp.json() if hasattr(resp, "json") else resp
            items = self._parse_cnki_response(data)
            logger.info("CNKI search returned %d items", len(items))
            return DatabaseResult(source="cnki", query=query, items=items, total_count=len(items))
        except Exception as e:
            logger.error("CNKI search error: %s", e)
            return DatabaseResult(source="cnki", query=query, error=str(e))

    async def fetch(self, record_id: str, **kwargs: Any) -> DatabaseResult:
        logger.info("CNKI fetch: %s", record_id)
        try:
            url = f"{self.config.base_url}/kcms/detail/detail.aspx"
            params = {"dbcode": kwargs.get("dbcode", "CJFQ"), "filename": record_id}
            resp = await self._request_with_retry("GET", url, params=params)
            data = resp.json() if hasattr(resp, "json") else resp
            items = [data] if isinstance(data, dict) else []
            return DatabaseResult(source="cnki", query=record_id, items=items, total_count=len(items))
        except Exception as e:
            logger.error("CNKI fetch error: %s", e)
            return DatabaseResult(source="cnki", query=record_id, error=str(e))

    @staticmethod
    def _parse_cnki_response(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            raw_items = data.get("data", data.get("items", data.get("results", [])))
            if isinstance(raw_items, list):
                return raw_items
        if isinstance(data, list):
            return data
        return []


class ScienceDBConnector(BaseConnector):
    _BASE_URL = "https://www.scidb.cn"

    def __init__(self, config: ConnectorConfig | None = None):
        cfg = config or ConnectorConfig(
            base_url=self._BASE_URL,
            mirror_url="https://www.scidb.cn",
        )
        super().__init__(cfg)
        logger.info("ScienceDBConnector initialized")

    async def search(self, query: str, **kwargs: Any) -> DatabaseResult:
        logger.info("ScienceDB search: %r", query[:80])
        try:
            url = f"{self.config.base_url}/api/search"
            params = {"q": query, "page": kwargs.get("page", 1), "per_page": kwargs.get("size", 20)}
            resp = await self._request_with_retry("GET", url, params=params)
            data = resp.json() if hasattr(resp, "json") else resp
            items = self._parse_scidb_response(data)
            logger.info("ScienceDB search returned %d items", len(items))
            return DatabaseResult(source="sciencedb", query=query, items=items, total_count=len(items))
        except Exception as e:
            logger.error("ScienceDB search error: %s", e)
            return DatabaseResult(source="sciencedb", query=query, error=str(e))

    async def fetch(self, record_id: str, **kwargs: Any) -> DatabaseResult:
        logger.info("ScienceDB fetch: %s", record_id)
        try:
            url = f"{self.config.base_url}/api/dataset/{record_id}"
            resp = await self._request_with_retry("GET", url)
            data = resp.json() if hasattr(resp, "json") else resp
            items = [data] if isinstance(data, dict) else []
            return DatabaseResult(source="sciencedb", query=record_id, items=items, total_count=len(items))
        except Exception as e:
            logger.error("ScienceDB fetch error: %s", e)
            return DatabaseResult(source="sciencedb", query=record_id, error=str(e))

    @staticmethod
    def _parse_scidb_response(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            raw_items = data.get("data", data.get("items", data.get("results", [])))
            if isinstance(raw_items, list):
                return raw_items
        if isinstance(data, list):
            return data
        return []


class MirrorRouter:
    """Smart mirror router with connectivity probe and fallback."""

    def __init__(self, cache: Any | None = None):
        self._cache = cache
        self._use_mirrors: bool = False
        self._offline_mode: bool = self._detect_offline()
        logger.info("MirrorRouter init: offline=%s", self._offline_mode)

    @staticmethod
    def _detect_offline() -> bool:
        import os

        return os.getenv("FUSION_OFFLINE_MODE", "").lower() in ("true", "1", "yes")

    def enable_mirrors(self, enabled: bool = True) -> None:
        self._use_mirrors = enabled
        logger.info("Mirror routing %s", "enabled" if enabled else "disabled")

    def get_url(self, db_name: str) -> str:
        from .mirror import DOMESTIC_MIRRORS

        endpoint = DOMESTIC_MIRRORS.get(db_name)
        if endpoint is None:
            return ""
        if (self._use_mirrors or self._offline_mode) and endpoint.mirror_url:
            return endpoint.mirror_url
        return endpoint.primary_url

    def is_offline(self) -> bool:
        return self._offline_mode

    def list_mirrors(self) -> list[dict[str, Any]]:
        from .mirror import DOMESTIC_MIRRORS

        return [
            {
                "name": m.name,
                "db_key": key,
                "primary_url": m.primary_url,
                "mirror_url": m.mirror_url,
                "enabled": m.enabled,
            }
            for key, m in DOMESTIC_MIRRORS.items()
        ]
