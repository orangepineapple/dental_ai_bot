import json
import uuid
from unittest.mock import MagicMock

import pytest

from src.tools.base import ToolContext
from src.tools.registry import ToolRegistry, tool_registry

EXPECTED_TOOLS = {
    "get_available_slots",
    "book_appointment",
    "reschedule_appointment",
    "cancel_appointment",
}


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(
        session=MagicMock(),
        user_id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        tool_call_id="call_1",
    )


def test_default_registry_contains_appointment_tools() -> None:
    assert set(tool_registry.names) == EXPECTED_TOOLS


def test_schemas_forbid_additional_properties_and_hide_identity() -> None:
    for tool in tool_registry.openai_tools():
        params = tool["function"]["parameters"]
        assert params["additionalProperties"] is False
        # Authorization identity must come from the server, never from the model.
        assert "user_id" not in params["properties"]


def test_duplicate_registration_rejected() -> None:
    registry = ToolRegistry(list(tool_registry._tools.values())[:1])
    with pytest.raises(ValueError):
        registry.register(list(tool_registry._tools.values())[0])


async def test_unknown_tool_is_refused(ctx: ToolContext) -> None:
    out = json.loads(await tool_registry.dispatch("drop_tables", "{}", ctx))
    assert out["error"] == "unknown_tool"


async def test_extra_argument_is_rejected(ctx: ToolContext) -> None:
    args = {"appointment_id": str(uuid.uuid4()), "confirmed": True, "patient_id": "x"}
    out = json.loads(await tool_registry.dispatch("cancel_appointment", json.dumps(args), ctx))
    assert out["error"] == "invalid_arguments"


async def test_invalid_json_is_rejected(ctx: ToolContext) -> None:
    out = json.loads(await tool_registry.dispatch("get_available_slots", "{not json", ctx))
    assert out["error"] == "invalid_arguments"


async def test_date_range_is_bounded(ctx: ToolContext) -> None:
    args = {"appointment_type": "cleaning", "start_date": "2026-11-01", "end_date": "2027-03-01"}
    out = json.loads(await tool_registry.dispatch("get_available_slots", json.dumps(args), ctx))
    assert out["error"] == "invalid_arguments"


async def test_stub_returns_not_implemented(ctx: ToolContext) -> None:
    args = {"appointment_type": "cleaning", "start_date": "2026-11-01", "end_date": "2026-11-07"}
    out = json.loads(await tool_registry.dispatch("get_available_slots", json.dumps(args), ctx))
    assert out["status"] == "not_implemented"
