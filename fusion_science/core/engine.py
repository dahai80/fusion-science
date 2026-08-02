"""Local LLM inference engine for scientific workloads.

All LLM calls go through fusion-mlx's OpenAI-compatible HTTP API via
fusion-core's shared FusionMLXClient. No direct mlx or mlx-lm imports.
"""

from __future__ import annotations

import logging
from typing import Any

from fusion_core.mlx_client import FusionMLXClient as _FusionMLXClient
from fusion_core.mlx_client import LLMResponse

logger = logging.getLogger(__name__)


class ScienceEngine:
    """Local inference engine for scientific research tasks.

    All LLM calls go through fusion-core's FusionMLXClient → fusion-mlx HTTP API.
    No direct mlx or mlx-lm imports — everything is routed via fusion-mlx.
    """

    def __init__(
        self,
        model: str = "qwen3.5-9b",
        base_url: str = "http://localhost:11434/v1",
        temperature: float = 0.3,
        max_tokens: int = 8192,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._mlx = _FusionMLXClient(base_url=base_url, timeout=300.0)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._mlx.close()

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> LLMResponse:
        """Call the LLM via fusion-mlx HTTP API.

        Args:
            messages: Conversation messages in OpenAI format.
            tools: Optional tool definitions for function calling.
            temperature: Sampling temperature (overrides default).
            max_tokens: Max tokens to generate (overrides default).
            **kwargs: Additional API parameters.

        Returns:
            LLMResponse with content, tool_calls, and usage.
        """
        return await self._mlx.chat(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            **kwargs,
        )

    async def health(self) -> bool:
        """Check if fusion-mlx is reachable."""
        return await self._mlx.health()

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