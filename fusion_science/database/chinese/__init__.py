from __future__ import annotations

from .cnki import CNKIConnector
from .ngdc import NGDCConnector
from .scidb import ScienceDBConnector

CHINESE_CONNECTORS: dict[str, type] = {
    "ngdc": NGDCConnector,
    "cnki": CNKIConnector,
    "scidb": ScienceDBConnector,
}

__all__ = [
    "CHINESE_CONNECTORS",
    "CNKIConnector",
    "NGDCConnector",
    "ScienceDBConnector",
]
