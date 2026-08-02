# core/__init__.py — re-exports agent system
# Importers: api/app.py imports QueryRouterAgent from core.agents

from __future__ import annotations

from .agents import DataAgent, ErrorAnalysisAgent, LiteratureAgent, QueryRouterAgent, VizAgent, WriterAgent

__all__ = [
    "QueryRouterAgent",
    "LiteratureAgent",
    "DataAgent",
    "VizAgent",
    "WriterAgent",
    "ErrorAnalysisAgent",
]
