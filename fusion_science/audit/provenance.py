"""Data provenance tracking — trace the origin and transformation of every data point.

Provides a provenance model that records:
- Data sources (database queries, uploaded files, code output)
- Data transformations (code operations, filtering, normalization)
- Data lineage (parent-child relationships between data items)
- Reproducibility information (parameters, environment, versions)
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProvenanceNode:
    """A single data provenance node."""

    id: str
    type: str  # source, transformation, output
    label: str
    timestamp: float
    inputs: list[str] = field(default_factory=list)  # IDs of input nodes
    outputs: list[str] = field(default_factory=list)  # IDs of output nodes
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProvenanceGraph:
    """A directed graph of data provenance."""

    name: str
    created_at: float
    nodes: dict[str, ProvenanceNode] = field(default_factory=dict)
    description: str = ""


class ProvenanceTracker:
    """Tracks the provenance of data throughout the research workflow.

    Builds a directed graph showing how data flows from sources
    through transformations to final outputs, enabling full
    reproducibility and auditability.
    """

    def __init__(self, storage_dir: str = "~/.cache/fusion-science/provenance"):
        self.storage_dir = Path(storage_dir).expanduser()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._graph: ProvenanceGraph | None = None

    def start_tracking(self, name: str, description: str = "") -> str:
        """Start a new provenance tracking session.

        Args:
            name: Name for the provenance graph.
            description: Optional description.

        Returns:
            Graph ID.
        """
        self._graph = ProvenanceGraph(
            name=name,
            created_at=time.time(),
            description=description,
        )
        return name

    def add_source(
        self,
        label: str,
        source_type: str,
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a data source (database query, file upload, etc.).

        Args:
            label: Human-readable label.
            source_type: Type of source (db_query, file_upload, api_call).
            parameters: Parameters used to obtain the data.
            metadata: Additional metadata.

        Returns:
            Node ID.
        """
        node_id = f"src_{uuid.uuid4().hex[:8]}"
        node = ProvenanceNode(
            id=node_id,
            type="source",
            label=label,
            timestamp=time.time(),
            parameters=parameters or {},
            metadata=metadata or {},
        )
        self._add_node(node)
        return node_id

    def add_transformation(
        self,
        label: str,
        input_ids: list[str],
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a data transformation (code execution, filtering, etc.).

        Args:
            label: Human-readable label.
            input_ids: IDs of input data nodes.
            parameters: Transformation parameters (code, config, etc.).
            metadata: Additional metadata.

        Returns:
            Node ID.
        """
        node_id = f"tx_{uuid.uuid4().hex[:8]}"
        node = ProvenanceNode(
            id=node_id,
            type="transformation",
            label=label,
            timestamp=time.time(),
            inputs=input_ids,
            parameters=parameters or {},
            metadata=metadata or {},
        )
        self._add_node(node)
        return node_id

    def add_output(
        self,
        label: str,
        input_ids: list[str],
        output_type: str,
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a data output (figure, table, paper section, etc.).

        Args:
            label: Human-readable label.
            input_ids: IDs of input data/transformation nodes.
            output_type: Type of output (figure, table, text, etc.).
            parameters: Output generation parameters.
            metadata: Additional metadata.

        Returns:
            Node ID.
        """
        node_id = f"out_{uuid.uuid4().hex[:8]}"
        node = ProvenanceNode(
            id=node_id,
            type="output",
            label=label,
            timestamp=time.time(),
            inputs=input_ids,
            parameters=parameters or {},
            metadata={"output_type": output_type, **(metadata or {})},
        )
        self._add_node(node)
        return node_id

    def _add_node(self, node: ProvenanceNode) -> None:
        """Add a node to the provenance graph.

        Args:
            node: Node to add.
        """
        if self._graph is None:
            self.start_tracking("provenance")

        self._graph.nodes[node.id] = node

        # Update parent-child relationships
        for input_id in node.inputs:
            if input_id in self._graph.nodes:
                parent = self._graph.nodes[input_id]
                if node.id not in parent.outputs:
                    parent.outputs.append(node.id)

    def get_lineage(self, node_id: str) -> list[ProvenanceNode]:
        """Get the full lineage of a data item (all ancestors).

        Args:
            node_id: Target node ID.

        Returns:
            List of ancestor nodes in topological order.
        """
        if self._graph is None or node_id not in self._graph.nodes:
            return []

        visited: set[str] = set()
        lineage: list[ProvenanceNode] = []

        def dfs(nid: str) -> None:
            if nid in visited or nid not in self._graph.nodes:
                return
            visited.add(nid)
            node = self._graph.nodes[nid]
            for input_id in node.inputs:
                dfs(input_id)
            lineage.append(node)

        dfs(node_id)
        return lineage

    def get_downstream(self, node_id: str) -> list[ProvenanceNode]:
        """Get all downstream nodes that depend on a data item.

        Args:
            node_id: Target node ID.

        Returns:
            List of downstream nodes.
        """
        if self._graph is None or node_id not in self._graph.nodes:
            return []

        visited: set[str] = set()
        downstream: list[ProvenanceNode] = []

        def dfs(nid: str) -> None:
            if nid in visited or nid not in self._graph.nodes:
                return
            visited.add(nid)
            node = self._graph.nodes[nid]
            downstream.append(node)
            for output_id in node.outputs:
                dfs(output_id)

        dfs(node_id)
        return downstream

    def get_graph(self) -> ProvenanceGraph | None:
        """Get the current provenance graph.

        Returns:
            The current ProvenanceGraph or None.
        """
        return self._graph

    def export_json(self, pretty: bool = True) -> str:
        """Export the provenance graph as JSON.

        Args:
            pretty: Pretty-print the JSON.

        Returns:
            JSON string of the provenance graph.
        """
        if self._graph is None:
            return json.dumps({"error": "No provenance graph"})

        data = {
            "name": self._graph.name,
            "created_at": self._graph.created_at,
            "description": self._graph.description,
            "node_count": len(self._graph.nodes),
            "nodes": {
                nid: {
                    "id": node.id,
                    "type": node.type,
                    "label": node.label,
                    "timestamp": node.timestamp,
                    "inputs": node.inputs,
                    "outputs": node.outputs,
                    "parameters": node.parameters,
                    "metadata": node.metadata,
                }
                for nid, node in self._graph.nodes.items()
            },
        }
        indent = 2 if pretty else None
        return json.dumps(data, indent=indent, default=str, ensure_ascii=False)

    def save(self, name: str | None = None) -> str:
        """Save the provenance graph to disk.

        Args:
            name: Optional filename (default: graph name).

        Returns:
            Path to the saved file.
        """
        if self._graph is None:
            raise RuntimeError("No provenance graph to save.")

        filename = name or self._graph.name.replace(" ", "_").lower()
        if not filename.endswith(".json"):
            filename += ".json"
        save_path = self.storage_dir / filename

        with open(save_path, "w", encoding="utf-8") as f:
            f.write(self.export_json())

        logger.info("Saved provenance graph to %s", save_path)
        return str(save_path)

    def load(self, path: str | Path) -> bool:
        """Load a provenance graph from disk.

        Args:
            path: Path to the JSON file.

        Returns:
            True if loaded successfully.
        """
        try:
            with open(path, "r") as f:
                data = json.load(f)

            nodes = {}
            for nid, ndata in data.get("nodes", {}).items():
                nodes[nid] = ProvenanceNode(**ndata)

            self._graph = ProvenanceGraph(
                name=data.get("name", "loaded"),
                created_at=data.get("created_at", time.time()),
                nodes=nodes,
                description=data.get("description", ""),
            )
            return True
        except Exception as e:
            logger.error("Failed to load provenance graph: %s", e)
            return False

    @staticmethod
    def generate_reproducibility_report(
        provenance_json: str,
        include_parameters: bool = True,
    ) -> str:
        """Generate a human-readable reproducibility report from provenance data.

        Args:
            provenance_json: JSON string of provenance data.
            include_parameters: Include operation parameters in the report.

        Returns:
            Markdown-formatted reproducibility report.
        """
        try:
            data = json.loads(provenance_json)
        except json.JSONDecodeError:
            return "Error: Invalid provenance data."

        nodes = data.get("nodes", {})
        if not nodes:
            return "No provenance data available."

        report = [
            "# Reproducibility Report",
            "",
            f"**Project:** {data.get('name', 'Unnamed')}",
            f"**Node count:** {data.get('node_count', 0)}",
            f"**Created:** {datetime.fromtimestamp(data.get('created_at', 0))}",
            "",
            "## Data Sources",
            "",
        ]
        from datetime import datetime

        sources = [(nid, n) for nid, n in nodes.items() if n.get("type") == "source"]
        if sources:
            for nid, src in sources:
                report.append(f"- **{src.get('label', 'Source')}**")
                report.append(f"  - ID: `{nid}`")
                if include_parameters and src.get("parameters"):
                    report.append(f"  - Parameters: `{json.dumps(src['parameters'])}`")
                report.append("")
        else:
            report.append("No data sources recorded.")

        report.append("## Transformations")
        report.append("")

        transformations = [(nid, n) for nid, n in nodes.items() if n.get("type") == "transformation"]
        if transformations:
            for nid, tx in transformations:
                report.append(f"- **{tx.get('label', 'Transformation')}**")
                report.append(f"  - ID: `{nid}`")
                report.append(f"  - Inputs: {', '.join(tx.get('inputs', []))}")
                report.append(f"  - Outputs: {', '.join(tx.get('outputs', []))}")
                if include_parameters and tx.get("parameters"):
                    report.append(f"  - Parameters: `{json.dumps(tx['parameters'])}`")
                report.append("")
        else:
            report.append("No transformations recorded.")

        report.append("## Outputs")
        report.append("")

        outputs = [(nid, n) for nid, n in nodes.items() if n.get("type") == "output"]
        if outputs:
            for nid, out in outputs:
                report.append(f"- **{out.get('label', 'Output')}**")
                report.append(f"  - ID: `{nid}`")
                report.append(f"  - Type: {out.get('metadata', {}).get('output_type', 'Unknown')}")
                report.append(f"  - Derived from: {', '.join(out.get('inputs', []))}")
                report.append("")
        else:
            report.append("No outputs recorded.")

        report.append("## Lineage Examples")
        report.append("")

        if outputs:
            example = outputs[0]
            report.append(f"### Data lineage for '{example[1].get('label', 'Example output')}'")
            report.append("")
            inputs = example[1].get("inputs", [])
            for input_id in inputs:
                report.append(f"- `{input_id}` → {nodes.get(input_id, {}).get('label', 'Unknown')}")

        return "\n".join(report)