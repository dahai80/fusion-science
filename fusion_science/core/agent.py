"""Science agent runtime — multi-agent orchestration for scientific workflows.

Leverages the multi-agent architecture to decompose complex scientific
research tasks into sub-tasks, execute them in parallel or sequence,
and synthesize results with full provenance tracking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .engine import LLMResponse, ScienceEngine

logger = logging.getLogger(__name__)


@dataclass
class AgentStep:
    """A single step in an agent's execution trace."""

    step: int
    action: str  # "think", "tool_call", "tool_result", "output"
    content: str
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    """Result of a single agent execution."""

    agent_name: str
    output: str
    steps: list[AgentStep] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    duration: float = 0.0
    error: str = ""


@dataclass
class PipelineResult:
    """Result of a full scientific pipeline execution."""

    task: str
    agent_results: list[AgentResult] = field(default_factory=list)
    total_duration: float = 0.0
    summary: str = ""
    trace_id: str = ""


# ---------------------------------------------------------------------------
# Science Agent — a single research agent with tool-use capability
# ---------------------------------------------------------------------------

class ScienceAgent:
    """A single research agent that can use tools and reason about scientific tasks."""

    def __init__(
        self,
        name: str,
        engine: ScienceEngine,
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ):
        self.name = name
        self.engine = engine
        self.system_prompt = system_prompt or (
            "You are a scientific research assistant. "
            "You reason step-by-step, use available tools when needed, "
            "and provide precise, evidence-based answers."
        )
        self.tools = tools or []
        self.steps: list[AgentStep] = []
        self._messages: list[dict] = []

    async def run(self, task: str, max_iterations: int = 10) -> AgentResult:
        """Execute the agent on a given task.

        Args:
            task: The task description.
            max_iterations: Maximum reasoning/tool-use iterations.

        Returns:
            AgentResult with output, steps, and usage.
        """
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

            # If the model made tool calls, execute them
            if resp.tool_calls:
                for tc in resp.tool_calls:
                    result = await self._execute_tool(tc)
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    self.steps.append(AgentStep(
                        step=i, action="tool_result",
                        content=f"Tool {tc.get('function', {}).get('name', 'unknown')}: {json.dumps(result, ensure_ascii=False)[:500]}",
                        metadata={"tool_call": tc, "result": result},
                    ))
            else:
                # No tool calls — final answer
                duration = time.time() - start
                return AgentResult(
                    agent_name=self.name,
                    output=resp.content,
                    steps=self.steps,
                    usage=resp.usage,
                    duration=duration,
                )

        # Fallback if max iterations reached
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
        """Call the LLM with current message history."""
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
        """Execute a tool call. Placeholder — actual tool execution is
        wired up by the pipeline orchestrator."""
        # In production, this dispatches to the ToolRegistry.
        # For now, return a placeholder.
        func_name = tool_call.get("function", {}).get("name", "unknown")
        arguments = tool_call.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            args = {}
        return {
            "tool": func_name,
            "args": args,
            "status": "not_implemented",
            "message": f"Tool {func_name} is not yet wired.",
        }


# ---------------------------------------------------------------------------
# Pipeline Orchestrator — coordinates multi-step scientific workflows
# ---------------------------------------------------------------------------

class SciencePipeline:
    """Orchestrates a multi-step scientific research pipeline.

    Supports sequential, parallel, and master-worker execution patterns
    for complex scientific workflows.
    """

    def __init__(self, engine: ScienceEngine):
        self.engine = engine
        self.agents: dict[str, ScienceAgent] = {}

    def register_agent(self, agent: ScienceAgent) -> None:
        """Register an agent for use in pipelines."""
        self.agents[agent.name] = agent

    # ------------------------------------------------------------------
    # Pipeline execution patterns
    # ------------------------------------------------------------------

    async def sequential(
        self,
        agent_names: list[str],
        task: str,
    ) -> PipelineResult:
        """Execute agents sequentially — each agent's output feeds the next.

        Typical use: literature search → data analysis → visualization → paper.

        Args:
            agent_names: Ordered list of agent names to execute.
            task: The initial task description.

        Returns:
            PipelineResult with all agent outputs.
        """
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
        """Execute agents in parallel on the same task.

        Typical use: simultaneous database queries, parallel analyses.

        Args:
            agent_names: List of agent names to run in parallel.
            task: The task description shared by all agents.

        Returns:
            PipelineResult with all agent outputs.
        """
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
        """Master agent decomposes a task, workers execute sub-tasks, master summarizes.

        Typical use: complex research question broken into sub-questions.

        Args:
            master_name: The master agent (decomposes and summarizes).
            worker_names: Worker agents (execute sub-tasks).
            task: The overall research task.

        Returns:
            PipelineResult with decomposition, worker results, and summary.
        """
        result = PipelineResult(task=task)
        start = time.time()
        master = self.agents.get(master_name)
        if not master:
            result.agent_results.append(AgentResult(
                agent_name=master_name, output="", error=f"Agent '{master_name}' not found",
            ))
            return result

        # 1. Master decomposes the task
        decompose_prompt = (
            f"Decompose the following research task into {len(worker_names)} sub-tasks, "
            f"one for each worker agent. Return a JSON array of sub-task descriptions:\n\n{task}"
        )
        decomposition = await master.run(decompose_prompt)
        result.agent_results.append(decomposition)

        # Extract sub-tasks from master output
        sub_tasks = self._extract_sub_tasks(decomposition.output, len(worker_names))

        # 2. Workers execute sub-tasks in parallel
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

        # 3. Master summarizes
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

    # ------------------------------------------------------------------
    # Built-in scientific pipelines
    # ------------------------------------------------------------------

    async def literature_review_pipeline(
        self,
        query: str,
        max_papers: int = 20,
    ) -> PipelineResult:
        """End-to-end literature review: search → analyze → summarize.

        Args:
            query: Research query for literature search.
            max_papers: Maximum papers to review.

        Returns:
            PipelineResult with search results, analysis, and summary.
        """
        return await self.sequential(
            ["literature_search", "literature_analysis", "literature_summary"],
            f"Search and analyze literature on: {query}\nMax papers: {max_papers}",
        )

    async def data_analysis_pipeline(
        self,
        data_description: str,
        analysis_type: str = "exploratory",
    ) -> PipelineResult:
        """End-to-end data analysis: plan → execute → visualize → report.

        Args:
            data_description: Description of the data to analyze.
            analysis_type: Type of analysis (exploratory, statistical, ml).

        Returns:
            PipelineResult with analysis plan, code, results, and report.
        """
        return await self.sequential(
            ["data_planner", "data_executor", "data_reporter"],
            f"Analyze the following data:\n{data_description}\nAnalysis type: {analysis_type}",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_sub_tasks(self, output: str, expected_count: int) -> list[str]:
        """Extract sub-task descriptions from master agent output."""
        try:
            tasks = json.loads(output)
            if isinstance(tasks, list):
                return [str(t) for t in tasks[:expected_count]]
        except (json.JSONDecodeError, TypeError):
            pass
        # Fallback: split by numbered lines
        lines = [l.strip() for l in output.split("\n") if l.strip()]
        return lines[:expected_count]

    def _generate_summary(self, result: PipelineResult) -> str:
        """Generate a brief summary of pipeline results."""
        parts = []
        for r in result.agent_results:
            status = "✓" if not r.error else "✗"
            parts.append(f"{status} {r.agent_name} ({r.duration:.1f}s)")
        return " | ".join(parts) if parts else "No results"