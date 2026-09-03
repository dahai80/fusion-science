# Fusion-Science 🔬

> **本地科研 AI 工作台 · 专为 Apple Silicon 打造**
> *Fusion-MLX 生态衍生项目 — 完全离线、隐私优先的 Claude Science 国内替代方案*

Fusion-Science 是一个开源、本地优先的科研 AI 平台，将文献调研 → 数据计算 → 可视化绘图 → 论文撰写 → 结果溯源的全流程收拢在单一界面中。基于 Apple MLX 实现全本地推理，无需任何云端 API 依赖，完全离线可用。

## 核心功能

- **🔬 60+ 专业科学数据库连接器** — 内置 PubMed、UniProt、PDB、Ensembl、ChEMBL 等，AI 自动跨库整合数据，支持国内镜像。
- **🤖 AI 智能体自动执行计算实验** — MCP 多智能体架构，自动调用 Python/R/Jupyter 进行统计分析、组学数据处理、分子模拟。
- **📊 全栈可视化** — 2D 统计图表、3D 分子/蛋白质结构可视化、出版级图表。
- **📝 文献综述 + 论文撰写** — 批量精读、对比、综合数百篇论文；迭代生成论文正文，自动管理引用。
- **🔗 全链路可审计、可复现** — 每份图表、数据、论文片段保留完整溯源：查询来源、执行代码、参数配置、计算日志。
- **🏠 本地优先，数据隐私可控** — 所有计算本地执行，敏感测序、药物研发数据不离开本机，支持私有集群算力。

## 快速开始

```bash
# 安装
pip install fusion-science

# 完整科学计算支持
pip install "fusion-science[all]"

# 命令行界面
fusion-science

# Web UI
fusion-science-web
```

## 架构

```
fusion-science/
├── core/
│   ├── gateway.py      # LLMGateway — httpx 直连 fusion-mlx HTTP API（流式、结构化输出）
│   ├── engine.py       # 向后兼容，重新导出 LLMGateway 为 ScienceEngine
│   ├── tools.py        # ToolRegistry — MCP 兼容工具注册，OpenAI function calling
│   ├── agent.py        # ScienceAgent（工具调用循环）+ SciencePipeline（顺序/并行/主从）
│   └── pipeline.py     # PipelineFactory + 内置模板（文献、生信、分子）
├── session/            # 研究会话管理
│   ├── models.py       # ResearchSession, Artifact, ResearchContext 数据类
│   ├── store.py        # MemorySessionStore (LRU) + SQLiteSessionStore (持久化)
│   └── manager.py      # SessionManager，集成 EventBus
├── api/                # FastAPI HTTP 服务
│   ├── app.py          # create_app() 工厂，lifespan，审计自动集成
│   ├── sse.py          # SSE 流式输出（逐 token + done/error）
│   ├── middleware.py   # APIKeyMiddleware（hmac，豁免路径）
│   └── routes/         # /api/v1/health, /sessions, /chat
├── database/           # 科学数据库连接器 + 国内镜像
│   ├── aggregator.py   # DatabaseAggregator — 多库并行检索，自动去重
│   ├── chinese/        # 国产数据库连接器（NGDC, CNKI, ScienceDB）
├── compute/            # 代码执行（Python/R/Jupyter）& HPC 调度
├── visualization/      # 图表、3D 分子、蛋白质结构
├── literature/         # 检索、阅读、提取、综合、综述、引用
│   ├── search.py       # LiteratureSearch + SearchPreset（快速/专业/深度）+ PRISMA 流程
│   ├── reader.py       # LiteratureReader — LLM 深度阅读，分段摘要 & TLDR
│   ├── extractor.py    # LiteratureExtractor — PICO，结构化数据，研究类型分类
│   ├── synthesizer.py  # LiteratureSynthesizer — 共识分析，矛盾检测
│   ├── review.py       # LiteratureReviewer — 异步综述，LLM 章节生成 + PRISMA
│   ├── citation.py     # CitationManager — APA/Vancouver/BibTeX，去重，图谱，验证
│   └── paper.py        # PaperGenerator — IMRaD 论文起草
├── audit/              # 溯源追踪 & 可复现报告
└── utils/
    └── events.py       # EventBus — 异步发布/订阅，跨模块解耦 & 审计自动集成
```

### Phase 1 基础重构 (v0.2.0)

- **LLMGateway** (`core/gateway.py`): httpx.AsyncClient 直连 fusion-mlx。支持 `chat()`, `chat_stream()` (SSE), `structured_output()`，懒加载客户端。
- **ToolRegistry** (`core/tools.py`): MCP 兼容工具中心。5 个内置工具（search_literature, search_database, execute_python, generate_chart, fetch_paper）。导出 OpenAI function calling 和 MCP 工具格式。
- **EventBus** (`utils/events.py`): 异步事件总线，模块间解耦。自动集成审计追踪。
- **ScienceAgent 重构**: `_execute_tool()` 现通过 ToolRegistry 分发，不再返回 "not_implemented" 存根。
- **ScienceConfig**: 新增 `api_host`, `api_port`, `api_cors_origins` 配置。

### Phase 2 会话 + API (v0.2.0)

- **会话管理** (`session/`): ResearchSession 包含消息、产出物、上下文。MemorySessionStore (LRU 1000) + SQLiteSessionStore 持久化。SessionManager 集成 EventBus 自动事件发射。
- **FastAPI 服务** (`api/`): `create_app()` 工厂函数，lifespan + CORS + APIKeyMiddleware。路由：`/api/v1/health`, `/api/v1/sessions` (CRUD), `/api/v1/chat` (同步 + SSE 流式)。
- **SSE 流式输出** (`api/sse.py`): 逐 token 流式输出，含 done/error 事件。防缓冲头。
- **审计自动集成**: app lifespan 中的 EventBus 处理器自动记录所有事件（db_query, code_execution, llm_call, tool_executed 等）到 TraceRecorder。
- **CLI serve 命令**: `fusion-science serve [--host] [--port] [--reload]` 启动 uvicorn API 服务。

### Phase 3 文献系统 (v0.3.0)

五层文献架构，LLM 驱动深度分析 + 规则降级：

- **LiteratureSearch** (`literature/search.py`): SearchPreset 级别 — QUICK (10 篇, <5s), PROFESSIONAL (30 篇, <15s), DEEP (100 篇, <60s 含 PRISMA 流程)。去重 + 相关性排序。
- **LiteratureReader** (`literature/reader.py`): LLM 驱动论文深度阅读。分段摘要、TLDR 生成、方法学评估、优劣势分析。无 LLM 时降级为基础阅读。
- **LiteratureExtractor** (`literature/extractor.py`): 结构化数据提取 — PICO（人群/干预/对照/结局）、研究类型分类（RCT/队列/荟萃分析等）、样本量、p 值、效应量、局限性。无 LLM 时规则降级。
- **LiteratureSynthesizer** (`literature/synthesizer.py`): 多论文共识分析。共识度评分 (-1.0~1.0)，矛盾检测，研究缺口识别，趋势分析。关键词频率降级路径。
- **LiteratureReviewer** (`literature/review.py`): **Breaking**: `analyze_papers()` 改为 `async`。通过 LLM 基于共识数据生成 IMRaD 章节，或基于规则的主题聚类。PRISMA 流程图支持。
- **CitationManager** (`literature/citation.py`): APA/Vancouver/BibTeX 格式化，自动 key 生成，去重，引用图谱（基于关键词关联），引用验证。
- **DatabaseAggregator** (`database/aggregator.py`): 多数据库并行检索（PubMed/UniProt/PDB/Ensembl/ChEMBL）。异步信号量控制并发，结果合并去重，统一排序。

### Phase 7 专业智能体 + MCP + 合规 (v0.4.0)

- **专业智能体系统** (`core/agents/`): 5 个专业智能体 + QueryRouterAgent 关键词路由。LiteratureAgent, DataAgent, VizAgent, WriterAgent, ErrorAnalysisAgent。失败时错误升级。
- **7 个新工具** (`core/tools.py`): ToolRegistry 共 12 个工具。
- **MCP 服务器** (`mcp_server.py`): JSON-RPC 2.0 + SSE 传输，支持 Claude Desktop / Cursor 集成。
- **增强 API 路由**: `/search`, `/analyze`, `/visualize`, `/review`, `/sessions/{id}/audit`。
- **ComplianceChecker** (`audit/compliance.py`): 4 维合规检查 — 数据出境、算法备案、伦理审查、敏感数据。

### Phase 8 API 覆盖 (v0.5.0)

- **数据库路由**: GET `/api/v1/databases` + GET `/api/v1/databases/{name}/status`。
- **流水线路由**: GET `/api/v1/pipelines` + POST `/api/v1/pipelines/{name}/run`。
- **模型路由**: GET `/api/v1/models`。会话历史端点。
- **MCP SSE 传输**: GET `/mcp/sse`，带 ping 保活。
- **232 测试通过**，ruff clean。

### Phase 9 P2 增强 (v0.6.0)

- **多模型切换** (F-31): `LLMGateway.set_model()`, `set_model_for_role()`, `get_model_for_role()`。配置字段：`model_reasoning`, `model_summarization`, `model_code`。API: PUT `/api/v1/models/current`, GET/PUT `/api/v1/models/roles`。
- **论文写作增强** (F-32): `PaperGenerator` IMRaD 章节生成，LLM 驱动写作，图例生成，代码生成方法，章节平衡检查。
- **引用图谱 API** (F-34): GET `/api/v1/citations/graph`, POST `/api/v1/citations/add`, GET `/api/v1/citations/bibliography`。
- **数学解释器** (F-35): `MathExplainer`，12 个统计公式模式，LaTeX 符号转换，LLM 增强解释。API: POST `/api/v1/math/explain`, POST `/api/v1/math/explain-text`。
- **269 测试通过**，ruff clean。

### Phase 10 剩余 P2 功能 (v0.7.0)

- **分子可视化 API** (F-24): POST `/api/v1/viz/molecule/smiles`, POST `/api/v1/viz/molecule/pdb`。rdkit/py3Dmol 不可用时 2D 降级。
- **蛋白质可视化 API** (F-25): POST `/api/v1/viz/protein`, POST `/api/v1/viz/protein/compare`。py3Dmol 3D 渲染，支持样式/颜色控制。
- **Jupyter 集成 API** (F-26): POST `/api/v1/compute/jupyter/execute`, GET `/api/v1/compute/jupyter/kernels`。内核生命周期管理。
- **代码生成 API** (F-30): POST `/api/v1/compute/code-gen`, POST `/api/v1/compute/code-gen/batch`。规则 + LLM 驱动分析代码。
- **合规 API** (F-29): POST `/api/v1/compute/compliance`。4 维完整合规检查，集成审计追踪。
- **离线模式增强** (F-33): 网络探测自动检测离线 + `FUSION_OFFLINE_MODE` 环境变量。GET `/api/v1/system/status`, GET `/api/v1/system/connectivity`。
- **283 测试通过**，ruff clean。

### Phase 11 非功能需求 (v0.8.0)

- **连接监控与自动重连** (NF-04): `ConnectionMonitor` (`core/retry.py`) — 定期健康检查，连续失败跟踪，连接状态（connected/disconnected）。`retry_with_backoff()` 指数退避 + 抖动，用于 LLM 网关调用。
- **LLMGateway 重试** (NF-01/04): `chat()` 在 ConnectError/ReadTimeout/PoolTimeout 时用 `retry_with_backoff()` 包装 HTTP 调用。响应时间跟踪（`get_avg_response_time()`），连接统计（`get_connection_stats()`）。
- **安全密钥存储** (NF-03): `utils/keychain.py` — macOS Keychain 集成（通过 `security` CLI）。`SecureConfig` 高级封装 + 内存降级。
- **自定义工具注册** (NF-05): `api/routes/tools.py` — POST/GET/DELETE `/api/v1/tools` 运行时 MCP 自定义工具注册。
- **审计完整性验证** (NF-08): `audit/integrity.py` — `AuditIntegrityChecker` 验证会话覆盖率、父引用、溯源链完整性、缺失参数、失败条目诊断。API: GET `/api/v1/sessions/{id}/audit/integrity`, GET `/api/v1/sessions/{id}/audit/provenance-integrity`。
- **安全 API** (NF-03): `api/routes/security.py` — POST/GET/DELETE `/api/v1/security/keys` API key 生命周期管理（值不暴露在响应中）。
- **增强系统状态** (NF-01): `/api/v1/system/status` 新增连接状态和性能指标。
- **324 测试通过**，ruff clean。

### Phase 12 国产数据库 + 镜像智能路由 (v0.9.0)

- **NGDC 连接器** (F-18/T5.4): `NGDCConnector` — 国家基因组科学数据中心，通过 `sub_db` 参数支持 GSA/GWH/OMIX 子数据库。环境变量覆盖：`FUSION_SCI_NGDC_URL`。
- **CNKI 连接器** (F-18/T5.5): `CNKIConnector` — 中国知网，搜索支持 `search_type` 过滤，获取含机构/基金详情。环境变量覆盖：`FUSION_SCI_CNKI_URL`。
- **ScienceDB 连接器** (F-18/T5.6): `ScienceDBConnector` — 科学数据银行，数据集搜索/获取含 DOI 和许可信息。环境变量覆盖：`FUSION_SCI_SCIENCEDB_URL`。
- **CHINESE_CONNECTORS 注册表**: `database/chinese/__init__.py` — 字典映射 `"ngdc"/"cnki"/"scidb"` 到连接器类。
- **MirrorRouter 智能路由** (F-20/T5.3): 延迟测试（`test_latency`, `test_all_latency`），自动切换模式（`enable_auto_switch`），智能 URL 选择（`smart_get_url` — 选取更快端点）。API: `GET /system/mirrors/latency`, `GET /system/mirrors/status`, `POST /system/mirrors/auto-switch`。
- **363 测试通过**，ruff clean。

### Phase 13 v1.0.0 正式发布 (v1.0.0)

全部计划功能和非功能需求已完成。首个生产就绪版本。

**功能总览 (F-01 ~ F-35)：**

| 分类 | 功能 |
|---|---|
| 核心引擎 | LLMGateway（流式、结构化输出、重试），ScienceAgent + SciencePipeline，ToolRegistry（12+ 工具） |
| 会话 & API | FastAPI 服务 + SSE 流式，会话 CRUD，API key 中间件，CORS |
| 文献 | 五层系统：Search → Reader → Extractor → Synthesizer → Reviewer，CitationManager（APA/Vancouver/BibTeX），PaperGenerator |
| 数据库 | 8 个海外连接器（PubMed, UniProt, PDB, Ensembl, ChEMBL 等）+ 3 个国产连接器（NGDC, CNKI, ScienceDB），DatabaseAggregator，MirrorRouter 智能路由 |
| 计算 | PythonExecutor, RExecutor, JupyterKernel, HPCScheduler, CodeGenerator |
| 可视化 | 2D 图表，3D 分子（SMILES/PDB），蛋白质结构 |
| 智能体 | 5 个专业智能体 + QueryRouterAgent，MCP 服务器（JSON-RPC 2.0 + SSE） |
| 数学 | MathExplainer，12 个统计公式模式 + LaTeX |
| 审计 | TraceRecorder，溯源链，ComplianceChecker（4 维），AuditIntegrityChecker |
| 安全 | macOS Keychain 集成，SecureConfig，API key 生命周期管理 |

**非功能需求 (NF-01 ~ NF-08)：**

| ID | 需求 | 实现 |
|---|---|---|
| NF-01 | 缓存 <1s 响应，非缓存 <5s | 响应时间跟踪，离线缓存，连接统计 |
| NF-02 | 冷启动 <2s | 懒加载客户端，延迟导入 |
| NF-03 | 安全密钥存储 | macOS Keychain + SecureConfig + 内存降级 |
| NF-04 | 自动重连 | ConnectionMonitor + retry_with_backoff（含抖动） |
| NF-05 | 自定义工具注册 | 运行时 MCP 工具 CRUD via API |
| NF-06 | 完整审计追踪 | EventBus 自动集成，TraceRecorder，溯源链 |
| NF-07 | 离线优先 | 镜像降级，SQLite 缓存，FUSION_OFFLINE_MODE |
| NF-08 | 审计完整性 | IntegrityChecker 验证覆盖率、链、参数 |

- **374 测试通过**，ruff clean，全部阶段完成。

### 补丁版本 (v1.0.1 ~ v1.0.8)

| 版本 | 变更 |
|---|---|
| v1.0.1 | 许可证改为 Apache-2.0（#6, #7）；代码库 ruff-format 清理；CI 强制 `ruff check` + `ruff format --check`。 |
| v1.0.2 | 推理引擎经 `fusion-gateway`（:11432）路由，不再直连 MLX（:11434）；`_MLX_STATUS_URL` 仍走 :11434 做服务发现（#5）。 |
| v1.0.3 | 统一服务端口 8200→11462（#9, PR #10）；CI 安装 `.[test,api]` 以运行 FastAPI 测试；`GET /system/mirrors/latency` 改为并行探测，单端点 3s 上限，单镜像失败不再整体超时（#8）。 |
| v1.0.4 | 验收通过：ToolRegistry 8→12 个 MCP 兼容工具（新增 `visualize_molecule`, `visualize_protein`, `explain_math`, `generate_citation`）；纠正连接器/文档计数（5 海外 + 3 国产）；修复无模型加载时的 MLX 自动探测测试。 |
| v1.0.5 | 统一 API 端口 11462（`serve` + `start.sh` 单一来源）；强制 ruff `I001` 导入排序；CI 矩阵锁定 Python 3.12（fusion-core `requires-python>=3.12`）（#16, PR #17）。 |
| v1.0.6 | 对抗审计安全加固：RCE 沙箱修复（最小环境白名单、进程组 kill、临时文件传数据——不内联源码）；图表类型白名单 + 输入注入封堵；Jupyter `code`/`timeout` 边界；默认拒绝的 API 认证 + 仅回环默认绑定；MLX 内存压力 fail-closed；每会话丢失更新锁 + WAL SQLite；IDOR 范围审计追踪；增量审计持久化；缓存字节预算 + Retry-After 抖动；网关 `total_deadline` 重试上限；HPC shell 注入防护（标识符白名单、`shlex.quote`、`O_EXCL` 脚本写入）。 |
| v1.0.7 | 企业审计加固（PR #20, #21）：全路由 4xx/5xx + `detail` 错误契约；会话/审计追踪 IDOR 属主范围；MCP 服务挂载到 `/mcp`（10/12 工具经 JSON-RPC 2.0 + SSE 暴露）；DatabaseAggregator 扩展到 8 库并行去重；defusedxml 防 XXE 解析；删除被遮蔽的遗留 `chinese.py`。产品发布门审计（0903）关闭剩余阻塞项。 |
| v1.0.8 | 企业生产加固（PR #23, #25）：用户代码 OS 级沙箱隔离（macOS `sandbox-exec` / Linux `bwrap`，rlimit 降级）；内置 RBAC + JWT（3 角色：admin/science/viewer，HS256，无外部 IdP）；基于共享 SQLite 存储的多 worker 支持；防篡改审计留存（按年龄/数量裁剪）+ NDJSON SIEM 导出；无需重启的运行时 API key 轮换；macOS Keychain 密钥解析。详见下方**企业安全**。 |

## 企业安全 (v1.0.8)

v1.0.8 关闭代码级企业生产缺口。配置通过环境变量驱动，无需外部身份提供商（本地优先）。

### RBAC + JWT

三个内置角色按"路由前缀 × HTTP 方法"管控所有 `/api/v1/*` 路由：

| 角色 | 权限 |
|---|---|
| `admin` | 所有路由、所有方法（向后兼容遗留单 key 的默认角色）。 |
| `science` | 读 + 科研流程：search, databases, citations, math, compute, chat, analysis, visualize, review, audit, pipelines, sessions, tools（只读）。无 system/security/model 变更。 |
| `viewer` | 只读：search, databases, citations, math, 可视化, models（列表）, health, metrics。无 compute、无 chat、无变更。 |

通过环境变量或 key 文件配置 key：

```bash
# 多角色 key（逗号或换行分隔的 "role:key" 对）
export FUSION_SCIENCE_API_KEYS="admin:admin-secret,science:sci-secret,viewer:viewer-secret"

# 遗留单 key（仍为 admin，向后兼容）
export FUSION_SCIENCE_API_KEY="admin-secret"
```

在 `POST /api/v1/auth/token` 用 key 换取短时 JWT（1 小时，HS256，含角色声明），作为 `Authorization: Bearer <jwt>` 发送。key 只能铸造同级或更低权限的 token——不可提权。`GET /api/v1/auth/whoami` 返回解析后的主体。

JWT 签名密钥默认取 `FUSION_SCIENCE_JWT_SECRET`；未设置则从遗留 API key 派生。

### 运行时密钥轮换

```bash
# 指向一个运维可重写的 key 文件，无需重启进程
export FUSION_SCIENCE_API_KEYS_FILE="/etc/fusion-science/api-keys.txt"
```

中间件每次请求重新读取已配置 key，因此重写文件即实时轮换——无需重启。确认重载并审计触发者：

```bash
curl -X POST http://localhost:11462/api/v1/security/rotate-keys \
  -H "X-API-Key: admin-secret"
# {"rotated": true, "actor": "apikey:admin-s", "total": 3, "by_role": {...}, "keys": [{"role":"admin","key":"****cret"}]}
```

密钥绝不入站过线——端点仅返回掩码确认，绝不经 HTTP 修改环境变量。

### 多 Worker

```bash
export FUSION_SCIENCE_WORKERS=4
./start.sh start   # uvicorn --workers 4
```

会话经共享 SQLite 存储（WAL 模式 + `busy_timeout=5000`）跨 worker 安全。EventBus/ScienceCache/MirrorRouter 仍每 worker 独立（读多写少，可接受）。需严格单进程语义时用 `--workers 1`。

### 密钥存储（macOS Keychain）

设置 `FUSION_SCIENCE_KEYCHAIN=1` 启用：从 macOS Keychain（服务名 `fusion-science`）解析推理引擎 API key 与 JWT 签名密钥，而非明文环境变量/配置文件。Keychain 优先级高于 MLX `settings.json` 文件；显式环境变量始终优先。通过 `POST/GET/DELETE /api/v1/security/keys` 管理已存密钥。

### 审计留存与 SIEM 导出

防篡改追踪记录器（SHA-256 哈希链、增量原子持久化）现在按年龄和数量在启动时裁剪：

```bash
export FUSION_SCIENCE_AUDIT_MAX_AGE_DAYS=180   # 默认 90
export FUSION_SCIENCE_AUDIT_MAX_SESSIONS=2000  # 默认 1000
```

导出会话审计追踪为 NDJSON（每行一条）供 SIEM/ELK/Splunk 接入：

```bash
curl http://localhost:11462/api/v1/sessions/{session_id}/audit/export \
  -H "X-API-Key: admin-secret"
# Content-Type: application/x-ndjson
```

### 生产部署清单

- 默认绑定回环（`127.0.0.1`）。要暴露到局域网须设 `FUSION_SCIENCE_API_HOST=0.0.0.0` **并**配置 API key。
- 通过 `FUSION_SCIENCE_API_KEYS` 或轮换 key 文件配置角色范围 key。
- 设置强 `FUSION_SCIENCE_JWT_SECRET`（或用 Keychain）。
- 启用审计留存以限制磁盘增长。
- 多 worker 时确认共享 SQLite 存储路径在快速本地存储上。
- 当 `sandbox-exec`（macOS）或 `bwrap`（Linux）可用时，OS 沙箱隔离自动启用；仅 rlimit 降级会记录警告。

> **不在范围（已提 issue）：** 多节点 HA 部署拓扑（#24）、正式合规认证路线图（#25）、外部 OAuth2/OIDC（#22）。v1.0.8 为单节点企业就绪；HA 与认证单独跟踪。

## 国内研究环境适配

Fusion-Science 专门针对国内科研环境进行了优化：
- **全本地推理** — 基于 MLX，不依赖海外 API 服务
- **国内数据库镜像** — 中科院镜像，国家基因组科学数据中心
- **离线缓存** — 文献和分子数据集可预缓存，完全离线运行
- **合规** — 个人/实验室内部使用无需 AI 算法备案

## 数据隐私声明

Fusion-Science 是一款**本地优先**的科研工具：

- **数据不离机**：所有计算、推理、存储均在本地 Mac 完成，数据不上传至任何外部服务器
- **无遥测**：不收集用户行为、使用统计或任何形式的遥测数据
- **日志脱敏**：审计追踪模块自动过滤敏感字段（患者信息、身份证号、联系方式等），确保日志不泄露隐私
- **开源透明**：Apache 2.0 许可证，代码完全可见，可自行审计

## 许可证

Apache License 2.0
