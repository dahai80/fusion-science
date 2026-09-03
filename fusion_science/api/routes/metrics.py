from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Request

router = APIRouter()


@dataclass
class _Metrics:
    # F-O2: minimal in-process counters. Single-process, consistent with the
    # single-worker model. Exposed at /metrics in Prometheus text exposition.
    requests: int = 0
    errors: int = 0
    llm_calls: int = 0
    llm_failures: int = 0
    total_latency: float = 0.0
    started: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_request(self, latency: float, is_error: bool) -> None:
        with self._lock:
            self.requests += 1
            self.total_latency += latency
            if is_error:
                self.errors += 1

    def record_llm(self, failed: bool) -> None:
        with self._lock:
            self.llm_calls += 1
            if failed:
                self.llm_failures += 1

    def snapshot(self) -> dict:
        with self._lock:
            avg = (self.total_latency / self.requests) if self.requests else 0.0
            uptime = time.time() - self.started
            return {
                "requests": self.requests,
                "errors": self.errors,
                "llm_calls": self.llm_calls,
                "llm_failures": self.llm_failures,
                "avg_latency": round(avg, 4),
                "uptime_seconds": round(uptime, 1),
            }


_metrics = _Metrics()


def get_metrics() -> _Metrics:
    return _metrics


@router.get("/metrics")
async def metrics(request: Request) -> dict:
    snap = _metrics.snapshot()
    return {
        "status": "ok",
        "counters": snap,
    }


_PROM_NAMES = {
    "requests": "fusion_science_requests_total",
    "errors": "fusion_science_errors_total",
    "llm_calls": "fusion_science_llm_calls_total",
    "llm_failures": "fusion_science_llm_failures_total",
    "avg_latency": "fusion_science_avg_latency_seconds",
    "uptime_seconds": "fusion_science_uptime_seconds",
}


@router.get("/metrics/prometheus")
async def metrics_prometheus(request: Request) -> str:
    snap = _metrics.snapshot()
    lines = []
    for human, prom in _PROM_NAMES.items():
        lines.append(f"# TYPE {prom} gauge" if human in ("avg_latency", "uptime_seconds") else f"# TYPE {prom} counter")
        lines.append(f"{prom} {snap[human]}")
    return "\n".join(lines) + "\n"
