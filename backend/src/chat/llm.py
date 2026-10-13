"""Provider adapter. The rest of the app depends only on `LLMClient`."""

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

from src.chat.config import ChatConfig
from src.chat.exceptions import LLMUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string from the model; validated by the tool registry


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str | None
    tool_calls: list[LLMToolCall] = field(default_factory=list)


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
        if self._client is None:
            raise LLMUnavailable("LLM_API_KEY is not configured")

        kwargs: dict[str, Any] = {
            "model": self._config.MODEL,
            "messages": messages,
            "temperature": self._config.TEMPERATURE,
            "max_tokens": self._config.MAX_OUTPUT_TOKENS,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as exc:
            # Log the class only; request/response bodies may contain patient text.
            logger.warning("LLM request failed: %s", type(exc).__name__)
            raise LLMUnavailable(type(exc).__name__) from exc

        if not completion.choices:
            raise LLMUnavailable("empty response")
        message = completion.choices[0].message
        tool_calls = [
            LLMToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in (message.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        return LLMResponse(content=message.content, tool_calls=tool_calls)
