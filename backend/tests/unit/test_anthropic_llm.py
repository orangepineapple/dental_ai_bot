import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.chat.anthropic_llm import AnthropicClient, to_anthropic_messages, to_anthropic_tools
from src.chat.config import ChatConfig
from src.chat.exceptions import LLMUnavailable


def test_tools_converted() -> None:
    tools = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {"a": {"type": "string"}}}}}]

    assert to_anthropic_tools(tools) == [
        {"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}}}
    ]


def test_messages_converted_with_tool_round_trip() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "book me"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "a", "arguments": '{"x": 1}'}},
            {"id": "c2", "type": "function", "function": {"name": "b", "arguments": "not json"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok": 1}'},
        {"role": "tool", "tool_call_id": "c2", "content": '{"ok": 2}'},
    ]

    system, out = to_anthropic_messages(messages)

    assert system == "sys"
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[1]["content"] == [
        {"type": "tool_use", "id": "c1", "name": "a", "input": {"x": 1}},
        {"type": "tool_use", "id": "c2", "name": "b", "input": {}},
    ]
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["c1", "c2"]


def test_user_text_after_tool_results_merges_with_results_first() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "a", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
        {"role": "user", "content": "more"},
    ]

    _, out = to_anthropic_messages(messages)

    assert [b["type"] for b in out[-1]["content"]] == ["tool_result", "text"]


class _FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.kwargs: dict[str, Any] = {}
        self._response, self._error = response, error

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self._error:
            raise self._error
        return self._response


def _client(fake: _FakeMessages) -> AnthropicClient:
    client = AnthropicClient(ChatConfig(ANTHROPIC_API_KEY="test"))
    client._client = SimpleNamespace(messages=fake)
    return client


async def test_complete_maps_text_and_tool_calls() -> None:
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id="c1", name="a", input={"x": 1})],
    )
    fake = _FakeMessages(response)

    result = await _client(fake).complete([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], [])

    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].name == "a" and json.loads(result.tool_calls[0].arguments) == {"x": 1}
    assert fake.kwargs["system"] == "s"
    assert "tools" not in fake.kwargs and "temperature" not in fake.kwargs


async def test_max_tokens_maps_to_length() -> None:
    response = SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(type="text", text="cut")])

    result = await _client(_FakeMessages(response)).complete([{"role": "user", "content": "u"}], [])

    assert result.finish_reason == "length"


async def test_missing_key_raises_unavailable() -> None:
    client = AnthropicClient(ChatConfig())

    with pytest.raises(LLMUnavailable):
        await client.complete([{"role": "user", "content": "u"}], [])
