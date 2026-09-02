from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..session.manager import SessionManager

try:
    import tiktoken

    _ENCODER_CACHE: dict[str, Any] = {}

    def _get_encoder(model: str) -> Any:
        if model not in _ENCODER_CACHE:
            try:
                _ENCODER_CACHE[model] = tiktoken.encoding_for_model(model)
            except Exception:
                _ENCODER_CACHE[model] = tiktoken.get_encoding("cl100k_base")
        return _ENCODER_CACHE[model]

    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False

_CHARS_PER_TOKEN = 4.0

_DEFAULT_CONTEXT_WINDOW = 32768
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_THINKING_BUDGET = 4096
_PROMPT_BUDGET_RATIO = 0.70

MAX_MESSAGES_BEFORE_COMPRESS = 40


def count_tokens(text: str, model: str = "qwen3.5-9b") -> int:
    if not text:
        return 0
    if not isinstance(text, str):
        # Defensive: callers may pass list/dict content — coerce to str
        try:
            text = str(text)
        except Exception:
            return 0
    if _TIKTOKEN_AVAILABLE:
        try:
            enc = _get_encoder(model)
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def count_message_tokens(messages: list[dict], model: str = "qwen3.5-9b") -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += count_tokens(part.get("text", ""), model)
        total += 4
    return total


class ContextManager:
    def __init__(
        self,
        session_manager: SessionManager,
        gateway: Any | None = None,
        context_window: int = _DEFAULT_CONTEXT_WINDOW,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        thinking_budget: int = _DEFAULT_THINKING_BUDGET,
        model: str = "qwen3.5-9b",
    ):
        self.session_manager = session_manager
        self.gateway = gateway
        self.context_window = context_window
        self.max_tokens = max_tokens
        self.thinking_budget = thinking_budget
        self.model = model
        self._last_compressed_count: dict[str, int] = {}

    def get_prompt_budget(self) -> int:
        reserved = self.max_tokens + self.thinking_budget
        budget = int(self.context_window * _PROMPT_BUDGET_RATIO)
        return max(512, budget - reserved)

    def fit(
        self,
        session_id: str,
        max_tokens_budget: int | None = None,
    ) -> list[dict]:
        messages = self.session_manager.get_messages(session_id)
        if not messages:
            return []

        budget = max_tokens_budget or self.get_prompt_budget()
        total = count_message_tokens(messages, self.model)
        logger.debug(
            "ContextManager.fit: session=%s msgs=%d total_tokens=%d budget=%d",
            session_id[:8],
            len(messages),
            total,
            budget,
        )

        if total <= budget:
            return messages

        fitted = self._sliding_window(messages, budget, session_id)
        fitted_tokens = count_message_tokens(fitted, self.model)
        logger.info(
            "ContextManager.fit: compressed session=%s %d->%d msgs, %d->%d tokens",
            session_id[:8],
            len(messages),
            len(fitted),
            total,
            fitted_tokens,
        )
        return fitted

    def _sliding_window(
        self,
        messages: list[dict],
        budget: int,
        session_id: str,
    ) -> list[dict]:
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if not non_system:
            return system_msgs

        system_tokens = count_message_tokens(system_msgs, self.model)
        remaining_budget = budget - system_tokens
        if remaining_budget < 100:
            return system_msgs[-1:] if system_msgs else []

        recent = []
        recent_tokens = 0
        for msg in reversed(non_system):
            msg_tokens = count_message_tokens([msg], self.model)
            if recent_tokens + msg_tokens > remaining_budget and recent:
                break
            recent.insert(0, msg)
            recent_tokens += msg_tokens

        if len(recent) < len(non_system):
            older = non_system[: len(non_system) - len(recent)]
            summary = self._summarize_older(older, session_id)
            if summary:
                summary_tokens = count_tokens(summary, self.model) + 4
                if summary_tokens + recent_tokens <= remaining_budget:
                    recent.insert(
                        0,
                        {
                            "role": "system",
                            "content": f"[Earlier conversation summary]: {summary}",
                        },
                    )

        return system_msgs + recent

    def _summarize_older(self, older_messages: list[dict], session_id: str) -> str:
        if not older_messages:
            return ""
        count = len(older_messages)
        logger.debug("ContextManager: truncating %d older messages (sync, no LLM)", count)
        return f"[{count} earlier messages truncated]"

    async def _summarize_older_async(self, older_messages: list[dict]) -> str:
        if not older_messages:
            return ""

        combined_parts = []
        for msg in older_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                combined_parts.append(f"[{role}] {content[:500]}")
        combined = "\n".join(combined_parts)
        if len(combined) > 3000:
            combined = combined[:3000] + "..."

        fallback = f"[{len(older_messages)} earlier messages summarized]\n{combined[:1000]}"

        if not self.gateway:
            logger.warning("ContextManager: no gateway, using truncated text summary")
            return fallback

        summary_messages = [
            {
                "role": "system",
                "content": (
                    "Summarize the following conversation history concisely. "
                    "Preserve key facts, decisions, entities, and numerical results. "
                    "Output only the summary."
                ),
            },
            {
                "role": "user",
                "content": f"Summarize these {len(older_messages)} messages:\n\n{combined}",
            },
        ]
        try:
            result = await self.gateway.chat(summary_messages, temperature=0.3, max_tokens=512)
            if result.content:
                logger.info("ContextManager: LLM summarized %d older messages", len(older_messages))
                return result.content
            logger.warning("ContextManager: gateway returned empty content, using fallback")
            return fallback
        except Exception as e:
            logger.warning("ContextManager gateway summarize failed: %s, using fallback", e)
            return fallback

    async def maybe_compress(self, session_id: str) -> bool:
        messages = self.session_manager.get_messages(session_id)
        if len(messages) < MAX_MESSAGES_BEFORE_COMPRESS:
            return False

        last_count = self._last_compressed_count.get(session_id, 0)
        if len(messages) - last_count < MAX_MESSAGES_BEFORE_COMPRESS:
            return False

        total = count_message_tokens(messages, self.model)
        budget = self.get_prompt_budget()
        if total <= budget:
            return False

        logger.info(
            "ContextManager: triggering compression for session=%s (%d msgs, %d tokens)",
            session_id[:8],
            len(messages),
            total,
        )

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        if not non_system:
            return False

        system_tokens = count_message_tokens(system_msgs, self.model)
        remaining_budget = budget - system_tokens
        if remaining_budget < 100:
            return False

        recent: list[dict] = []
        recent_tokens = 0
        for msg in reversed(non_system):
            msg_tokens = count_message_tokens([msg], self.model)
            if recent_tokens + msg_tokens > remaining_budget and recent:
                break
            recent.insert(0, msg)
            recent_tokens += msg_tokens

        older = non_system[: len(non_system) - len(recent)]
        if not older:
            return False

        summary = await self._summarize_older_async(older)
        new_messages = (
            system_msgs + [{"role": "system", "content": f"[Earlier conversation summary]: {summary}"}] + recent
        )

        await self.session_manager.replace_messages(session_id, new_messages)
        self._last_compressed_count[session_id] = len(new_messages)
        logger.info(
            "ContextManager: compressed session=%s %d->%d msgs",
            session_id[:8],
            len(messages),
            len(new_messages),
        )
        return True

    def get_stats(self, session_id: str) -> dict:
        messages = self.session_manager.get_messages(session_id)
        return {
            "session_id": session_id,
            "message_count": len(messages),
            "total_tokens": count_message_tokens(messages, self.model),
            "prompt_budget": self.get_prompt_budget(),
            "context_window": self.context_window,
            "max_tokens": self.max_tokens,
            "thinking_budget": self.thinking_budget,
        }
