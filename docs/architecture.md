---
doc_id: fusion-science/architecture
product: fusion-science
class: app
entry: service
doc_type: architecture
title: { zh: "架构说明", en: "Architecture" }
audience: engineer
status: draft
version: 1.0.17
updated: 2026-11-07
owner: @dahai
source_of_truth:
  - fusion_science
  - README.md
  - docs/api.md
---

# Architecture (架构说明)

## Module map (模块划分)

Source: `fusion_science/` package layout.

```
fusion_science/
├── cli.py             # click CLI: fusion-science (chat/search/...), serve, fusion-science-web alias
├── config.py          # ScienceConfig: env-driven settings
├── mcp_server.py      # MCP JSON-RPC 2.0 + SSE transport
├── core/              # engine (LLMGateway), agent, pipeline, tools (ToolRegistry), agents/ (5 pros + router), retry
├── database/          # 8 connectors + aggregator + mirror router + chinese/ registry
├── compute/           # python_executor, r_executor, jupyter_kernel, hpc_scheduler, code generator
├── visualization/     # chart generator, molecule/protein visualizers
├── literature/        # search, reader, extractor, synthesizer, review, citation, paper
├── session/           # ResearchSession, memory/sqlite/postgres stores, manager
├── audit/             # TraceRecorder, integrity checker, compliance checker, encryption, retention
├── api/               # FastAPI app factory, auth, middleware, sse, routes/, static/index.html
└── utils/             # EventBus (async pub/sub, auto-audit), keychain
```

## Request flow (请求链路)

1. Client → uvicorn (`start.sh` or `fusion-science serve`) on :11462.
2. `APIKeyMiddleware` — rate limit, key/JWT auth, RBAC check, idle-timeout check.
3. Route handler → module service (literature / compute / visualization / ...).
4. LLM calls go through `LLMGateway` (`core/gateway.py`) → fusion-gateway :11432
   → fusion-mlx :11434. Service discovery pings :11434 for the loaded-model list.
   `retry_with_backoff` (exponential + jitter, `total_deadline` cap) wraps calls.
5. EventBus publishes every DB query / code execution / LLM call / tool run; the
   app lifespan handler persists them into the per-session TraceRecorder
   (SHA-256 hash chain, optional AES-256-GCM envelope, optional SIEM fan-out).

链路：客户端 → 中间件（限流/鉴权/RBAC/空闲超时）→ 路由 → 模块服务；LLM 调用经
LLMGateway → fusion-gateway :11432 → fusion-mlx :11434，带指数退避重试；所有事件
经 EventBus 落入会话级哈希链审计。

## Data stores (数据存储)

| Store | Implementation | Notes |
|---|---|---|
| Sessions | Memory LRU (1000) / SQLite (WAL + per-session locks) / Postgres (`[ha]`) | `FUSION_SCIENCE_SESSION_STORE` |
| Audit trail | JSON/NDJSON files per session, hash-chained | retention prunes by age + count |
| Literature cache | SQLite | mirror routing + offline mode |

## Design decisions (设计要点)

- Tool dispatch is centralized in `ToolRegistry` (12 tools, MCP + OpenAI export);
  agents never call modules directly. Adding a tool = one registry entry + route.
- Sandbox: user code runs in a subprocess with a minimal env whitelist and is
  killed by process group; OS-level confinement via `sandbox-exec` (macOS) /
  `bwrap` (Linux), rlimit fallback otherwise.
- Connector failures degrade per-mirror: a dead endpoint is skipped after the
  latency probe (3s per-endpoint cap on `GET /system/mirrors/latency`), not fatal.
- SSE streaming sets anti-buffering headers and emits done/error terminal events.

- 工具调用统一走 ToolRegistry；沙箱按 OS 分层；镜像故障按探活降级；SSE 带终态事件。

## Failure modes (故障模式)

| Failure | Behavior |
|---|---|
| fusion-mlx/gateway unreachable | retry with backoff, then error; ConnectionMonitor marks disconnected and auto-reconnects |
| No model loaded | LLM layers fall back to rule-based paths (reader/extractor/synthesizer) |
| Postgres store down | `/api/v1/ready` → 503; health stays ok (liveness vs readiness split) |
| SIEM collector down | audit degrades to local-file-only, request path unaffected |
| Keychain unavailable | audit encryption degrades to plaintext with a warning log |

## Cross-repo dependencies (跨仓依赖)

- fusion-mlx (:11434) — inference engine.
- fusion-gateway (:11432) — inference front door; traffic should route through it.
- fusion-core — shared LLM client / config / logging base.
- See `/Users/dahai/fusion/architecture/port-registry.yaml` for port ownership.
