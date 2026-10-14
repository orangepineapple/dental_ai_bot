"""The agentic loop. Pure orchestration: no HTTP, no persistence of its own."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.chat.config import ChatConfig
from src.chat.constants import FALLBACK_REPLY, SYSTEM_PROMPT, TRUNCATED_REPLY
from src.chat.exceptions import LLMUnavailable
from src.chat.llm import LLMClient
from src.tools.base import ToolContext
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

Message = dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentResult:
    reply: str
    # Every message produced this turn (assistant tool requests, tool results, final
    # reply) in order, in provider format, ready to persist.
    new_messages: list[Message]
    degraded: bool = False


def build_system_prompt(timezone_name: str, patient_context: str = "") -> str:
    try:
        now = datetime.now(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        now = datetime.now(ZoneInfo("UTC"))
    prompt = f"{SYSTEM_PROMPT}\nToday is {now:%A %Y-%m-%d} ({now.tzinfo})."
    return f"{prompt}\n\n{patient_context}" if patient_context else prompt


async def run_agent_loop(*, llm: LLMClient, registry: ToolRegistry, history: list[Message], ctx_factory: Callable[[str], ToolContext], config: ChatConfig, patient_context: str = "") -> AgentResult:
    """Call the model, execute requested tools, repeat until it answers.

    `history` already ends with the new user message. `ctx_factory(tool_call_id)`
    builds the server-side ToolContext for each call.

    Bounded by config.MAX_TOOL_ROUNDS: after that many tool rounds the model is called
    once more with no tools so it must answer in text. Any provider failure returns
    the canned fallback rather than raising.
    """
    messages: list[Message] = [
        {"role": "system", "content": build_system_prompt(config.CLINIC_TIMEZONE, patient_context)},
        *history,
    ]
    new_messages: list[Message] = []
    tools = registry.openai_tools()

    def fallback(reason: str, text: str = FALLBACK_REPLY) -> AgentResult:
        if config.DEBUG_PRINT:
            print(f"[AGENT DEBUG] returning FALLBACK_REPLY because: {reason}")
        msg ={"role": "assistant", "content": FALLBACK_REPLY}
        return AgentResult(text, [*new_messages, msg], degraded=True)

    for round_index in range(config.MAX_TOOL_ROUNDS + 1):
        offer_tools = round_index < config.MAX_TOOL_ROUNDS
        try:
            response = await llm.complete(messages, tools if offer_tools else [])
        except LLMUnavailable as exc:
            return fallback(f"LLMUnavailable on round {round_index}: {exc}")

        if not response.tool_calls:
            if response.finish_reason == "length":
                # Cut off mid-output: with small reasoning models this is leaked chain of
                # thought, never a finished answer, so it must not reach the patient.
                return fallback("output hit MAX_OUTPUT_TOKENS (thinking too long)", TRUNCATED_REPLY)
            reply = (response.content or "").strip()
            if not reply:
                return fallback(f"model returned empty content and no tool calls on round {round_index}")
            new_messages.append({"role": "assistant", "content": reply})
            return AgentResult(reply, new_messages)

        if not offer_tools:
            # Model asked for tools although none were offered.
            return fallback(f"model requested tools after the {config.MAX_TOOL_ROUNDS}-round limit")

        assistant_msg: Message = {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ],
        }
        messages.append(assistant_msg)
        new_messages.append(assistant_msg)

        # Every tool_call id must get a tool message, including ones we refuse to run.
        for index, call in enumerate(response.tool_calls):
            if index >= config.MAX_TOOL_CALLS_PER_ROUND:
                output = '{"status": "error", "error": "too_many_tool_calls"}'
            else:
                output = await registry.dispatch(
                    call.name,
                    call.arguments,
                    ctx_factory(call.id),
                    timeout_seconds=config.TOOL_TIMEOUT_SECONDS,
                    max_output_chars=config.MAX_TOOL_OUTPUT_CHARS,
                )
            if config.DEBUG_PRINT:
                print(f"[AGENT DEBUG] tool {call.name} args={call.arguments} -> {output}")
            tool_msg: Message = {"role": "tool", "tool_call_id": call.id, "content": output}
            messages.append(tool_msg)
            new_messages.append(tool_msg)

    return fallback("loop ended without a reply")  # pragma: no cover - loop always returns above
