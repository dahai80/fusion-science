"""Base class for scientific database connectors.

All database connectors follow a common interface with:
- Async search/lookup methods
- Response normalization into a unified schema
- Domestic mirror fallback support
- Request retry & rate limiting
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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
        self._semaphore = asyncio.Semaphore(1)
        self._last_request_time: float = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}  # key -> (timestamp, data)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            base = self.config.mirror_url if self.config.use_mirror else self.config.base_url
            self._client = httpx.AsyncClient(
                base_url=base,
                timeout=self.config.timeout,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @abstractmethod
    async def search(self, query: str, **kwargs) -> DatabaseResult:
        """Search the database with a query string."""
        ...

    @abstractmethod
    async def fetch(self, identifier: str, **kwargs) -> DatabaseResult:
        """Fetch a record by its identifier (accession, PMID, PDB ID, etc.)."""
        ...

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        import time
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
            httpx.HTTPError: After all retries are exhausted.
        """
        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                await self._rate_limit()
                resp = await self.client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 503):
                    wait = 2 ** attempt
                    logger.warning("Rate limited, retrying in %ds (attempt %d/%d)", wait, attempt + 1, self.config.max_retries)
                    await asyncio.sleep(wait)
                    last_error = e
                else:
                    raise
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                logger.warning("Request failed: %s, retrying (attempt %d/%d)", e, attempt + 1, self.config.max_retries)
                await asyncio.sleep(2 ** attempt)
                last_error = e

        raise last_error or httpx.HTTPError("Request failed after retries")

    def _check_cache(self, key: str) -> Any | None:
        """Check if a cached result exists and is still valid."""
        import time
        if not self.config.cache_enabled:
            return None
        entry = self._cache.get(key)
        if entry:
            timestamp, data = entry
            if time.time() - timestamp < self.config.cache_ttl:
                return data
            del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any) -> None:
        """Cache a result."""
        if self.config.cache_enabled:
            import time
            self._cache[key] = (time.time(), data)

    def clear_cache(self) -> None:
        """Clear all cached results."""
        self._cache.clear()

    @staticmethod
    def safe_text(value: Any) -> str:
        """Safely convert a value to text, handling None."""
        if value is None:
            return ""
        return str(value).strip()