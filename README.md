# Fusion-Science

> **Local Scientific Research AI Workbench for Apple Silicon**  
> *Derived from the fusion-mlx ecosystem — a fully offline, privacy-first alternative to Claude Science for the domestic research environment.*

Fusion-Science is an open-source, local-first scientific research AI platform that unifies the entire research workflow — literature review, data computation, visualization, paper writing, and result traceability — into a single interface. Built on Apple MLX for fully local inference, it requires no cloud API access and works entirely offline.

## Key Features

- **🔬 60+ Scientific Database Connectors** — Built-in connectors for PubMed, UniProt, PDB, Ensembl, ChEMBL, and more. AI automatically cross-references data across databases with domestic mirror support.
- **🤖 AI Agent for Computational Experiments** — Multi-agent architecture (MCP-based) that automatically runs Python/R/Jupyter for statistical analysis, omics data processing, and molecular simulations.
- **📊 Full-Stack Visualization** — 2D statistical charts, 3D molecular/protein structure visualization, publication-ready figures.
- **📝 Literature Review & Paper Writing** — Batch reading, comparison, and synthesis of hundreds of papers; iterative paper generation with proper citations.
- **🔗 Full Audit Trail & Reproducibility** — Every chart, data point, and paper fragment retains complete provenance: query source, executed code, parameters, and computation logs.
- **🏠 Local-First, Privacy Controlled** — All computation runs locally; sensitive sequencing and drug discovery data never leaves your machine. Supports private cluster integration.

## Quick Start

```bash
# Install
pip install fusion-science

# Or with full scientific support
pip install "fusion-science[all]"

# Launch CLI
fusion-science

# Launch web UI
fusion-science-web
```

## Architecture

```
fusion-science/
├── core/           # MLX inference engine & agent runtime
├── database/       # Scientific database connectors + domestic mirrors
├── compute/        # Code execution (Python/R/Jupyter) & HPC scheduling
├── visualization/  # Charts, 3D molecules, protein structures
├── literature/     # Search, review, paper generation
├── audit/          # Provenance tracking & reproducibility reports
└── utils/          # Mirror configuration, helpers
```

## Domestic Research Environment

Fusion-Science is designed for the Chinese domestic research environment:
- **All-local inference** via MLX — no dependency on foreign API services
- **Domestic database mirrors** — Chinese Academy of Sciences mirror, National Genomics Data Center
- **Offline cache** — literature and molecular datasets can be pre-cached for full offline operation
- **Compliant** — personal/lab internal use does not require AI algorithm registration

## License

MIT