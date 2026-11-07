---
doc_id: fusion-science/api-reference
product: fusion-science
class: app
entry: service
doc_type: api-reference
title: { zh: "API 参考", en: "API Reference" }
audience: engineer
status: draft
version: 1.0.17
updated: 2026-11-07
owner: @dahai
source_of_truth:
  - fusion_science/api/routes
  - fusion_science/api/app.py
  - docs/api.md
---

# API Reference (API 参考)

Base URL: `http://127.0.0.1:11462`. All endpoints live under `/api/v1/*` except
the MCP transport. Error contract: every 4xx/5xx returns `{"detail": "..."}`.

基础地址 `http://127.0.0.1:11462`；错误统一返回 `{"detail": "..."}`。

## Authentication (鉴权)

- Default (no env config): requests from loopback pass without a key.
- With `FUSION_SCIENCE_API_KEY(S)` set: deny-by-default; every request needs
  `X-API-Key: <key>` or a JWT from `POST /api/v1/auth/token`.
- RBAC roles: `admin` > `science` > `viewer` (permission map in
  `fusion_science/api/auth.py`). External OIDC (RS256 via JWKS) is opt-in via
  `[oidc]` extra.

默认回环请求免鉴权；设置 API Key 后默认拒绝，需带 `X-API-Key` 或 JWT。
角色三级：admin / science / viewer。

## Endpoint catalog (端点目录)

Source of truth: `fusion_science/api/routes/`. The table below maps the route
modules; read each module for exact request/response schemas.

| Module | Endpoints | Notes |
|---|---|---|
| health | `GET /api/v1/health`, `GET /api/v1/ready` | ready returns 503 when the session store is down |
| auth | `POST /api/v1/auth/token` | JWT mint; requires `totp` field when MFA is enforced |
| sessions | `POST/GET/PUT/DELETE /api/v1/sessions`, `GET /api/v1/sessions/{id}/history` | IDOR owner-scoped |
| chat | `POST /api/v1/chat` | sync JSON or SSE stream |
| search | `POST /api/v1/search` | literature search, QUICK/PROFESSIONAL/DEEP presets |
| databases | `GET /api/v1/databases`, `GET /api/v1/databases/{name}/status` | 8 connectors |
| compute | `/api/v1/compute/*` | python/r execute, jupyter execute + kernels, code-gen, compliance |
| visualize | `POST /api/v1/visualize`, `/api/v1/viz/molecule/smiles`, `/api/v1/viz/molecule/pdb`, `/api/v1/viz/protein`, `/api/v1/viz/protein/compare` | chart-type whitelist; rdkit/py3Dmol optional |
| review | `POST /api/v1/review`, `/analyze` | async IMRaD review, PRISMA |
| citations | `GET /api/v1/citations/graph`, `POST /api/v1/citations/add`, `GET /api/v1/citations/bibliography` | APA/Vancouver/BibTeX |
| math | `POST /api/v1/math/explain`, `POST /api/v1/math/explain-text` | 12 statistical formula patterns |
| models | `GET /api/v1/models`, `PUT /api/v1/models/current`, `GET/PUT /api/v1/models/roles` | multi-model switching per role |
| pipelines | `GET /api/v1/pipelines`, `POST /api/v1/pipelines/{name}/run` | |
| tools | `POST/GET/DELETE /api/v1/tools` | runtime MCP tool registration |
| security | `POST/GET/DELETE /api/v1/security/keys` | key lifecycle; values never returned |
| audit | `GET /api/v1/sessions/{id}/audit`, `.../audit/integrity`, `.../audit/provenance-integrity`, `.../audit/export` | NDJSON export |
| privacy | `GET /api/v1/data-subject/{subject}/sessions`, `DELETE /api/v1/data-subject/{subject}` | DSAR; admin-only |
| system | `GET /api/v1/system/status`, `/connectivity`, `/mirrors/latency`, `/mirrors/status`, `POST /mirrors/auto-switch` | mirror smart routing |
| analysis, metrics, web | `analysis.py`, `metrics.py`, `web.py` | see module source |

## MCP transport (MCP 传输)

- `POST /mcp` — JSON-RPC 2.0. 10 of the 12 registry tools are exposed.
- `GET /mcp/sse` — SSE transport with ping keepalive.
- Registry export formats: OpenAI function calling and MCP tool schema
  (`fusion_science/core/tools.py`).

## Rate limits and bounds (限流与上限)

- API-key principals pass through the in-process rate limiter (per-IP /
  per-principal, single-process scope). Multi-worker deployments share the
  session store, but rate-limit counters are per-process.
- `/compute/*` endpoints enforce input size caps, timeouts, and run user code in
  a sandboxed subprocess with a minimal environment whitelist.

限流计数为单进程范围；`/compute/*` 有输入上限、超时与沙箱执行。

## Session store contract (会话存储)

Memory (LRU 1000) → SQLite (WAL, per-session locks) → Postgres (`[ha]` extra,
`FUSION_SCIENCE_SESSION_STORE=postgres`). The choice is transparent to callers;
only readiness semantics change (see `/api/v1/ready`).
