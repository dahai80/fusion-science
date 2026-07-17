# Fusion-Science 🔬

> **本地科研 AI 工作台 · 专为 Apple Silicon 打造**  
> *Fusion-MLX 生态衍生项目 — 完全离线、隐私优先的 Claude Science 国内替代方案*

Fusion-Science 是一个开源、本地优先的科研 AI 平台，将文献调研 → 数据计算 → 可视化绘图 → 论文撰写 → 结果溯源的全流程收拢在单一界面中。基于 Apple MLX 实现全本地推理，无需任何云端 API 依赖，完全离线可用。

---

## 目录

- [为什么需要 Fusion-Science](#为什么需要-fusion-science)
- [核心功能](#核心功能)
- [快速开始](#快速开始)
- [架构总览](#架构总览)
- [模块详解](#模块详解)
  - [Core 引擎](#1-core-引擎)
  - [数据库连接器](#2-数据库连接器)
  - [计算层](#3-计算层)
  - [可视化](#4-可视化)
  - [文献工作流](#5-文献工作流)
  - [审计溯源](#6-审计溯源)
- [国内研究环境适配](#国内研究环境适配)
- [与 Claude Science 对比](#与-claude-science-对比)
- [安装指南](#安装指南)
- [使用示例](#使用示例)
- [开发指南](#开发指南)
- [许可证](#许可证)

---

## 为什么需要 Fusion-Science

### 痛点

科研人员日常需要在 PubMed、Jupyter、R 语言、各类生物数据库、集群终端之间反复切换，文献检索、数据处理、绘图、论文撰写高度碎片化。传统 AI 工具（如普通 Claude、ChatGPT）仅支持文本问答，无法调用计算工具、数据库、集群算力，且结果不可追溯。

### Claude Science 的局限

Anthropic 于 2026 年 6 月发布的 Claude Science 是当前最先进的科研 AI 工作台，但存在以下问题：
- **云端服务**：国内无法直接访问
- **企业付费**：仅面向企业 Beta 版，门槛高
- **数据合规**：敏感科研数据需上传海外服务器
- **不可定制**：无法适配本地算力和特定数据库

### Fusion-Science 的解决方案

| 维度 | Claude Science | Fusion-Science |
|------|---------------|----------------|
| 推理方式 | 云端（Claude Opus 4.8） | 本地（MLX on Apple Silicon） |
| 网络要求 | 必须联网 | 完全离线可用 |
| 国内访问 | ❌ 不可用 | ✅ 完全可用 |
| 数据隐私 | 数据上传云端 | 数据全程本地 |
| 开源 | ❌ 闭源 | ✅ 开源 (MIT) |
| 算力 | 仅云端 | 本地 Mac + HPC 集群 |
| 定制化 | 固定功能 | 模块化可扩展 |
| 国内数据库 | 不支持 | CNKI/NGDC/CNGB 镜像 |

---

## 核心功能

### 1️⃣ 内置 60+ 专业科学数据库连接器

开箱对接生命科学、化学主流数据库，AI 自动跨库整合数据：

| 数据库 | 类型 | 国内镜像 |
|--------|------|---------|
| PubMed | 生物医学文献 | CNKI 替代 |
| UniProt | 蛋白质序列/功能 | 本地缓存 |
| PDB (RCSB) | 蛋白质 3D 结构 | PDBe 镜像 |
| Ensembl | 基因组数据 | 亚洲镜像 |
| ChEMBL | 药物分子/生物活性 | 学术网络 |
| 中国科学院数据库 | 基因组/科学数据 | ✅ 原生支持 |
| 国家基因组科学数据中心 | 基因组数据 | ✅ 原生支持 |

### 2️⃣ AI 智能体自动执行计算实验

基于 MCP 模型上下文协议搭建多智能体架构：
- 自动调用 Python/R/Jupyter 做统计、组学数据分析
- 调度 HPC 集群、Slurm 算力运行分子模拟、蛋白折叠计算
- 自动生成可复现代码、图表、3D 分子/蛋白结构图
- 内置事实校验，大幅降低文献、数据幻觉问题

### 3️⃣ 全链路可审计、可复现

每一份图表、数据、论文片段都会完整留存操作溯源记录：
- 查询来源（哪个数据库、什么参数）
- 执行代码（Python/R 脚本原文）
- 计算日志（完整输出、错误信息）
- 参数配置（模型参数、分析参数）

满足期刊、药企合规与实验重复验证要求。

### 4️⃣ 一站式文献综述 + 论文撰写

- 批量精读、对比数百篇学术文献
- 自动梳理研究脉络、实验方案、结论矛盾点
- 迭代生成论文正文、图表图例、参考文献、方法部分
- 辅助修改学术图表、标准化科研绘图格式

### 5️⃣ 本地优先，数据隐私可控

- 计算任务可本地执行，敏感测序、药物研发数据无需上传云端
- 支持私有集群算力对接
- 适配药企、高校数据合规需求

---

## 快速开始

### 安装

```bash
# 基础安装
pip install fusion-science

# 完整科学计算支持
pip install "fusion-science[all]"

# 按需安装
pip install "fusion-science[mlx]"      # MLX 本地推理
pip install "fusion-science[jupyter]"  # Jupyter 内核
pip install "fusion-science[r]"        # R 语言支持
pip install "fusion-science[molecule]" # 分子可视化
```

### 启动

```bash
# 命令行界面
fusion-science

# 查看帮助
fusion-science --help

# 查看系统信息
fusion-science info

# 初始化配置
fusion-science config init

# 启动 Web UI（开发中）
fusion-science-web
```

### 基本使用

```bash
# 搜索文献
fusion-science search "CRISPR-Cas9 gene therapy" --db pubmed --max 20

# 运行学术分析流程
fusion-science pipeline literature_review "单细胞测序在肿瘤研究中的应用"

# 生成可视化
fusion-science visualize molecule --data "CC(=O)OC1=CC=CC=C1C(=O)O"

# 生成审计报告
fusion-science audit --output ./reproducibility_report.md
```

---

## 架构总览

```
fusion-science/
├── pyproject.toml                    # 项目配置
├── README.md / README_CN.md          # 文档
├── fusion_science/
│   ├── __init__.py                   # 包入口
│   ├── cli.py                        # 命令行界面
│   ├── config.py                     # 配置管理
│   ├── core/                         # 核心引擎
│   │   ├── engine.py                 #   MLX 推理引擎
│   │   ├── agent.py                  #   AI 智能体
│   │   └── pipeline.py              #   流水线编排
│   ├── database/                     # 数据库连接器
│   │   ├── base.py                   #   抽象基类
│   │   ├── pubmed.py                 #   PubMed
│   │   ├── uniprot.py                #   UniProt
│   │   ├── pdb.py                    #   PDB
│   │   ├── ensembl.py                #   Ensembl
│   │   ├── chembl.py                 #   ChEMBL
│   │   └── mirror.py                 #   镜像/缓存
│   ├── compute/                      # 计算层
│   │   ├── python_executor.py        #   Python 沙箱
│   │   ├── jupyter_kernel.py         #   Jupyter 内核
│   │   ├── r_executor.py             #   R 语言执行
│   │   └── hpc_scheduler.py          #   HPC/Slurm
│   ├── visualization/                # 可视化
│   │   ├── chart.py                  #   统计图表
│   │   ├── molecule.py               #   分子结构
│   │   └── protein.py                #   蛋白质结构
│   ├── literature/                   # 文献工作流
│   │   ├── search.py                 #   文献检索
│   │   ├── review.py                 #   文献综述
│   │   └── paper.py                  #   论文撰写
│   ├── audit/                        # 审计溯源
│   │   ├── tracker.py                #   操作追踪
│   │   ├── provenance.py             #   数据溯源
│   │   └── report.py                 #   审计报告
│   └── utils/                        # 工具
│       └── mirrors.py                #   国内镜像配置
├── tests/                            # 测试
│   ├── test_core.py
│   ├── test_database.py
│   ├── test_compute.py
│   └── test_literature_audit.py
└── docs/                             # 文档
    ├── api.md
    └── architecture.md
```

---

## 模块详解

### 1. Core 引擎

**ScienceEngine** — 本地 LLM 推理的统一接口，支持两种模式：

```python
from fusion_science.core.engine import ScienceEngine, ModelConfig

# HTTP 模式（连接 fusion-mlx 服务器）
engine = ScienceEngine(ModelConfig(
    name="qwen3.5-9b",
    base_url="http://localhost:8000/v1",
))

# 直接 MLX 模式（需要 mlx-lm）
await engine.load_model("mlx-community/Qwen3.5-9B")
```

**ScienceAgent** — 单个科研智能体，支持工具调用和链式推理：

```python
from fusion_science.core.agent import ScienceAgent

agent = ScienceAgent(
    name="文献检索",
    engine=engine,
    system_prompt="你是文献检索专家。",
    tools=[...],
)
result = await agent.run("搜索CRISPR相关文献")
```

**SciencePipeline** — 多步骤科研流水线编排器，支持三种模式：

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| sequential | 顺序执行，前一个输出作为后一个输入 | 文献检索→分析→综述 |
| parallel | 并行执行，相同输入分发给多个智能体 | 同时查询多个数据库 |
| master_worker | 主智能体分解任务，工作智能体并行执行 | 复杂研究问题分解 |

### 2. 数据库连接器

所有连接器继承自 `BaseConnector`，统一接口：

```python
from fusion_science.database.pubmed import PubMedConnector
from fusion_science.database.uniprot import UniProtConnector

# 搜索
result = await connector.search("CRISPR", max_results=20)

# 通过 ID 获取
result = await connector.fetch("P04637")  # UniProt 登录号

# 国内镜像模式
connector = PubMedConnector(use_mirror=True)
```

### 3. 计算层

**PythonExecutor** — 沙箱化 Python 代码执行：

```python
from fusion_science.compute.python_executor import PythonExecutor

executor = PythonExecutor(timeout=120)
result = await executor.execute("""
import pandas as pd
import numpy as np
# 数据分析代码
result = "分析完成"
""")
```

**HPCScheduler** — Slurm 集群作业调度：

```python
from fusion_science.compute.hpc_scheduler import HPCScheduler

scheduler = HPCScheduler(slurm_partition="gpu")
job = await scheduler.submit_job(
    script_content=python_code,
    job_name="分子动力学模拟",
    gpus=4,
    time_limit="48:00:00",
)
```

### 4. 可视化

**ChartGenerator** — 学术图表生成：

```python
from fusion_science.visualization.chart import ChartGenerator

chart = ChartGenerator()
await chart.volcano_plot(log2fc, pvalues)  # 差异表达火山图
await chart.heatmap(expression_matrix)       # 基因表达热图
```

**MoleculeVisualizer** — 3D 分子结构可视化：

```python
from fusion_science.visualization.molecule import MoleculeVisualizer

viz = MoleculeVisualizer()
await viz.from_smiles("CC(=O)OC1=CC=CC=C1C(=O)O")  # 阿司匹林
await viz.from_pdb("6M0J")  # SARS-CoV-2 刺突蛋白
```

### 5. 文献工作流

**LiteratureSearch** — 跨库文献检索：

```python
from fusion_science.literature.search import LiteratureSearch

searcher = LiteratureSearch()
result = await searcher.search("单细胞测序 肿瘤", max_results=20)
# 自动从 PubMed 和 arXiv 搜索，合并去重
```

**PaperGenerator** — AI 辅助论文撰写：

```python
from fusion_science.literature.paper import PaperGenerator

gen = PaperGenerator()
paper = await gen.create_paper("单细胞测序在肿瘤免疫治疗中的应用")
# 自动生成 IMRaD 结构
```

### 6. 审计溯源

**TraceRecorder** — 完整操作追踪：

```python
from fusion_science.audit.tracker import TraceRecorder

recorder = TraceRecorder()
recorder.start_session()
recorder.record_db_query("search", "pubmed", "cancer", 10)
recorder.record_code_execution("analysis", "python", "差异表达分析")
session = recorder.end_session()
```

**ReportGenerator** — 实验复现报告：

```python
from fusion_science.audit.report import ReportGenerator

gen = ReportGenerator(trace_recorder, provenance_tracker)
report = gen.generate_audit_report("论文复现报告")
package = gen.export_package("./output")
```

---

## 国内研究环境适配

Fusion-Science 专门针对国内科研环境进行了优化：

### 🇨🇳 国内数据库镜像

| 数据库 | 国内替代方案 | 说明 |
|--------|-------------|------|
| PubMed | CNKI / 万方 / 中国生物医学文献数据库 | 中文文献全覆盖 |
| UniProt | 本地缓存 + 定期更新 | 预下载参考蛋白质组 |
| PDB | PDBe 镜像 / 年度发布包 | 离线结构分析 |
| Ensembl | 亚洲镜像 / GTF/GFF 文件 | 基因组注释离线 |
| NCBI | 国家基因组科学数据中心 (NGDC) | 国内基因组数据 |

### 🏠 离线缓存

SQLite 数据库缓存，支持断网完整运行：

```python
from fusion_science.database.mirror import ScienceCache

cache = ScienceCache()
cache.set("query_key", response_data, source="pubmed")
data = cache.get("query_key")  # 离线可用
```

### 🔒 数据合规

- 个人/实验室本地使用：无需大模型算法备案
- 科研数据全程本地留存，不跨境传输
- 符合《生成式人工智能服务管理暂行办法》

---

## 与 Claude Science 对比

| 特性 | Claude Science | Fusion-Science |
|------|---------------|----------------|
| **底层模型** | Claude Opus 4.8（云端） | 本地 MLX 模型（可更换） |
| **运行环境** | 仅云端 | macOS (Apple Silicon) |
| **国内可用** | ❌ | ✅ |
| **离线运行** | ❌ | ✅ |
| **数据隐私** | 数据上传 | 完全本地 |
| **开源** | ❌ | ✅ (MIT) |
| **自定义模型** | ❌ | ✅ 任意 MLX 模型 |
| **HPC 集成** | ❌ | ✅ Slurm 集群 |
| **国内数据库** | ❌ | ✅ CNKI/NGDC/CNGB |
| **价格** | 企业付费 | 免费开源 |
| **PubMed 连接** | ✅ 内置 | ✅ 内置 + 缓存 |
| **代码执行** | ✅ Python/R | ✅ Python/R/Jupyter |
| **3D 分子可视化** | ✅ | ✅ py3Dmol + RDKit |
| **审计溯源** | ✅ | ✅ 完整开源实现 |
| **论文撰写** | ✅ | ✅ IMRaD 结构 |

---

## 安装指南

### 系统要求

- macOS (Apple Silicon M1/M2/M3/M4)
- Python ≥ 3.11
- 建议 16GB+ 内存

### 完整安装

```bash
# 克隆项目
git clone https://github.com/your-org/fusion-science.git
cd fusion-science

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装全部依赖
pip install -e ".[all]"

# 或分步安装
pip install -e .                    # 基础
pip install -e ".[mlx]"             # MLX 推理
pip install -e ".[jupyter]"         # Jupyter 内核
pip install -e ".[molecule]"        # 分子可视化
```

### 可选依赖安装

```bash
# MLX 本地推理（Apple Silicon）
pip install mlx mlx-lm

# RDKit 分子可视化
pip install rdkit py3Dmol

# R 语言支持
pip install rpy2

# 初始化配置
fusion-science config init
```

---

## 开发指南

### 运行测试

```bash
pip install -e ".[test]"
pytest tests/ -v
pytest tests/ --cov=fusion_science  # 覆盖率报告
```

### 添加新数据库连接器

1. 在 `fusion_science/database/` 下创建新文件
2. 继承 `BaseConnector`
3. 实现 `search()` 和 `fetch()` 方法
4. 在 `mirror.py` 的 `DOMESTIC_MIRRORS` 中注册镜像

### 添加新流水线模板

1. 在 `pipeline.py` 中添加 `PipelineTemplate`
2. 定义智能体配置和系统提示词
3. 在 `PipelineFactory.TEMPLATES` 中注册

### 代码规范

- 类型注解：所有公共 API 必须包含类型注解
- 异步优先：所有 I/O 操作使用 async/await
- 错误处理：网络请求使用重试机制，数据库操作提供降级

---

## 项目路线图

### v0.1.0 (当前)
- [x] 核心引擎（MLX 推理）
- [x] 数据库连接器（PubMed, UniProt, PDB, Ensembl, ChEMBL）
- [x] 计算层（Python, R, Jupyter, HPC）
- [x] 可视化（图表, 分子, 蛋白质）
- [x] 文献工作流（检索, 综述, 论文）
- [x] 审计溯源（追踪, 溯源, 报告）
- [x] CLI 命令行界面
- [x] 国内镜像和离线缓存

### v0.2.0 (计划)
- [ ] Web UI 界面
- [ ] 更多数据库连接器（GEO, SRA, TCGA）
- [ ] 多模态输出（PDF, HTML 报告）
- [ ] 智能体工具注册系统
- [ ] 批量论文导入（PDF/BibTeX）

### v0.3.0 (计划)
- [ ] RAG 增强检索（本地论文库）
- [ ] 实验记录本（Lab Notebook）
- [ ] 协作功能（共享工作流）
- [ ] 插件系统

---

## 许可证

MIT License — 完全开源，可自由使用和修改。

---

## 相关项目

- [Fusion-MLX](https://github.com/your-org/fusion-mlx) — Apple Silicon 本地 LLM 推理引擎
- [Fusion-Agent-Studio](https://github.com/your-org/fusion-agent-studio) — 多智能体开发平台
- [Fusion-Bench](https://github.com/your-org/fusion-bench) — MLX 模型性能基准测试

---

---

## 数据隐私声明

Fusion-Science 是一款**本地优先**的科研工具：

- **数据不离机**：所有计算、推理、存储均在本地 Mac 完成，数据不上传至任何外部服务器
- **无遥测**：不收集用户行为、使用统计或任何形式的遥测数据
- **日志脱敏**：审计追踪模块自动过滤敏感字段（患者信息、身份证号、联系方式等），确保日志不泄露隐私
- **开源透明**：MIT 许可证，代码完全可见，可自行审计

*Fusion-Science 是为国内科研环境打造的 Claude Science 替代方案。完全本地运行，数据隐私可控，无需海外 API 访问。*