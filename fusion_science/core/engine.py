"""MLX inference engine — local LLM client for scientific workloads.

Wraps fusion-mlx's OpenAI-compatible HTTP API (or direct MLX loading)
for fully local inference without cloud dependency.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Structured response from an LLM call."""

    content: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
    })


@dataclass
class ModelConfig:
    """Configuration for a local MLX model."""

    name: str = "qwen3.5-9b"
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "local"
    timeout: float = 300.0
    temperature: float = 0.3
    max_tokens: int = 8192
    system_prompt: str = ""


class ScienceEngine:
    """Local inference engine for scientific research tasks.

    Supports two modes:
    1. HTTP mode — connects to fusion-mlx's OpenAI-compatible server.
    2. Direct MLX mode — loads model directly via mlx-lm (requires mlx extra).
    """

    def __init__(self, config: ModelConfig | None = None):
        self.config = config or ModelConfig()
        self._client: httpx.AsyncClient | None = None
        self._direct_model = None  # Direct MLX model handle

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.timeout,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # HTTP mode (default)
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        **kwargs,
    ) -> LLMResponse:
        """Call the LLM via HTTP API.

        Args:
            messages: Conversation messages in OpenAI format.
            tools: Optional tool definitions for function calling.
            temperature: Sampling temperature (overrides config).
            max_tokens: Max tokens to generate (overrides config).
            stream: Enable streaming.
            **kwargs: Additional API parameters.

        Returns:
            LLMResponse with content, tool_calls, and usage.
        """
        payload = {
            "model": self.config.name,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }
        if tools:
            payload["tools"] = tools
        payload.update(kwargs)

        resp = await self.client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice.get("message", {})

        return LLMResponse(
            content=message.get("content", ""),
            tool_calls=message.get("tool_calls", []),
            finish_reason=choice.get("finish_reason", "stop"),
            usage=data.get("usage", {}),
        )

    async def health(self) -> bool:
        """Check if the inference engine is reachable."""
        try:
            resp = await self.client.get("/models", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Direct MLX mode (requires mlx-lm)
    # ------------------------------------------------------------------

    async def load_model(self, model_path: str) -> bool:
        """Load a model directly via mlx-lm (requires mlx extra).

        Args:
            model_path: Path or HuggingFace repo ID for the model.

        Returns:
            True if the model loaded successfully.
        """
        try:
            import mlx_lm  # type: ignore[import-untyped]
            self._direct_model = mlx_lm.load(model_path)
            logger.info("Loaded model directly: %s", model_path)
            return True
        except ImportError:
            logger.error(
                "mlx-lm not installed. Install with: pip install fusion-science[mlx]"
            )
            return False
        except Exception as e:
            logger.error("Failed to load model %s: %s", model_path, e)
            return False

    async def generate_direct(
        self,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Generate text using a directly loaded MLX model.

        Args:
            prompt: The input prompt.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.

        Returns:
            Generated text.
        """
        if self._direct_model is None:
            raise RuntimeError("No model loaded. Call load_model() first.")
        try:
            import mlx_lm  # type: ignore[import-untyped]
            model, tokenizer = self._direct_model
            response = mlx_lm.generate(
                model,
                tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response
        except ImportError:
            raise RuntimeError("mlx-lm not installed.")
        except Exception as e:
            logger.error("Direct generation failed: %s", e)
            raise

    # ------------------------------------------------------------------
    # Scientific inference helpers
    # ------------------------------------------------------------------

    def build_science_prompt(
        self,
        task: str,
        context: str = "",
        instruction: str = "You are a scientific research assistant. Be precise, rigorous, and cite sources.",
    ) -> list[dict]:
        """Build a chat prompt for a scientific task.

        Args:
            task: The task description.
            context: Optional context (database results, code output, etc.).
            instruction: System instruction override.

        Returns:
            List of message dicts in OpenAI format.
        """
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
        """Analyze biological sequence data using the LLM.

        Args:
            sequence_data: DNA/RNA/protein sequence or annotation.
            analysis_type: Type of analysis (general, variant, structure).

        Returns:
            Analysis result text.
        """
        prompts = {
            "general": "Analyze the following biological sequence data and provide key insights:",
            "variant": "Analyze the following variant/mutation data and interpret its functional impact:",
            "structure": "Analyze the following protein structure information and describe key structural features:",
        }
        prompt = prompts.get(analysis_type, prompts["general"])
        messages = self.build_science_prompt(prompt, sequence_data)
        resp = await self.chat(messages, temperature=0.2)
        return resp.content