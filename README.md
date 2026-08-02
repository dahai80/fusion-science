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
├── core/
│   ├── gateway.py      # LLMGateway — httpx to fusion-mlx HTTP API (streaming, structured output)
│   ├── engine.py       # Backward-compat re-export of LLMGateway as ScienceEngine
│   ├── tools.py        # ToolRegistry — MCP-compatible tool registration with OpenAI function calling
│   ├── agent.py        # ScienceAgent (tool-use loop) + SciencePipeline (sequential/parallel/master-worker)
│   └── pipeline.py     # PipelineFactory + built-in templates (literature, bioinformatics, molecular)
├── session/            # Research session management
│   ├── models.py       # ResearchSession, Artifact, ResearchContext dataclasses
│   ├── store.py        # MemorySessionStore (LRU) + SQLiteSessionStore (persistence)
│   └── manager.py      # SessionManager with EventBus integration
├── api/                # FastAPI HTTP server
│   ├── app.py          # create_app() factory, lifespan, audit auto-integration
│   ├── sse.py          # SSE streaming (token-by-token + done/error)
│   ├── middleware.py   # APIKeyMiddleware (hmac, exempt paths)
│   └── routes/         # /api/v1/health, /sessions, /chat, /search, /analyze, /visualize, /review, /databases, /pipelines, /models
├── database/           # Scientific database connectors + domestic mirrors
│   ├── aggregator.py   # DatabaseAggregator — multi-DB parallel search with dedup
├── compute/            # Code execution (Python/R/Jupyter) & HPC scheduling
├── visualization/      # Charts, 3D molecules, protein structures
├── literature/         # Search, reading, extraction, synthesis, review, citations
│   ├── search.py       # LiteratureSearch + SearchPreset (quick/pro/deep) + PRISMA flow
│   ├── reader.py       # LiteratureReader — LLM deep reading with section summaries & TLDR
│   ├── extractor.py    # LiteratureExtractor — PICO, structured data, study type classification
│   ├── synthesizer.py  # LiteratureSynthesizer — consensus analysis, contradiction detection
│   ├── review.py       # LiteratureReviewer — async review with LLM sections + PRISMA
│   ├── citation.py     # CitationManager — APA/Vancouver/BibTeX, dedup, graph, verify
│   └── paper.py        # PaperGenerator — IMRaD paper drafting
├── audit/              # Provenance tracking & reproducibility reports
└── utils/
    └── events.py       # EventBus — async pub/sub for cross-module decoupling & audit auto-integration
```

### Phase 1 Foundation (v0.2.0)

- **LLMGateway** (`core/gateway.py`): Direct httpx.AsyncClient to fusion-mlx. Supports `chat()`, `chat_stream()` (SSE), `structured_output()`, lazy client creation.
- **ToolRegistry** (`core/tools.py`): MCP-compatible tool center. 5 built-in tools (search_literature, search_database, execute_python, generate_chart, fetch_paper). Exports OpenAI function calling and MCP tool formats.
- **EventBus** (`utils/events.py`): Async event bus for module decoupling. Auto-integrates with audit trail.
- **ScienceAgent refactor**: `_execute_tool()` now dispatches via ToolRegistry instead of returning "not_implemented" stubs.
- **ScienceConfig**: Added `api_host`, `api_port`, `api_cors_origins` for Phase 2 API server.

### Phase 2 Session + API (v0.2.0)

- **Session Management** (`session/`): ResearchSession with messages, artifacts, context. MemorySessionStore (LRU 1000) + SQLiteSessionStore for persistence. SessionManager with EventBus auto-emit.
- **FastAPI Server** (`api/`): `create_app()` factory with lifespan, CORS + APIKeyMiddleware. Routes: `/api/v1/health`, `/api/v1/sessions` (CRUD), `/api/v1/chat` (sync + SSE stream).
- **SSE Streaming** (`api/sse.py`): Token-by-token streaming with done/error events. Anti-buffering headers.
- **Audit Auto-Integration**: EventBus handler in app lifespan auto-records all events (db_query, code_execution, llm_call, tool_executed, etc.) to TraceRecorder.
- **CLI Serve Command**: `fusion-science serve [--host] [--port] [--reload]` starts uvicorn with API server.

### Phase 3 Literature System (v0.3.0)

Five-layer literature architecture with LLM-driven deep analysis and rule-based fallbacks:

- **LiteratureSearch** (`literature/search.py`): SearchPreset levels — QUICK (10 papers, <5s), PROFESSIONAL (30 papers, <15s), DEEP (100 papers, <60s with PRISMA flow). Dedup + relevance scoring.
- **LiteratureReader** (`literature/reader.py`): LLM-driven paper deep reading. Section summarization, TLDR generation, methodology assessment, strength/weakness analysis. Falls back to stub reading without LLM.
- **LiteratureExtractor** (`literature/extractor.py`): Structured data extraction — PICO (Population/Intervention/Comparator/Outcome), study type classification (RCT/cohort/meta-analysis/etc.), sample size, p-value, effect size, limitations. Rule-based fallback when no LLM.
- **LiteratureSynthesizer** (`literature/synthesizer.py`): Multi-paper consensus analysis. Consensus score (-1.0~1.0), contradiction detection, research gap identification, trend analysis. Keyword-frequency fallback path.
- **LiteratureReviewer** (`literature/review.py`): **Breaking**: `analyze_papers()` is now `async`. Generates IMRaD sections via LLM with consensus data, or rule-based theme clustering. PRISMA flow diagram support.
- **CitationManager** (`literature/citation.py`): APA/Vancouver/BibTeX formatting, auto key generation, deduplication, citation graph (keyword-based relation), citation verification.
- **DatabaseAggregator** (`database/aggregator.py`): Multi-database parallel search across PubMed/UniProt/PDB/Ensembl/ChEMBL. Async semaphore-controlled concurrency, result merging with dedup, unified ranking.

### Phase 4 Compute & Visualization (v0.3.1)

- **CodeGenerator** (`compute/code_generator.py`): Rule-based + LLM-driven code generation. 6 bioinformatics templates (DESeq2, GO enrichment, correlation, t-test, PCA, clustering). Returns `CodeSuggestion` with confidence scores and package requirements.
- **SandboxManager** (`compute/sandbox.py`): AST-based code validation, blocked pattern detection (eval/exec/os.system/subprocess), tempdir sandbox creation with resource limits. `validate_code()` returns `{valid, issues, risk_level}`.
- **SmartVisualizer** (`visualization/smart_viz.py`): Rule-based + LLM-driven visualization recommendations. 9 keyword-to-chart mappings. Returns `VizRecommendation` with confidence and suggested config.

### Phase 5 Audit & Chinese DB (v0.3.1)

- **ReproducibilityPack** (`audit/reproducibility.py`): Full environment + trace + provenance snapshot with SHA256 checksum. `ReproducibilityPackBuilder` collects platform/dependency/trace/provenance data. `export_to_dir()` for sharing.
- **ComplianceChecker** (`audit/reproducibility.py`): 6 built-in compliance rules (data_provenance, execution_trace, platform_recorded, dependencies_recorded, version_pinned, checksum_integrity). Custom rule support. `check_report()` generates full compliance report.
- **Chinese DB Connectors** (`database/chinese.py`): `NGDCConnector` (国家基因库), `CNKIConnector` (知网), `ScienceDBConnector` (ScienceDB). All extend `BaseConnector` with domestic mirror support.
- **MirrorRouter** (`database/chinese.py`): Smart mirror routing with offline mode. `get_url()` selects best mirror, `list_mirrors()` shows available routes.

### Phase 6 CI & Quality (v0.3.1)

- **GitHub Actions CI** (`.github/workflows/ci.yml`): Lint job (ruff check + format check) and test job (pytest on Python 3.11/3.12 matrix).
- **Ruff Configuration** (`pyproject.toml`): `target-version="py312"`, `line-length=120`, rules E/F/W/I/UP/B/SIM. Auto-formatting with double quotes, space indentation.
- **216 tests passing** across all modules: core, session, API, literature, audit, compute, visualization, database, events, tools, phases 4-6.

### Phase 7 Professional Agents + MCP + Compliance (v0.4.0)

- **Professional Agent System** (`core/agents/`): 5 specialized agents + QueryRouterAgent with keyword-based routing. LiteratureAgent (search, extract, consensus), DataAgent (Python/R execution), VizAgent (charts, molecules, proteins), WriterAgent (paper sections, citations), ErrorAnalysisAgent (failure diagnosis). Error escalation from any agent to ErrorAnalysisAgent on failure.
- **7 New Tools** (`core/tools.py`): extract_findings, analyze_consensus, execute_r, visualize_molecule, visualize_protein, write_section (internal), manage_citations (internal). 12 tools total registered in ToolRegistry.
- **MCP Server** (`mcp_server.py`): JSON-RPC 2.0 endpoint at `/mcp` for Model Context Protocol integration with Claude Desktop and Cursor. Methods: initialize, tools/list, tools/call. Full error code support (-32700/-32600/-32601/-32602/-32603).
- **Enhanced API Routes** (`api/routes/`): `/api/v1/search` (literature search), `/api/v1/analyze` (DataAgent), `/api/v1/visualize` (VizAgent), `/api/v1/review` (LiteratureAgent), `/api/v1/sessions/{id}/audit` (compliance check).
- **ComplianceChecker** (`audit/compliance.py`): 4 compliance dimensions — data_residency (no remote API calls), algorithm_registration (personal/lab exempt), ethics_review (human/animal data), sensitive_data (genomic/clinical/patient). `check_report()` returns structured results with severity levels.
- **232 tests passing** across all modules.

### Phase 8 API Coverage (v0.5.0)

- **Databases Route** (`api/routes/databases.py`): GET `/api/v1/databases` lists 8 scientific databases with mirror info. GET `/api/v1/databases/{name}/status` per-database health check with async connectivity test.
- **Pipelines Route** (`api/routes/pipelines.py`): GET `/api/v1/pipelines` lists 3 built-in pipeline templates (literature_review, bioinformatics_analysis, molecular_analysis). POST `/api/v1/pipelines/{name}/run` executes a pipeline with query and config.
- **Models Route** (`api/routes/models.py`): GET `/api/v1/models` lists available LLM models from fusion-mlx inference engine.
- **Session History**: GET `/api/v1/sessions/{id}/history` returns full session message history.
- **LLMGateway.list_models()** (`core/gateway.py`): New method to query fusion-mlx `/models` endpoint.
- **MCP SSE Transport** (`mcp_server.py`): GET `/mcp/sse` endpoint with Server-Sent Events transport and ping keepalive. POST `/mcp` handles JSON-RPC 2.0 messages.
- **Lint Clean**: All ruff issues resolved (I001 import sorting, F541 f-string fixes).
- **232 tests passing** across all modules.

## Domestic Research Environment

Fusion-Science is designed for the Chinese domestic research environment:
- **All-local inference** via MLX — no dependency on foreign API services
- **Domestic database mirrors** — Chinese Academy of Sciences mirror, National Genomics Data Center
- **Offline cache** — literature and molecular datasets can be pre-cached for full offline operation
- **Compliant** — personal/lab internal use does not require AI algorithm registration

## License

MIT