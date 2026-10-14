"""Provider adapter. The rest of the app depends only on `LLMClient`."""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

from src.chat.config import ChatConfig
from src.chat.exceptions import LLMUnavailable

logger = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)
# Qwen3's template injects the opening <think> into the prompt, so the output can hold
# only the reasoning followed by a bare </think> and then the real answer.
_THINK_CLOSE = re.compile(r"</think>", re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    text = _THINK_BLOCK.sub("", text)
    parts = _THINK_CLOSE.split(text)
    return parts[-1].strip()


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string from the model; validated by the tool registry


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str | None
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    finish_reason: str | None = None


class LLMClient(Protocol):
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        """Raise LLMUnavailable for any provider/network/config failure."""
        ...


class OpenAICompatibleClient:
    """OpenAI SDK pointed at any OpenAI-compatible endpoint (DeepSeek by default)."""

    def __init__(self, config: ChatConfig) -> None:
        self._config = config
        api_key = config.API_KEY.get_secret_value() if config.API_KEY else None
        self._client: AsyncOpenAI | None = (
            AsyncOpenAI(
                api_key=api_key,
                base_url=config.BASE_URL,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
                max_retries=1,
            )
            if api_key
            else None
        )

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        debug = self._config.DEBUG_PRINT
        if self._client is None:
            if debug:
                print("[LLM DEBUG] client not created: LLM_API_KEY is not set (even Ollama needs a dummy value)")
            raise LLMUnavailable("LLM_API_KEY is not configured")

        kwargs: dict[str, Any] = {
            "model": self._config.MODEL,
            "messages": messages,
            "temperature": self._config.TEMPERATURE,
            "max_tokens": self._config.MAX_OUTPUT_TOKENS,
        }
        if tools:
            kwargs["tools"] = tools

        if debug:
            sent_chars = sum(len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or "")) for m in messages)
            print(f"[LLM DEBUG] ---- request: {len(messages)} messages, ~{sent_chars} chars, {len(tools)} tools ----")
            for m in messages[1:]:
                print(f"[LLM DEBUG]   {m['role']}: {str(m.get('content') or m.get('tool_calls'))[:80]!r}")

        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as exc:
            # Log the class only; request/response bodies may contain patient text.
            logger.warning("LLM request failed: %s", type(exc).__name__)
            if debug:
                print(f"[LLM DEBUG] provider call failed: {type(exc).__name__}: {exc} (base_url={self._config.BASE_URL}, model={self._config.MODEL})")
            raise LLMUnavailable(type(exc).__name__) from exc

        if not completion.choices:
            if debug:
                print(f"[LLM DEBUG] provider returned no choices: {completion!r}")
            raise LLMUnavailable("empty response")
        message = completion.choices[0].message
        if debug:
            # Ollama/Qwen expose thinking as `reasoning` (or `reasoning_content`); some
            # builds leave <think>...</think> inline in content instead.
            reasoning = getattr(message, "reasoning", None) or getattr(message, "reasoning_content", None)
            print(
                "[LLM DEBUG] ---- model response ----\n"
                f"finish_reason: {completion.choices[0].finish_reason}\n"
                f"reasoning: {reasoning!r}\n"
                f"content: {message.content!r}\n"
                f"tool_calls: {message.tool_calls!r}\n"
                "[LLM DEBUG] ---- end ----"
            )
        tool_calls = [
            LLMToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in (message.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        content = strip_reasoning(message.content) if message.content else message.content
        return LLMResponse(content=content, tool_calls=tool_calls, finish_reason=completion.choices[0].finish_reason)
