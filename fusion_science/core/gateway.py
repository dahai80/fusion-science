from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx

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


class LLMGateway:
    def __init__(
        self,
        model: str = "qwen3.5-9b",
        base_url: str = "http://localhost:11434/v1",
        api_key: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        timeout: float = 300.0,
    ):
        self.model = model
        self.default_model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None
        self._model_roles: dict[str, str] = dict(MODEL_ROLES)
        self._model_roles["reasoning"] = model
        self._model_roles["summarization"] = model
        self._model_roles["code"] = model
        self._available_models: list[dict] = []

    def set_model(self, model: str) -> None:
        logger.info("Switching model: %s -> %s", self.model, model)
        self.model = model

    def set_model_for_role(self, role: str, model: str) -> None:
        self._model_roles[role] = model
        logger.info("Set model for role '%s': %s", role, model)

    def get_model_for_role(self, role: str) -> str:
        return self._model_roles.get(role, self.model)

    def get_model_roles(self) -> dict[str, str]:
        return dict(self._model_roles)

    async def refresh_available_models(self) -> list[dict]:
        try:
            client = await self._get_client()
            resp = await client.get("/models")
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

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
            )
            logger.debug("Created httpx.AsyncClient, base_url=%s", self._base_url)
        return self._client

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        use_model = model or self.model
        payload: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
        payload.update(kwargs)

        logger.debug(
            "LLM request: model=%s, msgs=%d, temp=%.2f, max_tokens=%d",
            use_model, len(messages),
            temperature if temperature is not None else self.temperature,
            max_tokens if max_tokens is not None else self.max_tokens,
        )

        client = await self._get_client()
        try:
            resp = await client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()

            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            logger.info("LLM response: model=%s, len=%d", use_model, len(msg.get("content", "")))
            return LLMResponse(
                content=msg.get("content", ""),
                tool_calls=msg.get("tool_calls", []),
                usage=data.get("usage", {}),
                model=data.get("model", use_model),
                finish_reason=choice.get("finish_reason", ""),
            )
        except httpx.HTTPStatusError as e:
            logger.error("LLM HTTP error: %s %s", e.response.status_code, e.response.text[:200])
            return LLMResponse(
                content="", error=f"HTTP {e.response.status_code}", model=use_model,
            )
        except Exception as e:
            logger.error("LLM error: %s", type(e).__name__, exc_info=True)
            return LLMResponse(
                content="", error=str(e), model=use_model,
            )

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
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

        client = await self._get_client()
        try:
            async with client.stream("POST", "/chat/completions", json=payload) as resp:
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
        except Exception as e:
            logger.error("LLM stream error: %s", type(e).__name__, exc_info=True)
            yield f"[stream error: {e}]"

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
            resp = await client.get("/models")
            return resp.status_code == 200
        except Exception:
            logger.warning("fusion-mlx health check failed")
            return False

    async def list_models(self) -> list[dict]:
        try:
            client = await self._get_client()
            resp = await client.get("/models")
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except Exception as e:
            logger.error("list_models failed: %s", e)
            return []

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug("Closed httpx.AsyncClient")

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
