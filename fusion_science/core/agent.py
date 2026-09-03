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

MAX_AGENT_CONTEXT_TOKENS = 24000
MAX_ASSISTANT_CONTENT_CHARS = 4000
MAX_WORKER_OUTPUT_CHARS = 3000


def _truncate_content(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n[...truncated...]"


def _estimate_tokens(messages: list[dict]) -> int:
    # I-4: route through ContextManager.count_message_tokens so the agent and the
    # context manager use ONE token estimator. Previously agent used a private
    # chars//4 heuristic while ContextManager used tiktoken — the two disagreed
    # on when to compact, causing premature or overdue compaction.
    from .context_manager import count_message_tokens

    return count_message_tokens(messages)


@dataclass
class AgentStep:
    step: int
    action: str
    content: str
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


def _compact_messages(messages: list[dict]) -> None:
    """Compact older messages in place, never splitting a tool_call/tool result pair.

    Keeps the most recent messages and summarizes older ones. A boundary is
    only cut between messages that do not leave a dangling tool_calls (assistant)
    without its following tool (role=tool) result.
    """
    if len(messages) <= 4:
        return
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    if len(non_system) <= 2:
        return
    keep_recent = min(6, len(non_system))
    cut = len(non_system) - keep_recent
    # Walk the cut boundary forward until it does not fall right after an
    # assistant message that carries tool_calls (which needs its tool results).
    while cut < len(non_system) and non_system[cut - 1].get("tool_calls") and non_system[cut].get("role") == "tool":
        cut += 1
    older = non_system[:cut]
    recent = non_system[cut:]
    # I-7: floor — never compact away ALL recent context. When the whole
    # non-system tail is tool-call/result pairs, the boundary walk above can
    # push cut to len(non_system) and leave recent=[] , collapsing the entire
    # conversation into a stub and losing the live context the model needs.
    if not recent and non_system:
        recent = non_system[-2:]
        older = non_system[:-2]
    summary_parts = []
    for msg in older:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:500]
        summary_parts.append(f"[{role}] {content}")
    summary = "[Compacted earlier steps]: " + " | ".join(summary_parts)
    messages[:] = system_msgs + [{"role": "system", "content": summary}] + recent


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

    async def run(self, task: str, max_iterations: int = 10) -> AgentResult:
        start = time.time()
        # Local state per run — instance-level _messages/steps would corrupt under
        # concurrent runs (parallel/master_worker call agent.run via gather).
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]
        steps: list[AgentStep] = []
        # F-E4: track tool failures so the final result can flag that the output
        # was produced with degraded tool support (not_found / invalid args).
        tool_errors: list[str] = []
        # F-P5: incremental token estimate. _estimate_tokens re-encodes the WHOLE
        # message list with tiktoken every iteration — O(n) per iter, O(n^2)
        # across a 10-iter run. Instead tally new messages once and carry the
        # sum; recount from scratch only after compact rewrites the list.
        _counted_upto = len(messages)
        _token_sum = _estimate_tokens(messages)
        _last_msg_snapshot: tuple | None = None
        _last_msg_tokens = 0

        for i in range(max_iterations):
            # F-P5: add only messages appended since the last tally.
            if _counted_upto < len(messages):
                _token_sum += _estimate_tokens(messages[_counted_upto:])
                _counted_upto = len(messages)
            # F-P5: _call_llm may merge content into the trailing assistant
            # message (mutating a message already tallied as near-empty).
            # Recount that one message so the running sum stays accurate
            # without re-encoding the whole list.
            if i > 0 and messages:
                last = messages[-1]
                last_key = (last.get("role"), last.get("content"))
                if last_key != _last_msg_snapshot:
                    _token_sum += _estimate_tokens([last]) - _last_msg_tokens
                    _last_msg_tokens = _estimate_tokens([last])
                    _last_msg_snapshot = last_key
            accumulated = _token_sum
            if accumulated > MAX_AGENT_CONTEXT_TOKENS:
                _compact_messages(messages)
                _token_sum = _estimate_tokens(messages)
                _counted_upto = len(messages)
                logger.info(
                    "Agent %s: context compacted at iter %d (%d -> %d tokens est)",
                    self.name,
                    i,
                    accumulated,
                    _token_sum,
                )

            resp = await self._call_llm(messages)
            steps.append(
                AgentStep(
                    step=i,
                    action="think",
                    content=resp.content or "",
                )
            )

            if resp.tool_calls:
                for tc in resp.tool_calls:
                    result = await self._execute_tool(tc)
                    # F-E4: detect tool-level failures and record them.
                    if isinstance(result, dict) and result.get("status") in ("not_found", "invalid_arguments", "error"):
                        tool_name = tc.get("function", {}).get("name", "unknown")
                        tool_errors.append(f"{tool_name}:{result.get('status')}")
                    tool_content = json.dumps(result, ensure_ascii=False, default=str)
                    if len(tool_content) > 2000:
                        tool_content = tool_content[:2000] + "[...truncated...]"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": tool_content,
                        }
                    )
                    steps.append(
                        AgentStep(
                            step=i,
                            action="tool_result",
                            content=f"Tool {tc.get('function', {}).get('name', 'unknown')}: {tool_content[:500]}",
                            metadata={"tool_call": tc, "result": result},
                        )
                    )
            else:
                duration = time.time() - start
                output = resp.content or ""
                # F-E4: if any tool failed during this run, annotate the output so
                # the caller knows the result may rest on degraded tool support.
                if tool_errors:
                    note = f"\n\n[注意：本次分析中以下工具调用失败，结果可能不完整：{', '.join(tool_errors)}]"
                    output = (output + note) if output else note.strip()
                    logger.warning(
                        "Agent %s completed with %d tool errors: %s", self.name, len(tool_errors), tool_errors
                    )
                return AgentResult(
                    agent_name=self.name,
                    output=output,
                    steps=steps,
                    usage=resp.usage,
                    duration=duration,
                    error="; ".join(tool_errors) if tool_errors else "",
                )

        duration = time.time() - start
        # L-1: surface incomplete analysis with a clear non-empty output + error
        return AgentResult(
            agent_name=self.name,
            output="分析未完成：达到最大迭代次数仍未给出最终答案，请细化任务或重试。",
            steps=steps,
            usage={},
            duration=duration,
            error="Max iterations reached without final answer.",
        )

    async def _call_llm(self, messages: list[dict]) -> LLMResponse:
        tools_param = self.tools if self.tools else None
        resp = await self.engine.chat(
            messages=messages,
            tools=tools_param,
        )
        if resp.content:
            truncated = _truncate_content(resp.content, MAX_ASSISTANT_CONTENT_CHARS)
            # L-11: merge into a trailing assistant message if the last one is an
            # empty assistant tool-call stub — avoid two consecutive assistant msgs.
            if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("content"):
                messages[-1]["content"] = truncated
            else:
                messages.append({"role": "assistant", "content": truncated})
        if resp.tool_calls:
            if messages and messages[-1].get("role") == "assistant" and "tool_calls" not in messages[-1]:
                messages[-1]["tool_calls"] = resp.tool_calls
            else:
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": resp.tool_calls,
                    }
                )
        return resp

    async def _execute_tool(self, tool_call: dict) -> Any:
        func_name = tool_call.get("function", {}).get("name", "unknown")
        arguments = tool_call.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError as e:
            # L-12: malformed tool-call JSON → return error, do NOT call handler with {}
            logger.warning("Malformed tool-call arguments for '%s': %s", func_name, e)
            return {
                "tool": func_name,
                "status": "invalid_arguments",
                "error": f"Malformed JSON arguments: {e}",
            }

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
    def __init__(self, engine: ScienceEngine, tool_registry: ToolRegistry | None = None, pattern: str = "sequential"):
        self.engine = engine
        self.tool_registry = tool_registry
        self.agents: dict[str, ScienceAgent] = {}
        # F-C2: execution pattern set by the template so run() can dispatch
        # without the caller hard-coding sequential/parallel/master_worker.
        self.pattern = pattern

    def register_agent(self, agent: ScienceAgent) -> None:
        self.agents[agent.name] = agent

    async def run(self, task: str) -> PipelineResult:
        # F-C2: single entry point that dispatches by the template's pattern,
        # so the CLI / API actually executes the pipeline instead of building
        # the object and stopping.
        names = list(self.agents.keys())
        if not names:
            return PipelineResult(task=task, summary="no_agents_registered")
        if self.pattern == "parallel":
            return await self.parallel(names, task)
        if self.pattern == "master_worker":
            master = names[0]
            workers = names[1:] if len(names) > 1 else names
            return await self.master_worker(master, workers, task)
        return await self.sequential(names, task)

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
                result.agent_results.append(
                    AgentResult(
                        agent_name=name,
                        output="",
                        error=f"Agent '{name}' not found",
                    )
                )
                continue
            try:
                agent_result = await agent.run(current_input)
                result.agent_results.append(agent_result)
                current_input = agent_result.output
            except Exception as e:
                logger.exception("Agent %s failed", name)
                result.agent_results.append(
                    AgentResult(
                        agent_name=name,
                        output="",
                        error=str(e),
                    )
                )

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
            result.agent_results.append(
                AgentResult(
                    agent_name=master_name,
                    output="",
                    error=f"Agent '{master_name}' not found",
                )
            )
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
            run_worker(worker_names[i], sub_tasks[i] if i < len(sub_tasks) else task) for i in range(len(worker_names))
        ]
        worker_results = await asyncio.gather(*worker_coros)
        result.agent_results.extend(worker_results)

        summary_prompt = (
            f"Original task: {task}\n\n"
            f"Worker results:\n"
            + "\n".join(
                f"--- {r.agent_name} ---\n{_truncate_content(r.output, MAX_WORKER_OUTPUT_CHARS)}"
                for r in worker_results
            )
            + "\n\nProvide a comprehensive summary of the research findings."
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
