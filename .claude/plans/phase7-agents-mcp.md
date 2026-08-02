# Phase 7: Professional Agents + MCP Server + Enhanced API

> Version: 0.4.0 | Prerequisite: Phases 1-6 complete (v0.3.1)
> User instruction: "启动下一个阶段的任务实施"
> Parent spec: `/Users/dahai/fusion/architecture/science-enhance.md` Section 3.3.7 (agents), 3.3.4 (API), 3.3.2 (MCP)

## Callers / Importers

- `api/app.py` → imports and mounts new route modules + `mcp_server.py`
- `core/agents/router.py` → instantiates and dispatches to `LiteratureAgent`, `DataAgent`, `VizAgent`, `WriterAgent`, `ErrorAnalysisAgent`
- `core/agents/literature.py` → calls `LiteratureSearch`, `LiteratureExtractor`, `LiteratureSynthesizer`, `LiteratureReviewer`
- `core/agents/data.py` → calls `CodeGenerator`, `PythonExecutor`, `RExecutor`, `SandboxManager`
- `core/agents/visualize.py` → calls `SmartVisualizer`, `ChartGenerator`, `MoleculeVisualizer`, `ProteinVisualizer`
- `core/agents/writer.py` → calls `LiteratureReviewer`, `CitationManager`, `PaperGenerator`
- `core/agents/error.py` → calls `LLMGateway.chat()` for diagnosis
- `mcp_server.py` → reads tools from `ToolRegistry.get_mcp_tools()`, executes via `ToolRegistry.execute()`
- `audit/compliance.py` → reads `TraceRecorder` entries, `ResearchSession` artifacts

## Affected API

New REST endpoints:
- POST `/api/v1/sessions/{id}/search` → LiteratureSearch
- POST `/api/v1/sessions/{id}/analyze` → DataAgent
- POST `/api/v1/sessions/{id}/visualize` → VizAgent
- POST `/api/v1/sessions/{id}/review` → LiteratureReviewer
- GET `/api/v1/sessions/{id}/audit` → Audit report + compliance
- POST `/mcp` → MCP JSON-RPC 2.0 (tools/list, tools/call, initialize)

## Data Schemas

### QueryRouterAgent.dispatch() returns AgentResult (existing)
### ComplianceResult (new dataclass)
```python
@dataclass
class ComplianceResult:
    category: str       # data_residency | algorithm_registration | ethics_review | sensitive_data
    passed: bool
    severity: str       # info | warning | critical
    details: str
    recommendation: str
```
### MCP JSON-RPC request/response follows MCP spec (tools/list → tool list, tools/call → result)

## What's Done (v0.3.1)

F-01~F-03, F-04~F-10, F-11~F-13, F-15, F-17~F-19, F-30, CI/lint — all complete.

## Phase 7 Tasks (3 streams)

### Stream A: Professional Agent System (F-22)

| # | File | Description |
|---|------|-------------|
| 1 | `core/agents/__init__.py` | Exports all agents + QueryRouterAgent |
| 2 | `core/agents/router.py` | QueryRouterAgent: keyword + LLM routing → dispatch |
| 3 | `core/agents/literature.py` | LiteratureAgent: search+extract+synthesize+review |
| 4 | `core/agents/data.py` | DataAgent: code_gen+execute+debug |
| 5 | `core/agents/visualize.py` | VizAgent: recommend+generate charts |
| 6 | `core/agents/writer.py` | WriterAgent: draft+cite |
| 7 | `core/agents/error.py` | ErrorAnalysisAgent: diagnose failures |

### Stream B: MCP Server (F-21)

| # | File | Description |
|---|------|-------------|
| 8 | `mcp_server.py` | MCP JSON-RPC endpoint (tools/list, tools/call) |

### Stream C: API Routes + Tools + Compliance

| # | File | Description |
|---|------|-------------|
| 9 | `api/routes/search.py` | POST /sessions/{id}/search |
| 10 | `api/routes/analysis.py` | POST /sessions/{id}/analyze |
| 11 | `api/routes/visualize.py` | POST /sessions/{id}/visualize |
| 12 | `api/routes/review.py` | POST /sessions/{id}/review |
| 13 | `api/routes/audit_route.py` | GET /sessions/{id}/audit |
| 14 | `audit/compliance.py` | ComplianceChecker with 4 check methods |
| 15-18 | `tests/test_agents.py`, `test_mcp_server.py`, `test_api_extended.py`, `test_compliance.py` | Tests |
| 19 | Modify `core/tools.py` | Add 7 tools: extract_findings, analyze_consensus, execute_r, visualize_molecule, visualize_protein, write_section, manage_citations |
| 20 | Modify `api/app.py` | Mount new routes + MCP endpoint |
| 21 | Modify `core/agent.py` | Add stream support to ScienceAgent |
| 22 | Modify `pyproject.toml` | Version 0.3.1 → 0.4.0 |
| 23 | Modify `README.md` | Phase 7 docs |

## Execution Order

1. Add 7 tools to `core/tools.py` (agents depend on these)
2. Stream A: `core/agents/` (6 agents + router)
3. Stream B: `mcp_server.py`
4. Stream C: API routes + compliance
5. Tests for all streams
6. Modify `api/app.py` to mount everything
7. ruff + pytest → commit + merge + tag v0.4.0
