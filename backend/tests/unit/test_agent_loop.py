import json
import uuid
from typing import Any
from unittest.mock import MagicMock

from src.chat.agent import run_agent_loop
from src.chat.config import ChatConfig
from src.chat.constants import FALLBACK_REPLY
from src.chat.exceptions import LLMUnavailable
from src.chat.llm import LLMResponse, LLMToolCall
from src.tools.base import ToolContext
from src.tools.appointments.tools import GetAvailableSlotsArgs
from src.tools.base import ToolDefinition
from src.tools.registry import ToolRegistry


async def _stub_slots(args: GetAvailableSlotsArgs, ctx: ToolContext) -> dict[str, Any]:
    # The loop is tested in isolation from the DB-backed appointment service.
    return {"status": "ok", "slots": []}


tool_registry = ToolRegistry(
    [
        ToolDefinition(
            name="get_available_slots",
            description="stub",
            args_model=GetAvailableSlotsArgs,
            handler=_stub_slots,
        )
    ]
)


class FakeLLM:
    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, tools):
        self.calls.append({"messages": list(messages), "tools": tools})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ctx(tool_call_id: str) -> ToolContext:
    return ToolContext(
        session=MagicMock(),
        user_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        tool_call_id=tool_call_id,
    )


def _run(llm: FakeLLM, **config: Any):
    return run_agent_loop(
        llm=llm,
        registry=tool_registry,
        history=[{"role": "user", "content": "hi"}],
        ctx_factory=_ctx,
        config=ChatConfig(**config),
    )


def _slots_call(call_id: str = "c1") -> LLMToolCall:
    args = {"appointment_type": "cleaning", "start_date": "2026-11-01", "end_date": "2026-11-07"}
    return LLMToolCall(id=call_id, name="get_available_slots", arguments=json.dumps(args))


async def test_plain_answer_without_tools() -> None:
    result = await _run(FakeLLM([LLMResponse(content="Hello!")]))

    assert result.reply == "Hello!"
    assert not result.degraded
    assert result.new_messages == [{"role": "assistant", "content": "Hello!"}]


async def test_tool_round_trip() -> None:
    llm = FakeLLM(
        [LLMResponse(content=None, tool_calls=[_slots_call()]), LLMResponse(content="Sorry.")]
    )

    result = await _run(llm)

    roles = [m["role"] for m in result.new_messages]
    assert roles == ["assistant", "tool", "assistant"]
    assert result.new_messages[1]["tool_call_id"] == "c1"
    assert json.loads(result.new_messages[1]["content"])["status"] == "ok"
    # The tool result was fed back to the model on the second call.
    assert llm.calls[1]["messages"][-1]["role"] == "tool"


async def test_text_beside_tool_calls_is_not_persisted_or_replayed() -> None:
    leaked = LLMResponse(content="Okay, let me figure out...", tool_calls=[_slots_call()])
    llm = FakeLLM([leaked, LLMResponse(content="Here you go.")])

    result = await _run(llm)

    assert result.new_messages[0]["content"] is None
    assert result.new_messages[0]["tool_calls"][0]["id"] == "c1"
    replayed = llm.calls[1]["messages"]
    assert all("let me figure out" not in str(m.get("content")) for m in replayed)


async def test_unknown_tool_does_not_crash_loop() -> None:
    bad = LLMToolCall(id="c1", name="rm_rf", arguments="{}")
    llm = FakeLLM([LLMResponse(content=None, tool_calls=[bad]), LLMResponse(content="ok")])

    result = await _run(llm)

    assert json.loads(result.new_messages[1]["content"])["error"] == "unknown_tool"
    assert result.reply == "ok"


async def test_provider_failure_returns_fallback() -> None:
    result = await _run(FakeLLM([LLMUnavailable("down")]))

    assert result.reply == FALLBACK_REPLY
    assert result.degraded


async def test_tool_rounds_are_limited_then_answer_forced_without_tools() -> None:
    script = [LLMResponse(content=None, tool_calls=[_slots_call(f"c{i}")]) for i in range(2)]
    script.append(LLMResponse(content="Giving up."))
    llm = FakeLLM(script)

    result = await _run(llm, MAX_TOOL_ROUNDS=2)

    assert result.reply == "Giving up."
    assert len(llm.calls) == 3
    assert llm.calls[-1]["tools"] == []


async def test_model_ignoring_tool_limit_gets_fallback() -> None:
    script = [LLMResponse(content=None, tool_calls=[_slots_call(f"c{i}")]) for i in range(3)]

    result = await _run(FakeLLM(script), MAX_TOOL_ROUNDS=2)

    assert result.degraded


async def test_excess_tool_calls_in_one_round_are_refused() -> None:
    calls = [_slots_call(f"c{i}") for i in range(3)]
    llm = FakeLLM([LLMResponse(content=None, tool_calls=calls), LLMResponse(content="done")])

    result = await _run(llm, MAX_TOOL_CALLS_PER_ROUND=1)

    tool_msgs = [m for m in result.new_messages if m["role"] == "tool"]
    assert len(tool_msgs) == 3  # every tool_call id is answered
    assert json.loads(tool_msgs[1]["content"])["error"] == "too_many_tool_calls"
