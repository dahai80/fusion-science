from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .engine import LLMResponse, ScienceEngine
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class AgentStep:
    step: int
    action: str
    content: str
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    agent_name: str
    output: str
    steps: list[AgentStep] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    duration: float = 0.0
    error: str = ""


@dataclass
class PipelineResult:
    task: str
    agent_results: list[AgentResult] = field(default_factory=list)
    total_duration: float = 0.0
    summary: str = ""
    trace_id: str = ""


class ScienceAgent:
    def __init__(
        self,
        name: str,
        engine: ScienceEngine,
        system_prompt: str = "",
        tools: list[dict] | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.name = name
        self.engine = engine
        self.system_prompt = system_prompt or (
            "You are a scientific research assistant. "
            "You reason step-by-step, use available tools when needed, "
            "and provide precise, evidence-based answers."
        )
        self.tool_registry = tool_registry
        self.tools = tools or []
        self.steps: list[AgentStep] = []
        self._messages: list[dict] = []

    async def run(self, task: str, max_iterations: int = 10) -> AgentResult:
        start = time.time()
        self._messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]
        self.steps = []

        for i in range(max_iterations):
            resp = await self._call_llm()
            self.steps.append(AgentStep(
                step=i, action="think",
                content=resp.content or "",
            ))

            if resp.tool_calls:
                for tc in resp.tool_calls:
                    result = await self._execute_tool(tc)
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })
                    self.steps.append(AgentStep(
                        step=i, action="tool_result",
                        content=f"Tool {tc.get('function', {}).get('name', 'unknown')}: {json.dumps(result, ensure_ascii=False, default=str)[:500]}",
                        metadata={"tool_call": tc, "result": result},
                    ))
            else:
                duration = time.time() - start
                return AgentResult(
                    agent_name=self.name,
                    output=resp.content,
                    steps=self.steps,
                    usage=resp.usage,
                    duration=duration,
                )

        duration = time.time() - start
        return AgentResult(
            agent_name=self.name,
            output=self._messages[-1].get("content", ""),
            steps=self.steps,
            usage={},
            duration=duration,
            error="Max iterations reached without final answer.",
        )

    async def _call_llm(self) -> LLMResponse:
        tools_param = self.tools if self.tools else None
        resp = await self.engine.chat(
            messages=self._messages,
            tools=tools_param,
        )
        if resp.content:
            self._messages.append({"role": "assistant", "content": resp.content})
        if resp.tool_calls:
            self._messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": resp.tool_calls,
            })
        return resp

    async def _execute_tool(self, tool_call: dict) -> Any:
        func_name = tool_call.get("function", {}).get("name", "unknown")
        arguments = tool_call.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            args = {}

        if self.tool_registry and self.tool_registry.has_tool(func_name):
            logger.info("Executing tool '%s' via ToolRegistry", func_name)
            return await self.tool_registry.execute(func_name, args)

        logger.warning("Tool '%s' not found in registry", func_name)
        return {
            "tool": func_name,
            "args": args,
            "status": "not_found",
            "message": f"Tool '{func_name}' is not registered.",
        }


class SciencePipeline:
    def __init__(self, engine: ScienceEngine, tool_registry: ToolRegistry | None = None):
        self.engine = engine
        self.tool_registry = tool_registry
        self.agents: dict[str, ScienceAgent] = {}

    def register_agent(self, agent: ScienceAgent) -> None:
        self.agents[agent.name] = agent

    async def sequential(
        self,
        agent_names: list[str],
        task: str,
    ) -> PipelineResult:
        result = PipelineResult(task=task)
        start = time.time()
        current_input = task

        for name in agent_names:
            agent = self.agents.get(name)
            if not agent:
                result.agent_results.append(AgentResult(
                    agent_name=name, output="", error=f"Agent '{name}' not found",
                ))
                continue
            try:
                agent_result = await agent.run(current_input)
                result.agent_results.append(agent_result)
                current_input = agent_result.output
            except Exception as e:
                logger.exception("Agent %s failed", name)
                result.agent_results.append(AgentResult(
                    agent_name=name, output="", error=str(e),
                ))

        result.total_duration = time.time() - start
        result.summary = self._generate_summary(result)
        return result

    async def parallel(
        self,
        agent_names: list[str],
        task: str,
    ) -> PipelineResult:
        result = PipelineResult(task=task)
        start = time.time()
        semaphore = asyncio.Semaphore(5)

        async def run_one(name: str) -> AgentResult:
            async with semaphore:
                agent = self.agents.get(name)
                if not agent:
                    return AgentResult(agent_name=name, output="", error=f"Agent '{name}' not found")
                try:
                    return await agent.run(task)
                except Exception as e:
                    return AgentResult(agent_name=name, output="", error=str(e))

        coros = [run_one(name) for name in agent_names]
        results = await asyncio.gather(*coros)
        result.agent_results = results
        result.total_duration = time.time() - start
        result.summary = self._generate_summary(result)
        return result

    async def master_worker(
        self,
        master_name: str,
        worker_names: list[str],
        task: str,
    ) -> PipelineResult:
        result = PipelineResult(task=task)
        start = time.time()
        master = self.agents.get(master_name)
        if not master:
            result.agent_results.append(AgentResult(
                agent_name=master_name, output="", error=f"Agent '{master_name}' not found",
            ))
            return result

        decompose_prompt = (
            f"Decompose the following research task into {len(worker_names)} sub-tasks, "
            f"one for each worker agent. Return a JSON array of sub-task descriptions:\n\n{task}"
        )
        decomposition = await master.run(decompose_prompt)
        result.agent_results.append(decomposition)

        sub_tasks = self._extract_sub_tasks(decomposition.output, len(worker_names))

        semaphore = asyncio.Semaphore(5)

        async def run_worker(name: str, sub_task: str) -> AgentResult:
            async with semaphore:
                agent = self.agents.get(name)
                if not agent:
                    return AgentResult(agent_name=name, output="", error=f"Agent '{name}' not found")
                try:
                    return await agent.run(sub_task)
                except Exception as e:
                    return AgentResult(agent_name=name, output="", error=str(e))

        worker_coros = [
            run_worker(worker_names[i], sub_tasks[i] if i < len(sub_tasks) else task)
            for i in range(len(worker_names))
        ]
        worker_results = await asyncio.gather(*worker_coros)
        result.agent_results.extend(worker_results)

        summary_prompt = (
            f"Original task: {task}\n\n"
            f"Worker results:\n"
            + "\n".join(
                f"--- {r.agent_name} ---\n{r.output}"
                for r in worker_results
            ) +
            "\n\nProvide a comprehensive summary of the research findings."
        )
        summary = await master.run(summary_prompt)
        result.agent_results.append(summary)
        result.summary = summary.output
        result.total_duration = time.time() - start
        return result

    async def literature_review_pipeline(
        self,
        query: str,
        max_papers: int = 20,
    ) -> PipelineResult:
        return await self.sequential(
            ["literature_search", "literature_analysis", "literature_summary"],
            f"Search and analyze literature on: {query}\nMax papers: {max_papers}",
        )

    async def data_analysis_pipeline(
        self,
        data_description: str,
        analysis_type: str = "exploratory",
    ) -> PipelineResult:
        return await self.sequential(
            ["data_planner", "data_executor", "data_reporter"],
            f"Analyze the following data:\n{data_description}\nAnalysis type: {analysis_type}",
        )

    def _extract_sub_tasks(self, output: str, expected_count: int) -> list[str]:
        try:
            tasks = json.loads(output)
            if isinstance(tasks, list):
                return [str(t) for t in tasks[:expected_count]]
        except (json.JSONDecodeError, TypeError):
            pass
        lines = [ln.strip() for ln in output.split("\n") if ln.strip()]
        return lines[:expected_count]

    def _generate_summary(self, result: PipelineResult) -> str:
        parts = []
        for r in result.agent_results:
            status = "ok" if not r.error else "FAIL"
            parts.append(f"{status} {r.agent_name} ({r.duration:.1f}s)")
        return " | ".join(parts) if parts else "No results"
