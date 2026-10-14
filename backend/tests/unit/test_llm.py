from types import SimpleNamespace
from typing import Any

import pytest

from src.chat.config import ChatConfig
from src.chat.llm import OpenAICompatibleClient, strip_reasoning


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hello there", "Hello there"),
        ("<think>plan it</think>\n\nHi Brian!", "Hi Brian!"),
        ("<THINK>plan it</THINK> Hi", "Hi"),
        # Qwen3 template injects the opener, so only the closer is in the output.
        ("Okay, let me figure this out.\n</think>\n\nHi Brian!", "Hi Brian!"),
        ("a</think>b</think>c", "c"),
        # Unterminated reasoning (cut off) leaves nothing to show.
        ("<think>still thinking", ""),
        ("", ""),
    ],
)
def test_strip_reasoning(raw: str, expected: str) -> None:
    assert strip_reasoning(raw) == expected


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self.kwargs: dict[str, Any] = {}
        self._content = content

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        message = SimpleNamespace(content=self._content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


def _client(completions: _FakeCompletions, **config: Any) -> OpenAICompatibleClient:
    client = OpenAICompatibleClient(ChatConfig(API_KEY="test", **config))
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))  # type: ignore[assignment]
    return client


async def test_complete_strips_leaked_reasoning() -> None:
    completions = _FakeCompletions("Let me think.\n</think>\n\nHello!")

    response = await _client(completions).complete([{"role": "system", "content": "sys"}], [])

    assert response.content == "Hello!"


async def test_soft_no_think_appended_to_system_message_copy() -> None:
    completions = _FakeCompletions("ok")
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]

    await _client(completions).complete(messages, [])

    sent = completions.kwargs["messages"]
    assert sent[0]["content"].endswith("/no_think")
    assert sent[1] == {"role": "user", "content": "hi"}
    assert messages[0]["content"] == "sys"  # caller's list is untouched


async def test_soft_no_think_can_be_disabled() -> None:
    completions = _FakeCompletions("ok")

    await _client(completions, SOFT_NO_THINK=False).complete([{"role": "system", "content": "sys"}], [])

    assert completions.kwargs["messages"][0]["content"] == "sys"
