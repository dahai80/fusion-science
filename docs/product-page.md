---
doc_id: fusion-science/product-page
product: fusion-science
class: app
entry: service
doc_type: product-page
title: { zh: "Fusion-Science 产品页", en: "Fusion-Science Product Page" }
audience: engineer
status: draft
version: 1.0.17
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - README.md
  - pyproject.toml
  - start.sh
  - fusion_science/api
---

# Fusion-Science Product Page (产品页)

## What it is (产品定位)

Fusion-Science is a local-first scientific research AI workbench for Apple Silicon.
It unifies literature review, data computation, visualization, paper writing, and
result traceability behind one FastAPI service. All inference runs locally through
the fusion-mlx engine (routed via fusion-gateway); no cloud API is required.

Fusion-Science 是一个本地优先的科研 AI 工作台：文献综述、数据计算、可视化、论文写作
与结果溯源统一在一个 FastAPI 服务之后。推理全部通过 fusion-mlx（经 fusion-gateway 路由）
本地完成，不依赖云端 API。

## Grid position (契约网格)

| Key | Value | Evidence (证据) |
|---|---|---|
| class | app | A domain application in the fusion monorepo, not shared infrastructure |
| entry | service | `fusion-science-web` is an alias for `serve` (`fusion_science/cli.py`); the "web UI" is a single static page served by the FastAPI app. The product surface is the REST API + CLI, managed by `start.sh` on port 11462 |

## What users no longer have to do (用户不再需要做什么)

- No more cross-database manual searching: the DatabaseAggregator queries 8
  connectors in parallel (PubMed, UniProt, PDB, Ensembl, ChEMBL, NGDC, CNKI,
  ScienceDB) with dedup and unified ranking. Constraint: connector coverage
  follows each site's public interface; a site change can break a connector
  until it is patched.
- No more copy-pasting analysis code between tools: the agent executes
  Python/R/Jupyter in an OS-level sandbox and records every step. Constraint:
  sandbox profiles are OS-specific (`sandbox-exec` on macOS, `bwrap` on Linux);
  an unsupported OS degrades to rlimit-only isolation.
- No more manual provenance bookkeeping: every DB query, code execution, LLM
  call, and tool run is auto-recorded to a SHA-256 hash-chained audit trail.
  Constraint: the trail is per-session; cross-session correlation is not built.

- 不再需要跨库手工检索：DatabaseAggregator 并行查询 8 个连接器并去重排序。
  约束：连接器依赖各站点公开接口，站点改版可能导致连接器失效。
- 不再需要在工具间粘贴分析代码：智能体在 OS 级沙箱中执行 Python/R/Jupyter 并记录全程。
  约束：沙箱依赖 macOS `sandbox-exec` / Linux `bwrap`，其他系统退化为 rlimit 隔离。
- 不再需要手工维护溯源：所有查询、执行、调用自动进入 SHA-256 哈希链审计。
  约束：审计按会话记录，不提供跨会话关联。

## Key capabilities (核心能力)

- Literature 5-layer pipeline: Search (QUICK/PROFESSIONAL/DEEP presets) → Reader
  → Extractor (PICO) → Synthesizer (consensus/contradiction) → Reviewer (IMRaD,
  PRISMA). Limitation: LLM-driven layers fall back to rule-based behavior when
  no model is loaded.
- Citations: APA/Vancouver/BibTeX formatting, dedup, citation graph, verification.
- Visualization: 2D charts (whitelisted chart types), 3D molecule (SMILES/PDB),
  protein structures. Optional extras `[molecule]` add rdkit/py3Dmol.
- MCP server: JSON-RPC 2.0 + SSE at `/mcp`, exposing 10 of 12 registry tools to
  Claude Desktop / Cursor.
- Enterprise controls (all opt-in, env-driven): RBAC + JWT (admin/science/viewer),
  external OIDC, TOTP MFA, TLS termination, AES-256-GCM audit encryption-at-rest,
  DSAR endpoints, Postgres HA session store, SIEM audit sink, JWT idle timeout.

- 文献五层流水线、引用管理、2D/3D 可视化、MCP 服务器（JSON-RPC 2.0 + SSE）。
- 企业级控制项均为可选并通过环境变量开启：RBAC+JWT、OIDC、TOTP MFA、TLS、
  审计静态加密、DSAR、Postgres 高可用会话存储、SIEM 审计外发、JWT 空闲超时。

## Interfaces (入口)

| Interface | How | Source |
|---|---|---|
| REST API | `./start.sh start` → `http://127.0.0.1:11462/api/v1/*` | `start.sh`, `fusion_science/api/app.py` |
| CLI | `fusion-science` (chat / search / serve ...) | `pyproject.toml [project.scripts]` |
| MCP | `/mcp` (JSON-RPC 2.0), `/mcp/sse` | `fusion_science/mcp_server.py` |

## Non-goals (边界)

- Not a hosted multi-tenant SaaS: the default bind is loopback-only; LAN or
  cluster exposure requires explicit host/key configuration.
- Not a cloud literature gateway: full-text access depends on local network
  reachability; `FUSION_OFFLINE_MODE=1` and mirror routing exist for domestic
  and offline environments.
- 非多租户 SaaS（默认仅回环绑定）；非云端文献网关（离线模式与镜像路由服务于
  本地/内网环境）。

## Status (状态)

v1.0.17. See `CHANGELOG.md` and README release history for the phase-by-phase
record. The compliance roadmap in `architecture/compliance-matrix.md` tracks the
remaining certification gaps; it is a tracking document, not a certification claim.
