# Fusion-Science Architecture

## Overview

Fusion-Science is a modular, local-first scientific research AI workbench designed for Apple Silicon (M-series) hardware. It follows a layered architecture with clear separation of concerns.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     CLI / Web UI                             │
│           (click CLI, future web interface)                  │
├─────────────────────────────────────────────────────────────┤
│                       Pipeline Layer                         │
│    SciencePipeline · PipelineFactory · PipelineTemplate      │
├─────────────────────────────────────────────────────────────┤
│                       Agent Layer                            │
│    ScienceAgent · Multi-Agent Orchestrator · Tool Registry   │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│Database  │ Compute  │Visualize │Literature│     Audit        │
│PubMed    │Python    │ Charts   │ Search   │ Trace Recorder   │
│UniProt   │R         │Molecules │ Review   │ Provenance       │
│PDB       │Jupyter   │Proteins  │ Paper    │ Reports          │
│Ensembl   │HPC/Slurm │          │          │                  │
│ChEMBL    │          │          │          │                  │
├──────────┴──────────┴──────────┴──────────┴─────────────────┤
│                       Core Engine                            │
│         ScienceEngine (MLX HTTP/Direct) · ModelConfig        │
├─────────────────────────────────────────────────────────────┤
│                    Infrastructure Layer                       │
│   Cache · Mirror Router · Config · File I/O · Logging       │
└─────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

### 1. Local-First
- All LLM inference runs locally via MLX (Apple Silicon)
- No cloud API dependency — works entirely offline
- Data never leaves the local machine

### 2. Modular Architecture
- Each module is independently usable and testable
- Clear interfaces between layers
- Optional dependencies per module (mlx, jupyter, r, molecule)

### 3. Domestic Research Environment Support
- Mirror routing for international databases
- SQLite-based offline cache
- Chinese database endpoints (CNGB, NGDC, CNKI)
- No cross-border data transfer required

### 4. Full Audit Trail
- Every operation is traced with parameters and results
- Data provenance graph tracks lineage
- Reproducibility packages for journal compliance

## Data Flow

### Research Pipeline Example:
```
1. User Input: "Analyze TP53 mutations in breast cancer"
2. Agent Decomposition → Sub-tasks
3. Literature Search → PubMed/arXiv papers
4. Data Retrieval → UniProt/Ensembl gene data
5. Analysis → Python/R code execution
6. Visualization → Charts, protein structures
7. Paper Generation → Draft with citations
8. Audit → Full provenance report
```

## Module Dependencies

```
core (no internal deps)
├── database (depends on core for LLM-assisted queries)
├── compute (standalone)
├── visualization (standalone)
├── literature (depends on database)
└── audit (depends on all modules for tracing)
```

## Configuration

Configuration is managed via:
1. YAML/JSON config file (`~/.config/fusion-science/config.yml`)
2. Environment variables (`FUSION_SCIENCE_*`)
3. Programmatic API (`ScienceConfig`)

## Extending Fusion-Science

### Adding a New Database Connector
1. Create a new file in `fusion_science/database/`
2. Inherit from `BaseConnector`
3. Implement `search()` and `fetch()` methods
4. Add to `DOMESTIC_MIRRORS` in `mirror.py`

### Adding a New Pipeline Template
1. Add a `PipelineTemplate` to `pipeline.py`
2. Define agent configurations with system prompts
3. Register in `PipelineFactory.TEMPLATES`