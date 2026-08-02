# Phase 1 重构实现计划：基础架构

**用户指令**: "启动实际重构实现" — 基于 science-enhance.md Phase 1 执行代码重构

**受影响调用方**: cli.py (import ScienceEngine), agent.py (import LLMResponse, ScienceEngine), pipeline.py (import ScienceAgent, SciencePipeline, ScienceEngine), tests/test_core.py (import ScienceEngine, ModelConfig, LLMResponse — ModelConfig 已不存在)

**API 变更**: ScienceEngine → LLMGateway (向后兼容 re-export), 新增 ToolRegistry/EventBus

**数据 Schema**: LLMResponse (已有), LLMResult (新增, 含 error/parsed 字段), ToolDefinition (新增)

---

## 当前问题

1. **engine.py** — ScienceEngine 缺少流式输出、结构化输出、async context manager
2. **agent.py** — `_execute_tool()` 是 stub (返回 "not_implemented")
3. **pipeline.py** — `_load_tools()` 返回硬编码 placeholder tool definitions
4. **config.py** — 无 API server 配置字段
5. **cli.py** — 大部分命令只打印信息不执行
6. **tests/test_core.py** — 引用已删除的 `ModelConfig`，所有测试 broken
7. **audit/tracker.py** — 存在但未被其他模块调用

## 实施步骤

### Step 1: core/gateway.py — LLMGateway (替代 engine.py)

参考 fusion-health `LLMGateway` 模式，新增:
- `chat_stream()` — 流式 async generator (SSE 解析)
- `structured_output()` — prompt 注入 JSON schema + `_parse_structured()`
- `__aenter__`/`__aexit__` — async context manager
- 延迟创建 `httpx.AsyncClient`
- 错误处理：HTTP 错误返回空 LLMResult 而非 raise

### Step 2: core/tools.py — ToolRegistry

- `register(name, description, parameters, handler)` / `execute(name, arguments)` / `get_openai_tools()` / `get_mcp_tools()`
- 内置工具 handler: search_literature, search_database, execute_python, generate_chart

### Step 3: core/agent.py — 重构 ScienceAgent

- 注入 ToolRegistry 实例
- `_execute_tool()` 从 registry 查找并调用真实 handler
- SciencePipeline 接收并传递 ToolRegistry

### Step 4: utils/events.py — EventBus

- `emit(event_type, data)` / `on(event_type, handler)` / `off(event_type, handler)`
- 事件类型: db_query, code_execution, llm_call, visualization

### Step 5: config.py — 增强 ScienceConfig

新增字段: `api_host`, `api_port`, `api_cors_origins`

### Step 6: core/engine.py — 向后兼容

改为从 `gateway.py` re-export: `LLMGateway as ScienceEngine`

### Step 7: 修复测试

- 重写 `tests/test_core.py` — 移除 `ModelConfig`
- 新增 `tests/test_tools.py`, `tests/test_events.py`

### Step 8: pyproject.toml — 新增 fastapi/uvicorn 可选依赖

### Step 9: README.md 更新

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 🆕 新增 | `core/gateway.py` | LLMGateway 统一 LLM 调用 |
| 🆕 新增 | `core/tools.py` | ToolRegistry 工具注册中心 |
| 🆕 新增 | `utils/events.py` | EventBus 事件总线 |
| 🔄 重写 | `core/engine.py` | 改为 re-export gateway |
| 🔄 修改 | `core/agent.py` | 注入 ToolRegistry，真实工具执行 |
| 🔄 修改 | `core/pipeline.py` | 注入 ToolRegistry，动态工具加载 |
| 🔄 修改 | `config.py` | 新增 api_* 字段 |
| 🔄 重写 | `tests/test_core.py` | 修复 broken 测试 |
| 🆕 新增 | `tests/test_tools.py` | ToolRegistry 测试 |
| 🆕 新增 | `tests/test_events.py` | EventBus 测试 |
| 🔄 修改 | `pyproject.toml` | 新增 api extras |

## 不做的事 (Phase 2+)

- ❌ session/ 会话管理
- ❌ api/ HTTP 服务层
- ❌ agents/ 专业代理系统
- ❌ literature 模块重构
- ❌ database/ 连接器重构
- ❌ compute/visualization 模块

## 验收标准

1. `from fusion_science.core.gateway import LLMGateway, LLMResponse` 成功
2. `from fusion_science.core.engine import ScienceEngine, LLMResponse` 仍兼容
3. `pytest tests/` 全部通过
4. `ToolRegistry` 能注册/执行/导出 OpenAI tools
5. `ScienceAgent._execute_tool()` 调用真实 ToolRegistry handler
6. `EventBus` emit/on/off 正常工作
