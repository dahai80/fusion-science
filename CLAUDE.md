---
doc_id: fusion-science/guide
product: fusion-science
class: app
entry: service
doc_type: guide
title: { zh: "Claude Code 工作指引", en: "Claude Code Working Guide" }
audience: engineer
status: draft
version: 1.0.17
updated: 2026-11-07
owner: @dahai
source_of_truth:
  - CLAUDE.md
---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Fusion-Science is a local-first scientific research AI workbench for Apple Silicon (v1.0.17). It unifies literature review, data computation, visualization, paper writing, and result traceability — all running locally via MLX inference. Sits in the fusion monorepo; shares one `.venv/` at the repo root with sibling `fusion-*` projects. Hardened for enterprise/compliance deployments (RBAC, OIDC, HA Postgres, encryption-at-rest, MFA, DSAR, 等保三级 controls).

## Environment & Build

```bash
cd /Users/dahai/fusion && source .venv/bin/activate   # REQUIRED before any Python work

# Install (editable, with test + API deps for full dev)
pip install -e ".[test,api]"

# Full enterprise install (what CI uses)
pip install -e ".[test,api,oidc,ha,security]"

# Lint (ruff config in pyproject.toml — target py312, line-length 120)
ruff check .
ruff format .

# Run all tests
pytest tests/ -v

# Single test (tests are class-based — specify Class::method)
pytest tests/test_core.py::TestLLMResponse::test_default_response -v

# Integration tests against a live fusion-mlx service (skipped by default)
pytest tests/ --integration -v

# Environment check (deps, mirrors, offline mode)
python scripts/check_env.py
```

Optional extras: `[mlx]` `[jupyter]` `[r]` `[molecule]` `[oidc]` `[ha]` `[security]` or `[all]`. The `[api]` extra (fastapi/uvicorn/sse-starlette) is needed to run the API server and is required for the CI test install. `[oidc]` (pyjwt/cryptography), `[ha]` (psycopg), `[security]` (cryptography) are lazy-import extras — default install must keep working without them. CI installs fusion-core from git (`dahai80/fusion-core`, not on PyPI); see `fusion-core#9`.

## Running

```bash
# CLI entry points (registered in pyproject [project.scripts])
fusion-science                       # interactive REPL (run/pipeline/search/analyze/visualize/review/audit/config/serve)
fusion-science run "task"            # single task
fusion-science pipeline literature_review "query"
fusion-science search "query" --db pubmed
fusion-science serve                 # API server (alias: fusion-science-web / web)
fusion-science info                  # system info & optional-dep status

# Daemon lifecycle (PID file, health-gated startup, optional TLS)
./start.sh start|stop|restart|status   # default port 11462 (FUSION_SCIENCE_PORT), health at /api/v1/health
```

start.sh auto-resolves the MLX model + API key from `~/.fusion-mlx/settings.json` and `/api/status`; multi-worker via `WEB_CONCURRENCY`/`UVICORN_WORKERS`/`FUSION_SCIENCE_WORKERS`. `start` runs a register-then-start self-check first (Config Phase 3.3, issue #36): it verifies the service's port is registered in `architecture/port-registry.yaml` (`FUSION_REGISTRY` to override) and refuses to start on an unregistered port; `FUSION_SKIP_REGISTRY_CHECK=1` bypasses, and a missing registry file warns and continues (standalone deploy).

The inference engine (fusion-mlx) must be running separately: `~/fusion/fusion-mlx/start.sh start` (port 11434).

## Architecture

Two layers wrap fusion-core infrastructure into a scientific agent stack:

```
fusion_science/
├── core/
│   ├── gateway.py       LLMGateway — httpx via fusion_core.http_client → fusion-gateway :11432 → fusion-mlx HTTP API; streaming, structured output, multi-model roles (reasoning/summarization/code), connection monitor, MLX memory check/unload
│   ├── engine.py        Backward-compat re-export: `from .gateway import LLMGateway as ScienceEngine` (legacy code imports ScienceEngine)
│   ├── agent.py         ScienceAgent (think→tool_call→tool_result→output loop, max 10 iters) + SciencePipeline (sequential/parallel/master_worker)
│   ├── agents/          QueryRouterAgent routes by keyword to 5 specialist agents: LiteratureAgent, DataAgent, VizAgent, WriterAgent, ErrorAnalysisAgent
│   ├── pipeline.py      PipelineFactory + templates: literature_review, bioinformatics_analysis, molecular_analysis
│   ├── tools.py         ToolRegistry (12 built-in tools; JSON-schema arg validation, result caps, RCE-via-MCP disabled)
│   ├── context_manager.py  Conversation context (sliding-window B1)
│   └── retry.py         ConnectionMonitor + RetryStats for gateway resilience
├── api/                 FastAPI server (create_app() factory, lifespan auto-wires gateway+tools+agents+session+audit)
│   ├── app.py           lifespan: load_config → LLMGateway → SessionManager (store by config) → ToolRegistry → QueryRouterAgent → shared DatabaseAggregator → ContextManager → TraceRecorder (retention/sink/encryption) subscribed to EventBus
│   ├── auth.py          RBAC: 3 roles (admin/science/viewer) + route-prefix permission map; HS256 JWT issue/decode; OIDC RS256 via IdP JWKS (`FUSION_SCIENCE_OIDC_*`); idle lockout; key rotation
│   ├── middleware.py    APIKeyMiddleware (fail-closed auth: API key / JWT / OIDC, RBAC gate), RateLimitMiddleware (opt-in), MetricsMiddleware, AnomalyMiddleware (opt-in route-enumeration/burst detection)
│   ├── sse.py           SSE token streaming
│   ├── routes/          /api/v1: health, auth (login + TOTP MFA enforcement), sessions, chat, search, analysis, visualize, review, audit, databases, pipelines, models, citations, math, metrics, viz, compute, system, tools, security, privacy (DSAR)
│   ├── _context.py      Prior-context injection into chat requests
│   ├── _owner.py        Session-ownership helper (IDOR protection)
│   └── mcp_server.py mounted at /mcp — MCP JSON-RPC 2.0 (initialize, tools/list, tools/call) + /sse transport
├── session/             ResearchSession/Artifact dataclasses; SessionStore ABC + Memory/SQLite (WAL, optimistic-lock, backup-on-shutdown)/Postgres (HA shared) stores; SessionManager (EventBus-integrated, per-session caps)
├── database/            BaseConnector (ABC: async search/fetch, LRU cache, rate limit, retry, offline mode); connectors for PubMed (defusedxml)/UniProt/PDB/Ensembl/ChEMBL + chinese/ (NGDC/CNKI/ScienceDB); aggregator.py multi-DB parallel search w/ dedup (shared instance on app.state); mirror.py MirrorManager + ScienceCache (SQLite, single-conn WAL)
├── compute/             python_executor.py (subprocess + OS isolation, resource limits, matplotlib capture), isolation.py (macOS seatbelt profile / bubblewrap), r_executor.py (rpy2), jupyter_kernel.py, hpc_scheduler.py, code_generator.py, sandbox.py
├── literature/          search.py (SearchPreset quick/pro/deep + PRISMA), reader.py, extractor.py (PICO), synthesizer.py, review.py, citation.py, paper.py, math_explainer.py
├── visualization/       chart.py, molecule.py (SMILES), protein.py (PDB), smart_viz.py
├── audit/               tracker.py (TraceRecorder: hash-chain, atomic persist, retention, SIEM sink, AES-256-GCM encrypt-at-rest), provenance.py, report.py, compliance.py, integrity.py, reproducibility.py
└── utils/               events.py (EventBus), crypto.py (AES-256-GCM), keychain.py (macOS Keychain secrets), mfa.py (TOTP), malware_scan.py (stdlib signatures, /security/scan), logging_setup.py (rotating file), mirrors.py, offline.py
```

### Key Patterns

**LLM calls** — All inference goes through `LLMGateway` (re-exported as `ScienceEngine` for legacy imports) → `fusion_core.http_client.get_async_client` → fusion-gateway `:11432/v1` (NOT MLX `:11434` directly). No direct `mlx`/`mlx_lm` imports anywhere. Gateway sends `X-Fusion-Route: fusion-science` header; checks MLX memory pressure and can unload models. Multi-model roles via `set_model_for_role()`.

**Engine wiring** — `api/app.py` lifespan constructs the full stack; routes access these via `request.app.state` (`config`, `gateway`, `session_manager`, `tool_registry`, `router_agent`, `aggregator`, `context_manager`, `recorder`). Shutdown closes gateway/aggregator, backs up + closes the session store, resets shared cache/router.

**Auth model** — `APIKeyMiddleware` is fail-closed: requests must carry a valid API key (role-mapped, from env/file, hot-reloadable), an HS256 JWT from `/api/v1/auth/token`, or an OIDC RS256 token verified against JWKS. JWT signing secret has NO hardcoded fallback (issue #32): env → Keychain → derived from `FUSION_SCIENCE_API_KEY`; prod missing → RuntimeError, dev → warning + token login disabled. `role_allows(role, route_prefix, method)` gates every route; health/metrics exempt. Sessions enforce owner checks (`_owner.py`).

**Configuration** — `ScienceConfig` (dataclass) merges: defaults → `.env` (config/, optional python-dotenv) → YAML/JSON file (`./fusion-science.yml` or `~/.config/fusion-science/config.yml`) → env vars. Env var prefixes: `FUSION_SCIENCE_*` (config fields), `FUSION_SCI_*` (mirror URLs), `FUSION_OFFLINE_MODE` (offline toggle). Sentinel `engine_api_key` resolves via the `fusion_core.config` single-point resolver (issue #34): macOS Keychain (opt-in `FUSION_SCIENCE_KEYCHAIN=1`) → `client_key()` alias chain (`FUSION_CLIENT_KEY`, dev missing = `""` + warning, prod missing = `RuntimeError`) → `resolve_api_key()` (settings.json fallback). Sentinel `model_name` auto-detects from the running fusion-mlx service; explicit settings always win. No local `_resolve_*` key functions — resolution lives in fusion-core only (config-conventions §2).

**Ports & data** — `engine_base_url` defaults to `http://localhost:11432/v1` (fusion-gateway route); override via `FUSION_SCIENCE_ENGINE_BASE_URL`. `api_port` defaults to 11462; `FUSION_SCIENCE_PORT` (via `fusion_core.config.service_port`, what start.sh uses) beats legacy `FUSION_SCIENCE_API_PORT`. `FUSION_SCIENCE_DATA_DIR` (via `service_data_dir`) re-roots `cache_dir`/`session_db_path`/`trace_dir`/`provenance_dir` when they are still at their defaults; explicit per-path env/file settings win. MLX status discovery probes `:11434/api/status` (`FUSION_SCI_MLX_STATUS_URL`). API binds 127.0.0.1 by default; LAN exposure requires setting an API key.

**Database connectors** — All extend `BaseConnector`. `ConnectorConfig` controls mirror URL, offline mode, retry, rate limit, cache TTL. Offline mode (`FUSION_OFFLINE_MODE=true`) raises `RuntimeError` on any network request — connectors only serve from cache. LRU cache per connector. `_request_with_retry()` handles 429/503 with exponential backoff.

**Agent system** — `QueryRouterAgent` keyword-routes to specialist agents; each `ScienceAgent` runs a tool-use loop. `SciencePipeline` orchestrates with sequential/parallel/master_worker patterns. `PipelineFactory` builds from templates.

**Enterprise/compliance knobs (all opt-in via env)** — `FUSION_SCIENCE_COMPLIANCE_LEVEL` (3 = 等保三级: raises audit retention to 180d), `FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS`/`_MAX_SESSIONS`, `FUSION_SCIENCE_AUDIT_SINK_URL` (NDJSON SIEM forward), `FUSION_SCIENCE_ENCRYPT_AT_REST`, `FUSION_SCIENCE_RATE_LIMIT`/`_RATE_WINDOW`, `FUSION_SCIENCE_ANOMALY_DETECT`, `FUSION_SCIENCE_SESSION_DSN` (Postgres HA store via `session_store=postgres`), `FUSION_SCIENCE_API_KEY_FILE` (hot-reload key rotation), `FUSION_SCIENCE_MFA_SECRETS_FILE` (TOTP, `subject:base32secret` — secret is the segment after the LAST colon), `FUSION_SCIENCE_KEYCHAIN` (macOS Keychain), `FUSION_SCIENCE_ENCRYPTION_KEY`.

## Testing

Tests use `pytest` with `asyncio_mode=auto`, `testpaths=["tests"]` (config in pyproject.toml). Test files are **class-based** (e.g. `class TestLLMResponse: def test_default_response`). ~1657 tests.

Key test files: `test_core.py` (engine/gateway/response/config), `test_api.py` (FastAPI routes), `test_auth_rbac.py` (roles/JWT/OIDC/MFA), `test_ha.py` (Postgres store), `test_compliance.py` + `test_enterprise_gaps.py` (encryption, retention, DSAR, malware scan, anomaly), `test_database.py`, `test_compute.py` (sandbox/isolation), `test_session.py`, `test_tools.py`, `test_literature_audit.py`, `test_e2e.py` (live MLX integration), `test_coverage_batch1-6.py` (large coverage suites).

Integration tests (marked `@pytest.mark.integration`) require a running fusion-mlx and are **skipped unless** `--integration` is passed (conftest.py enforces). CI runs `ruff check` + `ruff format --check` + pytest on Python 3.12 only (fusion-core requires ≥3.12), with `OPENBLAS_NUM_THREADS: 1` (BLAS thread-pin — sandbox env must not leak threads).

## Conventions

- 4-space multiples indentation, no docstrings, always include logging
- setuptools backend with `[tool.setuptools.packages.find] include=["fusion_science*"]`
- Ruff: select `E,F,W,I,UP,B,SIM` (ignore E501/B008/SIM108); imports isort-clean (I001)
- Upstream issues (fusion-mlx, fusion-core, fusion-gateway) → file issue first, then PR; don't modify sibling projects. Cross-repo issue rules in `AGENTS.md`
- Ports: register in `architecture/port-registry.yaml` before binding; run `python scripts/check-ports.py` from repo root (see root CLAUDE.md)
- Long-running entrypoints set process title via setproctitle (`fusion-science serve :<port>` in `api/app.py` lifespan, try/except fallback — see AGENTS.md)
- Lazy-import optional extras — default install (no extras) must always import and run
