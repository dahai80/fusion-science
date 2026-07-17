# Fusion-Science API Reference

## Overview

Fusion-Science is organized into six main modules:

- **core** — MLX inference engine, agent runtime, pipeline orchestrator
- **database** — Scientific database connectors with domestic mirror support
- **compute** — Code execution (Python, R, Jupyter) and HPC scheduling
- **visualization** — Charts, 3D molecules, protein structures
- **literature** — Search, review, and paper generation
- **audit** — Trace tracking, provenance, and reproducibility reports

## Core Module (`fusion_science.core`)

### `ScienceEngine`

The main interface for local LLM inference. Supports both HTTP mode (connecting to fusion-mlx server) and direct MLX mode.

```python
from fusion_science.core.engine import ScienceEngine, ModelConfig

# HTTP mode (default)
engine = ScienceEngine(ModelConfig(
    name="qwen3.5-9b",
    base_url="http://localhost:8000/v1",
))

# Direct MLX mode
await engine.load_model("mlx-community/Qwen3.5-9B")
```

### `ScienceAgent`

A single research agent with tool-use capability.

```python
from fusion_science.core.agent import ScienceAgent

agent = ScienceAgent(
    name="literature_search",
    engine=engine,
    system_prompt="You are a literature search specialist.",
    tools=[...],
)
result = await agent.run("Search for CRISPR papers")
```

### `SciencePipeline`

Orchestrates multi-step scientific workflows.

```python
from fusion_science.core.pipeline import SciencePipeline, PipelineFactory

# Pre-built pipeline
factory = PipelineFactory(engine)
pipeline = factory.create_pipeline("literature_review")
result = await pipeline.sequential(["agent1", "agent2"], "Research question")
```

## Database Module (`fusion_science.database`)

### Connectors

All connectors inherit from `BaseConnector` and support the same interface:

```python
from fusion_science.database.pubmed import PubMedConnector
from fusion_science.database.uniprot import UniProtConnector
from fusion_science.database.pdb import PDBConnector
from fusion_science.database.ensembl import EnsemblConnector
from fusion_science.database.chembl import ChEMBLConnector

# Search
result = await connector.search("query", max_results=20)

# Fetch by ID
result = await connector.fetch("P04637")  # UniProt accession
```

### Cache & Mirror

```python
from fusion_science.database.mirror import ScienceCache, MirrorRouter

cache = ScienceCache()
cache.set("key", data, source="pubmed")
data = cache.get("key")

router = MirrorRouter()
router.enable_mirrors(True)
url = router.get_url("pubmed")
```

## Compute Module (`fusion_science.compute`)

### PythonExecutor

```python
from fusion_science.compute.python_executor import PythonExecutor

executor = PythonExecutor(timeout=120)
result = await executor.execute("print('Hello')")
```

### JupyterKernelManager

```python
from fusion_science.compute.jupyter_kernel import JupyterKernelManager

km = JupyterKernelManager()
await km.start_kernel("python3")
result = await km.execute("import numpy as np")
await km.shutdown()
```

### HPCScheduler

```python
from fusion_science.compute.hpc_scheduler import HPCScheduler

scheduler = HPCScheduler()
job = await scheduler.submit_job(script_content, job_name="analysis")
status = await scheduler.check_status(job.job_id)
```

## Visualization Module (`fusion_science.visualization`)

### ChartGenerator

```python
from fusion_science.visualization.chart import ChartGenerator, ChartConfig

chart = ChartGenerator()
result = await chart.bar_chart(["A", "B"], [10, 20])
result = await chart.volcano_plot(log2fc, pvalues)
```

### MoleculeVisualizer

```python
from fusion_science.visualization.molecule import MoleculeVisualizer

viz = MoleculeVisualizer()
result = await viz.from_smiles("CC(=O)OC1=CC=CC=C1C(=O)O")  # Aspirin
result = await viz.from_pdb("6M0J")  # SARS-CoV-2 spike
```

### ProteinVisualizer

```python
from fusion_science.visualization.protein import ProteinVisualizer

viz = ProteinVisualizer()
result = await viz.visualize("6M0J", style="cartoon")
```

## Literature Module (`fusion_science.literature`)

### LiteratureSearch

```python
from fusion_science.literature.search import LiteratureSearch

searcher = LiteratureSearch()
result = await searcher.search("CRISPR-Cas9", max_results=20)
```

### LiteratureReviewer

```python
from fusion_science.literature.review import LiteratureReviewer

reviewer = LiteratureReviewer()
review = await reviewer.analyze_papers(papers, "research question")
```

### PaperGenerator

```python
from fusion_science.literature.paper import PaperGenerator

gen = PaperGenerator()
paper = await gen.create_paper("My Study")
await gen.write_section(paper, 0, "Context...")
```

## Audit Module (`fusion_science.audit`)

### TraceRecorder

```python
from fusion_science.audit.tracker import TraceRecorder

recorder = TraceRecorder()
recorder.start_session()
recorder.record_db_query("module", "pubmed", "cancer", 10)
session = recorder.end_session()
```

### ProvenanceTracker

```python
from fusion_science.audit.provenance import ProvenanceTracker

tracker = ProvenanceTracker()
tracker.start_tracking("experiment")
src = tracker.add_source("Database query", "db_query")
tx = tracker.add_transformation("Analysis", [src])
```

### ReportGenerator

```python
from fusion_science.audit.report import ReportGenerator

gen = ReportGenerator(trace_recorder, provenance_tracker)
report = gen.generate_audit_report()
package = gen.export_package("./output")
```