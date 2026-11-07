---
doc_id: fusion-science/get-started
product: fusion-science
class: app
entry: service
doc_type: get-started
title: { zh: "快速开始", en: "Get Started" }
audience: engineer
status: draft
version: 1.0.17
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - README.md
  - start.sh
  - pyproject.toml
---

# Get Started (快速开始)

## Prerequisites (前置条件)

- macOS on Apple Silicon (MLX) or a Linux box for API-only use.
- Python 3.12. In the fusion monorepo, use the shared venv:
  `cd /Users/dahai/fusion && source .venv/bin/activate`.
- fusion-mlx running on :11434 (or fusion-gateway on :11432). Without a loaded
  model, LLM-driven layers degrade to rule-based fallbacks; rule-based features
  still work.

- macOS（Apple Silicon）或 Linux；Python 3.12（monorepo 内使用共享 `.venv`）。
- 需要 fusion-mlx（:11434）或 fusion-gateway（:11432）；未加载模型时 LLM 层退化为规则兜底。

## Install (安装)

```bash
cd /Users/dahai/fusion/fusion-science
pip install -e ".[test,api]"          # development
pip install -e ".[all]"               # everything: mlx, jupyter, r, molecule, oidc, ha, security
```

## Start the service (启动服务)

```bash
./start.sh start     # uvicorn on http://127.0.0.1:11462, waits for /api/v1/health
./start.sh status    # exit 0 = running
./start.sh stop
./start.sh restart
```

Or without start.sh:

```bash
fusion-science serve              # same API server; fusion-science-web is an alias
```

Logs land in `logs/stdout.log` and `logs/stderr.log`; the PID file is
`.fusion-science.pid`. Both are produced by `start.sh`.

日志位于 `logs/stdout.log`、`logs/stderr.log`，PID 文件为 `.fusion-science.pid`。

## Verify (验证)

```bash
curl http://127.0.0.1:11462/api/v1/health
curl http://127.0.0.1:11462/api/v1/ready    # 503 when the session store is down
curl http://127.0.0.1:11462/api/v1/databases
```

Expect `{"status": "ok", ...}` from the health probe. `/api/v1/ready` returns 503
when a shared (Postgres) session store is unreachable.

健康检查返回 `{"status": "ok", ...}`；共享会话存储不可用时 `/api/v1/ready` 返回 503。

## First request (第一个请求)

```bash
# create a session, then stream a chat completion
curl -X POST http://127.0.0.1:11462/api/v1/sessions -H "Content-Type: application/json" -d '{"title": "demo"}'
curl -X POST http://127.0.0.1:11462/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session-id-from-above>", "message": "Summarize CRISPR delivery methods"}'
```

With auth enabled (see `docs/config-reference.md`), add
`X-API-Key: <key>` or `Authorization: Bearer <jwt>` to every request.

启用鉴权后，每个请求需带 `X-API-Key` 或 `Authorization: Bearer` 头。

## What you no longer have to do (你不再需要做什么)

- You do not need to install a separate vector DB or cloud API key for literature
  search — connectors + SQLite cache run in-process. Limitation: full-text
  fetching still needs network access to the source sites or their mirrors.
- You do not need to wire an LLM client yourself — the gateway auto-discovers
  the MLX endpoint (:11432 via fusion-gateway, :11434 for status discovery) at
  startup. Limitation: if neither port answers, start fusion-mlx first.

- 无需自建向量库或云端 API Key：连接器与 SQLite 缓存均在进程内。
- 无需手写 LLM 客户端：启动时自动发现 MLX 端点；两端口都不可达时需先启动 fusion-mlx。

## Next steps (下一步)

- `docs/api-reference.md` — endpoint catalog
- `docs/config-reference.md` — every `FUSION_SCIENCE_*` variable
- `docs/troubleshooting.md` — common failures and fixes
- README "Enterprise Security" sections — RBAC, OIDC, MFA, TLS, HA
