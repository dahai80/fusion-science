from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Artifact:
    id: str = ""
    type: str = ""
    name: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "content": self.content,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass
class ResearchContext:
    papers: list[dict] = field(default_factory=list)
    datasets: list[dict] = field(default_factory=list)
    code_history: list[dict] = field(default_factory=list)
    figures: list[dict] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchSession:
    id: str = ""
    title: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[dict] = field(default_factory=list)
    context: ResearchContext = field(default_factory=ResearchContext)
    artifacts: list[Artifact] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "context": {
                "papers": self.context.papers,
                "datasets": self.context.datasets,
                "code_history": self.context.code_history,
                "figures": self.context.figures,
                "variables": self.context.variables,
            },
            "artifacts": [
                {
                    "id": a.id,
                    "type": a.type,
                    "name": a.name,
                    "content": a.content,
                    "metadata": a.metadata,
                    "created_at": a.created_at,
                }
                for a in self.artifacts
            ],
            "trace_ids": self.trace_ids,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ResearchSession:
        ctx_data = data.get("context", {})
        context = ResearchContext(
            papers=ctx_data.get("papers", []),
            datasets=ctx_data.get("datasets", []),
            code_history=ctx_data.get("code_history", []),
            figures=ctx_data.get("figures", []),
            variables=ctx_data.get("variables", {}),
        )
        artifacts = [
            Artifact(
                id=a.get("id", ""),
                type=a.get("type", ""),
                name=a.get("name", ""),
                content=a.get("content", ""),
                metadata=a.get("metadata", {}),
                created_at=a.get("created_at", 0.0),
            )
            for a in data.get("artifacts", [])
        ]
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            messages=data.get("messages", []),
            context=context,
            artifacts=artifacts,
            trace_ids=data.get("trace_ids", []),
        )
