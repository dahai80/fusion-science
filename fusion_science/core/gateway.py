from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx
from fusion_core.http_client import get_async_client, with_retry

from .retry import ConnectionMonitor, RetryStats

logger = logging.getLogger(__name__)

MODEL_ROLES = {
    "reasoning": "qwen3.5-9b",
    "summarization": "qwen3.5-9b",
    "code": "qwen3.5-9b",
}


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""
    finish_reason: str = ""
    error: str = ""
    raw: str = ""
    parsed: Any = None


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""
    finish_reason: str = ""
    error: str = ""


class LLMGateway:
    def __init__(
        self,
        model: str = "qwen3.5-9b",
        base_url: str = "http://localhost:11432/v1",
        api_key: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        timeout: float = 300.0,
        enable_thinking: bool = True,
    ):
        self.model = model
        self.default_model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.enable_thinking = enable_thinking
        self._base_url = base_url.rstrip("/")
        self._engine_base_url = self._base_url.replace("/v1", "") or "http://localhost:11432"
        self._timeout = timeout
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None
        self._model_roles: dict[str, str] = dict(MODEL_ROLES)
        self._model_roles["reasoning"] = model
        self._model_roles["summarization"] = model
        self._model_roles["code"] = model
        self._role_max_tokens: dict[str, int] = {}
        self._available_models: list[dict] = []
        self._connection_monitor: ConnectionMonitor | None = None
        self._max_retries: int = 3
        self._request_times: deque[float] = deque(maxlen=100)
        self._memory_check_enabled: bool = True
        self._memory_soft_threshold: float = 0.85
        self._last_used_model: str = model

    def set_model(self, model: str) -> None:
        logger.info("Switching model: %s -> %s", self.model, model)
        self.model = model

    def set_model_for_role(self, role: str, model: str, max_tokens: int | None = None) -> None:
        self._model_roles[role] = model
        if max_tokens is not None:
            self._role_max_tokens[role] = max_tokens
        else:
            if role == "reasoning":
                self._role_max_tokens[role] = 8192
            elif role == "summarization":
                self._role_max_tokens[role] = 4096
            elif role == "code":
                self._role_max_tokens[role] = 8192
            else:
                self._role_max_tokens[role] = self.max_tokens
        logger.info("Set model for role '%s': %s (max_tokens=%d)", role, model, self._role_max_tokens[role])

    def get_max_tokens_for_role(self, role: str) -> int:
        return self._role_max_tokens.get(role, self.max_tokens)

    def get_model_for_role(self, role: str) -> str:
        return self._model_roles.get(role, self.model)

    def get_model_roles(self) -> dict[str, str]:
        return dict(self._model_roles)

    async def refresh_available_models(self) -> list[dict]:
        try:
            client = await self._get_client()
            resp = await client.get("/models", headers=self._route_headers())
            resp.raise_for_status()
            data = resp.json()
            self._available_models = data.get("data", [])
            logger.info("Refreshed model list: %d models", len(self._available_models))
            return self._available_models
        except Exception as e:
            logger.error("refresh_available_models failed: %s", e)
            return []

    def get_available_models(self) -> list[dict]:
        return list(self._available_models)

    def get_connection_stats(self) -> RetryStats:
        if self._connection_monitor:
            return self._connection_monitor.stats
        return RetryStats()

    def get_avg_response_time(self) -> float:
        if not self._request_times:
            return 0.0
        recent = list(self._request_times)[-20:]
        return sum(recent) / len(recent)

    def start_connection_monitor(self, interval: float = 30.0) -> None:
        if self._connection_monitor:
            return
        self._connection_monitor = ConnectionMonitor(
            health_check=self.health,
            check_interval=interval,
        )
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            # R-9: keep a strong reference to the monitor task. Without it, the
            # task can be garbage-collected mid-run (no caller holds the
            # coroutine) and stop_connection_monitor has no handle to cancel.
            self._monitor_task = loop.create_task(self._connection_monitor.start_monitor())
        except RuntimeError:
            logger.debug("No running loop for connection monitor; will start on first use")

    def stop_connection_monitor(self) -> None:
        if not self._connection_monitor:
            return
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._connection_monitor.stop_monitor())
        except RuntimeError:
            pass
        # Cancel the monitor task so it does not outlive the gateway; swallow
        # CancelledError which is the expected outcome of cancelling a task.
        task = getattr(self, "_monitor_task", None)
        if task is not None and not task.done():
            task.cancel()
            logger.debug("Cancelled connection monitor task")
        self._monitor_task = None
        self._connection_monitor = None

    def _route_headers(self) -> dict[str, str]:
        headers = {"X-Fusion-Route": "fusion-science"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = get_async_client(
                self._base_url,
                timeout=self._timeout,
            )
            logger.debug("Pooled httpx.AsyncClient via fusion_core, base=%s", self._base_url)
        return self._client

    async def check_mlx_memory(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self._engine_base_url,
                headers=self._route_headers(),
                timeout=10.0,
            ) as status_client:
                resp = await status_client.get("/api/status")
                resp.raise_for_status()
                data = resp.json()
                logger.debug("MLX api/status: %s", json.dumps(data, ensure_ascii=False)[:300])
                return data
        except Exception as e:
            logger.warning("check_mlx_memory failed (non-fatal): %s", e)
            return {}

    def evaluate_memory_pressure(self, status: dict[str, Any]) -> tuple[bool, str]:
        if not status:
            if os.getenv("FUSION_SCIENCE_MEMORY_FAIL_OPEN", "").lower() in ("1", "true", "yes"):
                logger.warning("MLX status unavailable; fail-open permitted by env, proceeding")
                return True, "status_unavailable_fail_open"
            return False, "status_unavailable_block"
        ceiling = status.get("model_memory_max", 0)
        current = status.get("model_memory_used", 0)
        if ceiling > 0 and current > 0:
            ratio = current / ceiling
            if ratio >= self._memory_soft_threshold:
                return False, f"memory_pressure_high ratio={ratio:.2f}"
            return True, f"memory_ok ratio={ratio:.2f}"
        loaded = status.get("loaded_models", [])
        if isinstance(loaded, list) and len(loaded) > 3:
            return False, f"too_many_models_loaded count={len(loaded)}"
        return True, "status_inconclusive_proceed"

    async def unload_model(self, model_id: str) -> bool:
        if model_id == self.default_model:
            logger.debug("Skip unloading default model: %s", model_id)
            return False
        try:
            async with httpx.AsyncClient(
                base_url=self._engine_base_url,
                headers=self._route_headers(),
                timeout=30.0,
            ) as unload_client:
                resp = await unload_client.post(f"/v1/models/{model_id}/unload")
                if resp.status_code == 200:
                    logger.info("Unloaded non-default model: %s", model_id)
                    return True
                logger.warning("Unload model %s returned %d", model_id, resp.status_code)
                return False
        except Exception as e:
            logger.warning("unload_model(%s) failed (non-fatal): %s", model_id, e)
            return False

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        enable_thinking: bool | None = None,
        check_memory: bool = True,
        **kwargs,
    ) -> LLMResponse:
        use_model = model or self.model
        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        thinking_flag = enable_thinking if enable_thinking is not None else self.enable_thinking
        if thinking_flag and effective_max_tokens < 4096:
            effective_max_tokens = 4096
            logger.debug("Bumped max_tokens to 4096 minimum for thinking model safety")
        payload: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": effective_max_tokens,
        }
        if tools:
            payload["tools"] = tools
        payload["chat_template_kwargs"] = {"enable_thinking": thinking_flag}
        payload.update(kwargs)

        logger.debug(
            "LLM request: model=%s, msgs=%d, temp=%.2f, max_tokens=%d, thinking=%s",
            use_model,
            len(messages),
            temperature if temperature is not None else self.temperature,
            effective_max_tokens,
            thinking_flag,
        )

        if check_memory and self._memory_check_enabled:
            status = await self.check_mlx_memory()
            ok, reason = self.evaluate_memory_pressure(status)
            if not ok:
                logger.warning("Pre-call memory check FAILED: %s (model=%s)", reason, use_model)
                return LLMResponse(
                    content="",
                    error=f"memory_pressure: {reason}",
                    model=use_model,
                )
            logger.debug("Pre-call memory check: %s", reason)

        if self._last_used_model and self._last_used_model != use_model and self._last_used_model != self.default_model:
            await self.unload_model(self._last_used_model)

        client = await self._get_client()
        headers = self._route_headers()
        t0 = time.time()
        try:
            resp = await with_retry(
                lambda: client.post("/chat/completions", json=payload, headers=headers),
                retries=self._max_retries,
                total_deadline=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            content = msg.get("content") or ""
            elapsed = time.time() - t0
            self._request_times.append(elapsed)
            if self._connection_monitor:
                self._connection_monitor.record_success()
            logger.info("LLM response: model=%s, len=%d, %.2fs", use_model, len(content), elapsed)
            if not content or not content.strip():
                logger.warning("LLM returned empty content, model=%s", use_model)
                if use_model != self.default_model:
                    await self.unload_model(use_model)
                return LLMResponse(
                    content="",
                    error="empty_content",
                    model=data.get("model", use_model),
                )
            self._last_used_model = use_model
            return LLMResponse(
                content=content,
                tool_calls=msg.get("tool_calls", []),
                usage=data.get("usage", {}),
                model=data.get("model", use_model),
                finish_reason=choice.get("finish_reason", ""),
            )
        except httpx.HTTPStatusError as e:
            elapsed = time.time() - t0
            self._request_times.append(elapsed)
            if self._connection_monitor:
                self._connection_monitor.record_failure(f"HTTP {e.response.status_code}")
            logger.error("LLM HTTP error: %s %s", e.response.status_code, e.response.text[:200])
            if use_model != self.default_model:
                await self.unload_model(use_model)
            return LLMResponse(
                content="",
                error=f"HTTP {e.response.status_code}",
                model=use_model,
            )
        except Exception as e:
            elapsed = time.time() - t0
            self._request_times.append(elapsed)
            if self._connection_monitor:
                self._connection_monitor.record_failure(str(e))
            logger.error("LLM error: %s", type(e).__name__, exc_info=True)
            if use_model != self.default_model:
                await self.unload_model(use_model)
            return LLMResponse(
                content="",
                error=str(e),
                model=use_model,
            )

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        check_memory: bool = True,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        use_model = model or self.model
        payload: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        payload.update(kwargs)

        logger.debug("LLM stream request: model=%s, msgs=%d", use_model, len(messages))

        if check_memory and self._memory_check_enabled:
            status = await self.check_mlx_memory()
            ok, reason = self.evaluate_memory_pressure(status)
            if not ok:
                logger.warning("Stream pre-call memory check FAILED: %s (model=%s)", reason, use_model)
                raise RuntimeError(f"memory_pressure: {reason}")
            logger.debug("Stream pre-call memory check: %s", reason)

        if self._last_used_model and self._last_used_model != use_model and self._last_used_model != self.default_model:
            await self.unload_model(self._last_used_model)

        client = await self._get_client()
        headers = self._route_headers()
        try:
            async with client.stream("POST", "/chat/completions", json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            yield token
                    except json.JSONDecodeError:
                        logger.warning("Stream chunk parse error: %s", data[:100])
                        continue
            self._last_used_model = use_model
        except Exception as e:
            logger.error("LLM stream error: %s", type(e).__name__, exc_info=True)
            if use_model != self.default_model:
                with contextlib.suppress(Exception):
                    await self.unload_model(use_model)
            raise

    async def structured_output(
        self,
        messages: list[dict],
        schema: dict,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResult:
        schema_instruction = (
            "You MUST respond with valid JSON matching this schema. "
            "Do NOT wrap in markdown code fences.\n\n"
            f"JSON Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )
        augmented = list(messages)
        augmented.append({"role": "system", "content": schema_instruction})

        resp = await self.chat(
            messages=augmented,
            temperature=temperature or 0.1,
            max_tokens=max_tokens,
            model=model,
        )

        if resp.error:
            return LLMResult(
                content=resp.content,
                error=resp.error,
                raw=resp.content,
                model=resp.model,
            )

        text = resp.content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
            logger.info("Structured output parsed successfully")
            return LLMResult(
                content=resp.content,
                parsed=data,
                raw=resp.content,
                model=resp.model,
                usage=resp.usage,
            )
        except json.JSONDecodeError as e:
            logger.warning("Structured output JSON parse failed: %s", e)
            return LLMResult(
                content=resp.content,
                error=f"json_decode_error: {e}",
                raw=resp.content,
                model=resp.model,
                usage=resp.usage,
            )

    async def health(self) -> bool:
        try:
            client = await self._get_client()
            resp = await client.get("/models", headers=self._route_headers())
            return resp.status_code == 200
        except Exception:
            logger.warning("fusion-mlx health check failed")
            return False

    async def list_models(self) -> list[dict]:
        try:
            client = await self._get_client()
            resp = await client.get("/models", headers=self._route_headers())
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except Exception as e:
            logger.error("list_models failed: %s", e)
            return []

    async def close(self) -> None:
        if self._connection_monitor:
            await self._connection_monitor.stop_monitor()
        if self._client is not None:
            logger.debug("Closing pooled httpx.AsyncClient on gateway shutdown")
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None
        with contextlib.suppress(Exception):
            from fusion_core.http_client import close_all

            await close_all()

    async def __aenter__(self) -> LLMGateway:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    def build_science_prompt(
        self,
        task: str,
        context: str = "",
        instruction: str = "You are a scientific research assistant. Be precise, rigorous, and cite sources.",
    ) -> list[dict]:
        messages = [{"role": "system", "content": instruction}]
        if context:
            messages.append({"role": "user", "content": f"Context:\n{context}\n\nTask: {task}"})
        else:
            messages.append({"role": "user", "content": task})
        return messages

    async def analyze_sequence(
        self,
        sequence_data: str,
        analysis_type: str = "general",
    ) -> str:
        prompts = {
            "general": "Analyze the following biological sequence data and provide key insights:",
            "variant": "Analyze the following variant/mutation data and interpret its functional impact:",
            "structure": "Analyze the following protein structure information and describe key structural features:",
        }
        prompt = prompts.get(analysis_type, prompts["general"])
        messages = self.build_science_prompt(prompt, sequence_data)
        resp = await self.chat(messages, temperature=0.2)
        return resp.content
