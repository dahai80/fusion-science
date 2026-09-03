# Fusion-Science

> **Local Scientific Research AI Workbench for Apple Silicon**  
> *Derived from the fusion-mlx ecosystem — a fully offline, privacy-first alternative to Claude Science for the domestic research environment.*

Fusion-Science is an open-source, local-first scientific research AI platform that unifies the entire research workflow — literature review, data computation, visualization, paper writing, and result traceability — into a single interface. Built on Apple MLX for fully local inference, it requires no cloud API access and works entirely offline.

## Key Features

- **🔬 8 Scientific Database Connectors** — Built-in connectors for PubMed, UniProt, PDB, Ensembl, ChEMBL (overseas) + NGDC, CNKI, ScienceDB (Chinese). AI automatically cross-references data across databases with domestic mirror support.
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
│   ├── chinese/        # Chinese domestic DB connectors (NGDC, CNKI, ScienceDB)
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

> **Security (v1.0.6+):** The API server binds to `127.0.0.1` by default (loopback only). To expose it on a LAN, set `FUSION_SCIENCE_API_HOST=0.0.0.0` **and** set `FUSION_SCIENCE_API_KEY` to a strong secret — every `/api/v1/*` request must then carry `X-API-Key: <key>`. Code-execution endpoints (`/compute/*`) carry input size + timeout bounds and run in a sandboxed subprocess with a minimal environment whitelist.

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

### Phase 12 Chinese Databases + Mirror Smart Routing (v0.9.0)

- **NGDC Connector** (F-18/T5.4): `NGDCConnector` — 国家基因组科学数据中心, supports GSA/GWH/OMIX sub-databases via `sub_db` parameter. Env override: `FUSION_SCI_NGDC_URL`.
- **CNKI Connector** (F-18/T5.5): `CNKIConnector` — 中国知网, search with `search_type` filter, fetch with institution/fund detail. Env override: `FUSION_SCI_CNKI_URL`.
- **ScienceDB Connector** (F-18/T5.6): `ScienceDBConnector` — 科学数据银行, dataset search/fetch with DOI and license info. Env override: `FUSION_SCI_SCIENCEDB_URL`.
- **CHINESE_CONNECTORS registry**: `database/chinese/__init__.py` — dict mapping `"ngdc"/"cnki"/"scidb"` to connector classes.
- **MirrorRouter Smart Routing** (F-20/T5.3): Latency testing (`test_latency`, `test_all_latency`), auto-switch mode (`enable_auto_switch`), smart URL selection (`smart_get_url` — picks faster endpoint). API: `GET /system/mirrors/latency`, `GET /system/mirrors/status`, `POST /system/mirrors/auto-switch`.
- **363 tests passing**, ruff clean.

### Phase 13 v1.0.0 Release (v1.0.0)

All planned features and non-functional requirements are complete. This is the first production-ready release.

**Feature Summary (F-01 ~ F-35):**

| Category | Features |
|---|---|
| Core Engine | LLMGateway (streaming, structured output, retry), ScienceAgent + SciencePipeline, ToolRegistry (12+ tools) |
| Session & API | FastAPI server with SSE streaming, session CRUD, API key middleware, CORS |
| Literature | 5-layer system: Search → Reader → Extractor → Synthesizer → Reviewer, CitationManager (APA/Vancouver/BibTeX), PaperGenerator |
| Database | 5 overseas connectors (PubMed, UniProt, PDB, Ensembl, ChEMBL) + 3 Chinese connectors (NGDC, CNKI, ScienceDB), DatabaseAggregator, MirrorRouter smart routing |
| Compute | PythonExecutor, RExecutor, JupyterKernel, HPCScheduler, CodeGenerator |
| Visualization | 2D charts, 3D molecule (SMILES/PDB), protein structures |
| Agents | 5 professional agents + QueryRouterAgent, MCP server (JSON-RPC 2.0 + SSE) |
| Math | MathExplainer with 12 statistical formula patterns + LaTeX |
| Audit | TraceRecorder, Provenance chain, ComplianceChecker (4 dimensions), AuditIntegrityChecker |
| Security | macOS Keychain integration, SecureConfig, API key lifecycle |

**Non-Functional Requirements (NF-01 ~ NF-08):**

| ID | Requirement | Implementation |
|---|---|---|
| NF-01 | <1s response (cached), <5s (uncached) | Response time tracking, offline cache, connection stats |
| NF-02 | <2s cold start | Lazy client creation, deferred imports |
| NF-03 | Secure key storage | macOS Keychain + SecureConfig + in-memory fallback |
| NF-04 | Auto-reconnection | ConnectionMonitor + retry_with_backoff with jitter |
| NF-05 | Custom tool registration | Runtime MCP tool CRUD via API |
| NF-06 | Full audit trail | EventBus auto-integration, TraceRecorder, provenance chains |
| NF-07 | Offline-first | Mirror fallback, SQLite cache, FUSION_OFFLINE_MODE |
| NF-08 | Audit integrity | IntegrityChecker validates coverage, chain, parameters |

- **1522+ tests passing**, ruff clean, all phases complete.

### Patch Releases (v1.0.1 ~ v1.0.3)

| Version | Changes |
|---|---|
| v1.0.1 | License → Apache-2.0 (#6, #7); codebase ruff-format cleaned; CI enforces `ruff check` + `ruff format --check`. |
| v1.0.2 | Engine routed through `fusion-gateway` (:11432) instead of direct MLX (:11434); `_MLX_STATUS_URL` stays on :11434 for service discovery (#5). |
| v1.0.3 | Unified service port 8200→11462 (#9, PR #10); CI test job installs `.[test,api]` so FastAPI-backed tests run; `GET /system/mirrors/latency` now probes mirrors in parallel with a 3s per-endpoint cap and degrades per-mirror failure instead of timing out (#8). |
| v1.0.4 | Acceptance pass: ToolRegistry expanded 8→12 MCP-compatible tools (added `visualize_molecule`, `visualize_protein`, `explain_math`, `generate_citation`) with OpenAI function-calling + MCP export; corrected connector/docs counts (5 overseas + 3 Chinese); fixed live MLX auto-detect test for the no-model-loaded case. |
| v1.0.5 | Unified API port 11462 (single source of truth across `serve` + `start.sh`); ruff `I001` import sorting enforced; CI matrix pinned to Python 3.12 (fusion-core `requires-python>=3.12`) (#16, PR #17). |
| v1.0.6 | Security hardening from adversarial audit: RCE sandbox fixes (minimal env whitelist, process-group kill, temp-file data passing — no source inlining); chart-type whitelist + input-data injection closed; Jupyter `code`/`timeout` bounds; deny-by-default API auth with loopback-only default bind; fail-closed MLX memory pressure; per-session lost-update locks + WAL SQLite; IDOR-scoped audit traces; incremental audit persist; cache byte budget + Retry-After jitter; gateway `total_deadline` retry cap; HPC shell-injection guards (identifier whitelist, `shlex.quote`, `O_EXCL` script write). |
| v1.0.7 | Enterprise audit hardening (PRs #20, #21): 4xx/5xx + `detail` error contract across all routes; IDOR owner-scoping on session/audit traces; MCP server wired at `/mcp` (10/12 tools exposed via JSON-RPC 2.0 + SSE); DatabaseAggregator extended to 8 databases with parallel dedup; defusedxml-backed XXE-safe parsing; shadowed legacy `chinese.py` removed. Product release-gate audit (0903) closed the remaining blockers. |
| v1.0.8 | Enterprise production hardening (PRs #23, #25): OS-level sandbox isolation for user code (`sandbox-exec` on macOS / `bwrap` on Linux, rlimit fallback); built-in RBAC + JWT (3 roles: admin/science/viewer, HS256, no external IdP); multi-worker support via shared SQLite store; tamper-evident audit retention (age/count prune) + NDJSON SIEM export; runtime API-key rotation without restart; macOS Keychain-backed secret resolution. See **Enterprise Security** below. |

## Enterprise Security (v1.0.8)

Fusion-Science v1.0.8 closes the code-level enterprise production gaps. Configuration is environment-variable driven; no external identity provider is required (local-first).

### RBAC + JWT

Three built-in roles govern every `/api/v1/*` route by route-prefix × HTTP-method:

| Role | Access |
|---|---|
| `admin` | All routes, all methods (backward-compatible default for the legacy single key). |
| `science` | Read + research workflows: search, databases, citations, math, compute, chat, analysis, visualize, review, audit, pipelines, sessions, tools (read). No system/security/model mutation. |
| `viewer` | Read-only: search, databases, citations, math, visualization, models (list), health, metrics. No compute, no chat, no mutations. |

Provision keys via env or a key file:

```bash
# Multi-role keys (comma- OR newline-separated "role:key" pairs)
export FUSION_SCIENCE_API_KEYS="admin:admin-secret,science:sci-secret,viewer:viewer-secret"

# Legacy single key (still admin, backward compatible)
export FUSION_SCIENCE_API_KEY="admin-secret"
```

Exchange a key for a short-lived JWT (1h, HS256, role claim) at `POST /api/v1/auth/token`; send it as `Authorization: Bearer <jwt>`. A key can only mint a same-or-lower-privilege token — no escalation. `GET /api/v1/auth/whoami` returns the resolved principal.

The JWT signing secret defaults to `FUSION_SCIENCE_JWT_SECRET`; if unset it is derived from the legacy API key.

### Runtime Key Rotation

```bash
# Point at a key file the operator can rewrite without restarting the process
export FUSION_SCIENCE_API_KEYS_FILE="/etc/fusion-science/api-keys.txt"
```

The middleware re-reads provisioned keys on every request, so rewriting the file is a live rotation — no restart. Confirm the reload and audit who triggered it:

```bash
curl -X POST http://localhost:11462/api/v1/security/rotate-keys \
  -H "X-API-Key: admin-secret"
# {"rotated": true, "actor": "apikey:admin-s", "total": 3, "by_role": {...}, "keys": [{"role":"admin","key":"****cret"}]}
```

Secrets never cross the wire inbound — the endpoint only reports masked confirmation; it never mutates env over HTTP.

### Multi-Worker

```bash
export FUSION_SCIENCE_WORKERS=4
./start.sh start   # uvicorn --workers 4
```

Sessions are safe across workers via the shared SQLite store (WAL mode + `busy_timeout=5000`). EventBus/ScienceCache/MirrorRouter remain per-worker (read-mostly state — acceptable). Use `--workers 1` for strictly-single-process semantics.

### Secret Storage (macOS Keychain)

Opt in with `FUSION_SCIENCE_KEYCHAIN=1` to resolve the engine API key and JWT signing secret from the macOS Keychain (service `fusion-science`) instead of plaintext env/config files. Keychain takes priority over the MLX `settings.json` file; explicit env vars always win. Manage stored keys via `POST/GET/DELETE /api/v1/security/keys`.

### Audit Retention & SIEM Export

The tamper-evident trace recorder (SHA-256 hash chain, incremental atomic persist) now prunes at startup by age and count:

```bash
export FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS=180   # default 90
export FUSION_SCIENCE_AUDIT_MAX_SESSIONS=2000  # default 1000
```

Export a session's audit trail as NDJSON (one entry per line) for SIEM/ELK/Splunk ingest:

```bash
curl http://localhost:11462/api/v1/sessions/{session_id}/audit/export \
  -H "X-API-Key: admin-secret"
# Content-Type: application/x-ndjson
```

### Production Deployment Checklist

- Bind loopback by default (`127.0.0.1`). To expose on a LAN set `FUSION_SCIENCE_API_HOST=0.0.0.0` **and** provision API keys.
- Provision role-scoped keys via `FUSION_SCIENCE_API_KEYS` or a rotated key file.
- Set a strong `FUSION_SCIENCE_JWT_SECRET` (or use Keychain).
- Enable audit retention to bound disk growth.
- For multi-worker, confirm the shared SQLite store path is on fast local storage.
- OS sandbox isolation is automatic when `sandbox-exec` (macOS) or `bwrap` (Linux) is available; rlimit-only fallback logs a warning.

> **Not in scope (filed as issues):** multi-node HA deployment topology (#24), formal compliance certification roadmap (#25), external OAuth2/OIDC (#22). v1.0.8 is single-node enterprise-ready; HA and certification are tracked separately.

## Domestic Research Environment

Fusion-Science is designed for the Chinese domestic research environment:
- **All-local inference** via MLX — no dependency on foreign API services
- **Domestic database mirrors** — Chinese Academy of Sciences mirror, National Genomics Data Center
- **Offline cache** — literature and molecular datasets can be pre-cached for full offline operation
- **Compliant** — personal/lab internal use does not require AI algorithm registration

## License

Apache License 2.0