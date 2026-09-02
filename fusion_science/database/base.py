"""Base class for scientific database connectors.

All database connectors follow a common interface with:
- Async search/lookup methods
- Response normalization into a unified schema
- Domestic mirror fallback support
- Request retry & rate limiting
- Offline mode support (skip network when FUSION_OFFLINE_MODE=true)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Maximum in-memory cache entries per connector (LRU eviction)
_MAX_CACHE_SIZE = 200
# Maximum total cached bytes per connector (bounds memory growth)
_MAX_CACHE_BYTES = 64 * 1024 * 1024
# Follow-redirect cap (defense in depth against open-redirect loops)
_MAX_REDIRECTS = 3


@dataclass
class DatabaseResult:
    """Normalized result from any scientific database query."""

    source: str  # e.g., "pubmed", "uniprot", "pdb"
    query: str
    items: list[dict] = field(default_factory=list)
    total_count: int = 0
    error: str = ""


@dataclass
class ConnectorConfig:
    """Configuration for a database connector."""

    base_url: str = ""
    mirror_url: str = ""  # Domestic mirror URL for China
    use_mirror: bool = False
    offline_mode: bool = False  # Block overseas requests when True
    timeout: float = 30.0
    max_retries: int = 3
    rate_limit: float = 0.5  # Seconds between requests
    cache_enabled: bool = True
    cache_ttl: int = 3600  # Cache TTL in seconds


class BaseConnector(ABC):
    """Abstract base class for all scientific database connectors."""

    def __init__(self, config: ConnectorConfig | None = None):
        self.config = config or ConnectorConfig()
        self._client: httpx.AsyncClient | None = None
        # A-3: lock only guards the rate-limit timestamp, NOT the whole request —
        # Semaphore(1) serialized every DB call across the connector.
        self._rate_lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        # LRU-limited cache: OrderedDict for O(1) move-to-end eviction
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._cache_bytes: int = 0

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client.

        Raises RuntimeError if offline_mode is enabled.
        """
        if self.config.offline_mode:
            raise RuntimeError(
                f"离线模式已启用: {self.__class__.__name__} 无法发起网络请求。"
                "请设置 FUSION_OFFLINE_MODE=false 或直接传入离线数据。"
            )
        if self._client is None:
            base = self.config.mirror_url if self.config.use_mirror else self.config.base_url
            self._client = httpx.AsyncClient(
                base_url=base,
                timeout=self.config.timeout,
                follow_redirects=True,
                max_redirects=_MAX_REDIRECTS,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self.clear_cache()

    @abstractmethod
    async def search(self, query: str, **kwargs) -> DatabaseResult:
        """Search the database with a query string."""
        ...

    @abstractmethod
    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        """Fetch a record by its identifier (accession, PMID, PDB ID, etc.)."""
        ...

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests (timestamp guarded by a lock)."""
        async with self._rate_lock:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.config.rate_limit:
                await asyncio.sleep(self.config.rate_limit - elapsed)
            self._last_request_time = time.time()

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """Make an HTTP request with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Request URL (relative to base_url).
            **kwargs: Additional request parameters.

        Returns:
            httpx.Response on success.

        Raises:
            RuntimeError: If offline_mode is enabled.
            httpx.HTTPError: After all retries are exhausted.
        """
        # Fail fast in offline mode
        if self.config.offline_mode:
            raise RuntimeError(f"离线模式已启用: 无法请求 {url}。请设置 FUSION_OFFLINE_MODE=false 以启用网络请求。")

        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                await self._rate_limit()
                resp = await self.client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 503):
                    # P-2: honor Retry-After header, fall back to exp backoff + jitter
                    retry_after = e.response.headers.get("Retry-After", "")
                    if retry_after and retry_after.isdigit():
                        wait = min(int(retry_after), 60)
                    else:
                        wait = min(2**attempt, 30)
                    wait += random.uniform(0, 1)  # jitter to de-synchronize retries
                    logger.warning(
                        "Rate limited, retrying in %.1fs (attempt %d/%d)", wait, attempt + 1, self.config.max_retries
                    )
                    await asyncio.sleep(wait)
                    last_error = e
                else:
                    raise
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                wait = min(2**attempt, 30) + random.uniform(0, 1)
                logger.warning(
                    "Request failed: %s, retrying in %.1fs (attempt %d/%d)",
                    e,
                    wait,
                    attempt + 1,
                    self.config.max_retries,
                )
                await asyncio.sleep(wait)
                last_error = e

        raise last_error or httpx.HTTPError("Request failed after retries")

    @staticmethod
    def _cache_key(key: str) -> str:
        """Hash long cache keys to bound memory (collisions acceptable for a cache)."""
        if len(key) <= 64:
            return key
        return hashlib.sha1(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _approx_size(data: Any) -> int:
        """Approximate byte size of a cached value."""
        try:
            return len(data)
        except TypeError:
            import sys

            return sys.getsizeof(data, 0)

    def _check_cache(self, key: str) -> Any | None:
        """Check if a cached result exists and is still valid (LRU-aware)."""
        if not self.config.cache_enabled:
            return None
        ckey = self._cache_key(key)
        entry = self._cache.get(ckey)
        if entry:
            timestamp, data = entry
            if time.time() - timestamp < self.config.cache_ttl:
                # Move to end (most recently used) for LRU tracking
                self._cache.move_to_end(ckey)
                return data
            # Expired — evict and account for freed bytes
            self._cache_bytes -= self._approx_size(data)
            if self._cache_bytes < 0:
                self._cache_bytes = 0
            del self._cache[ckey]
        return None

    def _set_cache(self, key: str, data: Any) -> None:
        """Cache a result with LRU eviction (entry + byte budget)."""
        if not self.config.cache_enabled:
            return
        ckey = self._cache_key(key)
        size = self._approx_size(data)
        # Evict by entry count
        while len(self._cache) >= _MAX_CACHE_SIZE and self._cache:
            _evict_key, (_ts, evict_data) = self._cache.popitem(last=False)
            self._cache_bytes -= self._approx_size(evict_data)
        # Evict by byte budget
        while self._cache_bytes + size > _MAX_CACHE_BYTES and self._cache:
            _evict_key, (_ts, evict_data) = self._cache.popitem(last=False)
            self._cache_bytes -= self._approx_size(evict_data)
        # If a single value exceeds the byte cap, skip caching it rather than
        # storing an oversized entry.
        if size > _MAX_CACHE_BYTES:
            logger.debug("Skipping cache: value size %d exceeds cap %d", size, _MAX_CACHE_BYTES)
            return
        self._cache[ckey] = (time.time(), data)
        self._cache_bytes += size
        self._cache.move_to_end(ckey)  # Mark as most recently used

    def clear_cache(self) -> None:
        """Clear all cached results."""
        self._cache.clear()
        self._cache_bytes = 0

    @staticmethod
    def safe_text(value: Any) -> str:
        """Safely convert a value to text, handling None."""
        if value is None:
            return ""
        return str(value).strip()
