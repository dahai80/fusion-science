# Phase 2 实现计划：会话 + API

**依据**: science-enhance.md Phase 2 (Week 3-4)
**前置**: Phase 1 已完成 — LLMGateway, ToolRegistry, EventBus, Agent 重构, Config 增强

## 任务清单

### T2.1 实现 Session 模块
**文件**: `fusion_science/session/__init__.py`, `models.py`, `manager.py`, `store.py`

**models.py**:
- `ResearchSession`: id, title, created_at, updated_at, messages, context, artifacts, trace_ids
- `ResearchContext`: papers, datasets, code_history, figures, variables
- `Artifact`: id, type (chart/code/document/paper), name, content, created_at

**manager.py** — SessionManager:
- `create_session(title?) -> ResearchSession` — 创建会话，emit EVENT_SESSION_CREATED
- `get_session(id) -> ResearchSession | None`
- `list_sessions() -> list[ResearchSession]`
- `delete_session(id)` — emit EVENT_SESSION_UPDATED
- `add_message(session_id, role, content)` — 追加消息，emit EVENT_SESSION_UPDATED
- `add_artifact(session_id, artifact)` — 追加产出物
- `get_messages(session_id) -> list[dict]`
- 内部使用 Store 持久化

**store.py** — SessionStore:
- `MemorySessionStore` — 内存存储，LRU 驱逐 (MAX_SESSIONS=1000)
- `SQLiteSessionStore` — SQLite 持久化存储，可恢复
- 共同接口: `save()`, `load()`, `delete()`, `list_all()`

### T2.2 实现 FastAPI App
**文件**: `fusion_science/api/__init__.py`, `app.py`, `middleware.py`

**app.py** — `create_app(config?) -> FastAPI`:
- `lifespan()` 上下文管理器: 初始化 LLMGateway + ToolRegistry + SessionManager, 关闭时清理
- CORS 中间件 (origins from config.api_cors_origins)
- API Key 中间件 (参考 fusion-health, env var `FUSION_SCIENCE_API_KEY`)
- include routers: sessions, chat, tools, pipelines, health

**middleware.py** — APIKeyMiddleware:
- 复用 fusion-health 模式: hmac 比较, exempt paths, X-API-Key header

### T2.3 实现 SSE 流式输出
**文件**: `fusion_science/api/sse.py`

- `_sse_generator(tokens)` — 从 LLMGateway.chat_stream() 的 AsyncGenerator 生成 `data: {json}\n\n` 格式
- `sse_response(tokens)` — 包装为 StreamingResponse, media_type="text/event-stream"
- 完全复用 fusion-health 的 sse.py 模式

### T2.4 实现 API Routes
**文件**: `fusion_science/api/routes/__init__.py`, `sessions.py`, `chat.py`, `tools.py`, `pipelines.py`, `health.py`

**sessions.py**:
- `POST /sessions` — 创建会话
- `GET /sessions` — 列出所有会话
- `GET /sessions/{id}` — 获取会话详情
- `GET /sessions/{id}/history` — 获取对话历史
- `DELETE /sessions/{id}` — 删除会话

**chat.py**:
- `POST /sessions/{id}/chat` — 发送消息，返回 SSE 流式响应
- `POST /sessions/{id}/chat/sync` — 发送消息，返回同步响应

**tools.py**:
- `GET /tools` — 列出可用工具
- `POST /tools/{name}/execute` — 执行工具

**pipelines.py**:
- `GET /pipelines` — 列出 pipeline 模板
- `POST /pipelines/{name}/run` — 执行 pipeline

**health.py**:
- `GET /health` — API + MLX engine 健康检查
- `GET /models` — 可用模型列表

### T2.5 CLI serve 命令
**文件**: 修改 `fusion_science/cli.py`

- 将 `web` 命令改为 `serve` 命令
- `fusion-science serve --host 0.0.0.0 --port 8300`
- 使用 uvicorn 运行 `create_app()`

### T2.6 审计自动集成
**文件**: 修改 `fusion_science/session/manager.py`, `fusion_science/api/routes/chat.py`

- SessionManager 事件 emit 时，EventBus handler 自动调用 TraceRecorder
- chat 路由自动记录 LLM 调用到审计
- 工具执行自动记录到审计

### T2.7 API 集成测试
**文件**: `tests/test_api.py`

- 使用 httpx.AsyncClient + FastAPI TestClient 测试
- 测试会话 CRUD, 聊天流, 工具列表, 健康检查, pipeline 列表

## 文件结构

```
fusion_science/
├── session/
│   ├── __init__.py      # export SessionManager, ResearchSession
│   ├── models.py        # ResearchSession, ResearchContext, Artifact
│   ├── manager.py       # SessionManager (create/get/list/delete/add_message/add_artifact)
│   └── store.py         # MemorySessionStore, SQLiteSessionStore
├── api/
│   ├── __init__.py      # export create_app
│   ├── app.py           # create_app() factory + lifespan
│   ├── sse.py           # _sse_generator + sse_response
│   ├── middleware.py     # APIKeyMiddleware
│   └── routes/
│       ├── __init__.py
│       ├── sessions.py  # 会话 CRUD endpoints
│       ├── chat.py      # 聊天 + SSE 流式 endpoints
│       ├── tools.py     # 工具查询/执行 endpoints
│       ├── pipelines.py # Pipeline 模板/执行 endpoints
│       └── health.py    # 健康检查 + 模型列表
├── cli.py               # 增加 serve 命令
tests/
└── test_api.py          # API 集成测试
```

## 验收标准

- [ ] `fusion-science serve --port 8300` 启动 HTTP 服务
- [ ] `POST /api/v1/sessions/{id}/chat` 返回 SSE 流式响应
- [ ] 会话可持久化到 SQLite 并恢复
- [ ] 现有 101 + 新增测试全部通过

## 实现顺序

1. session/models.py + store.py + manager.py (T2.1)
2. api/sse.py + middleware.py (T2.3, 部分 T2.2)
3. api/app.py + routes/* (T2.2, T2.4)
4. cli.py serve 命令 (T2.5)
5. 审计自动集成 (T2.6)
6. test_api.py (T2.7)
