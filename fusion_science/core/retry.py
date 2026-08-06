from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RetryStats:
    total_attempts: int = 0
    successful: int = 0
    failed: int = 0
    retried: int = 0
    last_error: str = ""
    last_attempt_time: float = 0.0
    connection_state: str = "unknown"


class ConnectionMonitor:
    def __init__(
        self,
        health_check: Any = None,
        check_interval: float = 30.0,
        max_failures: int = 3,
    ):
        self._health_check = health_check
        self._check_interval = check_interval
        self._max_failures = max_failures
        self._consecutive_failures = 0
        self._is_connected = True
        self._last_check_time = 0.0
        self._stats = RetryStats()
        self._monitor_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def stats(self) -> RetryStats:
        return self._stats

    async def start_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Connection monitor started")

    async def stop_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
        logger.info("Connection monitor stopped")

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._check_interval)
                await self.check_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Monitor loop error: %s", e)

    async def check_health(self) -> bool:
        if self._health_check is None:
            return True
        try:
            healthy = await self._health_check()
            if healthy:
                self._consecutive_failures = 0
                if not self._is_connected:
                    logger.info("Connection restored")
                self._is_connected = True
                self._stats.connection_state = "connected"
            else:
                self._on_failure("health check returned false")
        except Exception as e:
            self._on_failure(str(e))
        self._last_check_time = time.time()
        return self._is_connected

    def _on_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        self._stats.last_error = reason
        if self._consecutive_failures >= self._max_failures:
            self._is_connected = False
            self._stats.connection_state = "disconnected"
            logger.warning(
                "Connection lost after %d failures: %s",
                self._consecutive_failures,
                reason,
            )
        else:
            self._stats.connection_state = "reconnecting"

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._is_connected = True
        self._stats.connection_state = "connected"
        self._stats.successful += 1
        self._stats.total_attempts += 1
        self._stats.last_attempt_time = time.time()

    def record_failure(self, error: str) -> None:
        self._stats.failed += 1
        self._stats.total_attempts += 1
        self._stats.last_error = error
        self._stats.last_attempt_time = time.time()
        self._on_failure(error)


async def retry_with_backoff(
    fn: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Any = None,
) -> Any:
    import random

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            result = await fn()
            return result
        except retryable_exceptions as e:
            last_error = e
            if attempt < max_retries:
                delay = min(base_delay * (exponential_base**attempt), max_delay)
                if jitter:
                    delay *= 0.5 + random.random()
                logger.warning(
                    "Retry %d/%d after %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    delay,
                    e,
                )
                if on_retry:
                    on_retry(attempt + 1, e)
                await asyncio.sleep(delay)
    if last_error:
        raise last_error
    raise RuntimeError("retry_with_backoff: unexpected state")
