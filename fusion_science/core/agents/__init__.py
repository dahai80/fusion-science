# core/agents/__init__.py — Professional agent system (F-22)
# Importers: core/agents/router.py dispatches to all agents below
# API routes (api/routes/search.py, analysis.py, visualize.py, review.py) use QueryRouterAgent
# User instruction: "启动下一个阶段的任务实施"
# Parent spec: architecture/science-enhance.md Section 3.3.7

from .data import DataAgent
from .error import ErrorAnalysisAgent
from .literature import LiteratureAgent
from .router import QueryRouterAgent
from .visualize import VizAgent
from .writer import WriterAgent

__all__ = [
    "QueryRouterAgent",
    "LiteratureAgent",
    "DataAgent",
    "VizAgent",
    "WriterAgent",
    "ErrorAnalysisAgent",
]
