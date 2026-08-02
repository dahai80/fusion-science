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
│   └── routes/         # /api/v1/health, /sessions, /chat
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

### Phase 7 Professional Agents + MCP + Compliance (v0.4.0)

- **Professional Agent System** (`core/agents/`): 5 specialized agents + QueryRouterAgent with keyword-based routing. LiteratureAgent, DataAgent, VizAgent, WriterAgent, ErrorAnalysisAgent. Error escalation on failure.
- **7 New Tools** (`core/tools.py`): 12 tools total in ToolRegistry.
- **MCP Server** (`mcp_server.py`): JSON-RPC 2.0 + SSE transport for Claude Desktop / Cursor integration.
- **Enhanced API Routes**: `/search`, `/analyze`, `/visualize`, `/review`, `/sessions/{id}/audit`.
- **ComplianceChecker** (`audit/compliance.py`): 4 compliance dimensions — data_residency, algorithm_registration, ethics_review, sensitive_data.

### Phase 8 API Coverage (v0.5.0)

- **Databases Route**: GET `/api/v1/databases` + GET `/api/v1/databases/{name}/status`.
- **Pipelines Route**: GET `/api/v1/pipelines` + POST `/api/v1/pipelines/{name}/run`.
- **Models Route**: GET `/api/v1/models`. Session history endpoint.
- **MCP SSE Transport**: GET `/mcp/sse` with ping keepalive.
- **232 tests passing**, ruff clean.

### Phase 9 P2 Enhancements (v0.6.0)

- **Multi-Model Switching** (F-31): `LLMGateway.set_model()`, `set_model_for_role()`, `get_model_for_role()`. Config fields: `model_reasoning`, `model_summarization`, `model_code`. API: PUT `/api/v1/models/current`, GET/PUT `/api/v1/models/roles`.
- **Paper Writing Enhancement** (F-32): `PaperGenerator` with IMRaD section generation, LLM-driven writing, figure legends, methods-from-code, section balance checks.
- **Citation Graph API** (F-34): GET `/api/v1/citations/graph`, POST `/api/v1/citations/add`, GET `/api/v1/citations/bibliography`.
- **Math Explainer** (F-35): `MathExplainer` with 12 statistical formula patterns, LaTeX symbol conversion, LLM-enhanced explanation. API: POST `/api/v1/math/explain`, POST `/api/v1/math/explain-text`.
- **269 tests passing**, ruff clean.

### Phase 10 Remaining P2 Features (v0.7.0)

- **Molecule Visualization API** (F-24): POST `/api/v1/viz/molecule/smiles`, POST `/api/v1/viz/molecule/pdb`. Graceful rdkit/py3Dmol degradation with 2D fallback.
- **Protein Visualization API** (F-25): POST `/api/v1/viz/protein`, POST `/api/v1/viz/protein/compare`. py3Dmol-based 3D rendering with style/color controls.
- **Jupyter Integration API** (F-26): POST `/api/v1/compute/jupyter/execute`, GET `/api/v1/compute/jupyter/kernels`. Kernel lifecycle management.
- **Code Generation API** (F-30): POST `/api/v1/compute/code-gen`, POST `/api/v1/compute/code-gen/batch`. Rule-based + LLM-driven analysis code.
- **Compliance API** (F-29): POST `/api/v1/compute/compliance`. Full 4-dimension compliance check with trace integration.
- **Offline Mode Enhancement** (F-33): Auto-detect offline via network probe + `FUSION_OFFLINE_MODE` env. GET `/api/v1/system/status`, GET `/api/v1/system/connectivity`.
- **283 tests passing**, ruff clean.

### Phase 11 Non-Functional Requirements (v0.8.0)

- **Connection Monitoring & Auto-Reconnection** (NF-04): `ConnectionMonitor` in `core/retry.py` — periodic health checks, consecutive failure tracking, connection state (connected/disconnected). `retry_with_backoff()` with exponential backoff + jitter for LLM gateway calls.
- **LLMGateway Retry** (NF-01/04): `chat()` wraps HTTP calls in `retry_with_backoff()` for ConnectError/ReadTimeout/PoolTimeout. Response time tracking with `get_avg_response_time()`. Connection stats via `get_connection_stats()`.
- **Secure Key Storage** (NF-03): `utils/keychain.py` — macOS Keychain integration via `security` CLI. Store/retrieve/delete API keys with `SecureConfig` high-level wrapper and in-memory fallback.
- **Custom Tool Registration** (NF-05): `api/routes/tools.py` — POST/GET/DELETE `/api/v1/tools` for MCP-compatible custom tool registration at runtime.
- **Audit Integrity Verification** (NF-08): `audit/integrity.py` — `AuditIntegrityChecker` validates session coverage, parent references, provenance chain integrity, missing parameters, and failed entry diagnostics. API: GET `/api/v1/sessions/{id}/audit/integrity`, GET `/api/v1/sessions/{id}/audit/provenance-integrity`.
- **Security API** (NF-03): `api/routes/security.py` — POST/GET/DELETE `/api/v1/security/keys` for API key lifecycle management (values never exposed in responses).
- **Enhanced System Status** (NF-01): `/api/v1/system/status` now includes connection state and performance metrics.
- **324 tests passing**, ruff clean.

## Domestic Research Environment

Fusion-Science is designed for the Chinese domestic research environment:
- **All-local inference** via MLX — no dependency on foreign API services
- **Domestic database mirrors** — Chinese Academy of Sciences mirror, National Genomics Data Center
- **Offline cache** — literature and molecular datasets can be pre-cached for full offline operation
- **Compliant** — personal/lab internal use does not require AI algorithm registration

## License

MIT