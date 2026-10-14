import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.emergency import sender, service
from src.emergency.config import EmergencyConfig
from src.emergency.models import DELIVERED, FAILED
from src.tools.base import ToolContext
from src.tools.registry import tool_registry


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session=MagicMock(), user_id=uuid.uuid4(), patient_id=uuid.uuid4(), conversation_id=uuid.uuid4(), tool_call_id="call_1")


def _config() -> EmergencyConfig:
    return EmergencyConfig(STAFF_WEBHOOK_URL="http://staff.test/notify", STAFF_WEBHOOK_TOKEN="tok")


async def test_sender_posts_payload_with_idempotency_and_auth() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["Idempotency-Key"]
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(202)

    await sender.post_to_staff_channel({"summary": "x"}, idempotency_key="k1", config=_config(), transport=httpx.MockTransport(handler))

    assert seen == {"url": "http://staff.test/notify", "key": "k1", "auth": "Bearer tok", "body": {"summary": "x"}}


@pytest.mark.parametrize("status", [400, 500])
async def test_sender_non_2xx_is_failure(status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    with pytest.raises(sender.StaffChannelError, match=str(status)):
        await sender.post_to_staff_channel({}, idempotency_key="k", config=_config(), transport=transport)


async def test_sender_unreachable_is_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(sender.StaffChannelError, match="unreachable"):
        await sender.post_to_staff_channel({}, idempotency_key="k", config=_config(), transport=httpx.MockTransport(handler))


def test_idempotency_key_ignores_case_and_whitespace_but_not_conversation() -> None:
    conversation = uuid.uuid4()
    assert service._idempotency_key(conversation, "Tooth  hurts") == service._idempotency_key(conversation, "tooth hurts")
    assert service._idempotency_key(conversation, "tooth hurts") != service._idempotency_key(uuid.uuid4(), "tooth hurts")


async def test_get_emergency_instructions_returns_configured_text(ctx: ToolContext) -> None:
    out = json.loads(await tool_registry.dispatch("get_emergency_instructions", "{}", ctx))
    assert out["status"] == "ok"
    assert out["instructions"]


async def test_notify_reports_staff_notified_only_when_delivered(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = service.NotificationOutcome(uuid.uuid4(), DELIVERED)
    monkeypatch.setattr(service, "notify_staff_of_emergency", AsyncMock(return_value=outcome))
    args = json.dumps({"summary": "Knocked out a front tooth 20 minutes ago, bleeding"})
    out = json.loads(await tool_registry.dispatch("notify_staff_of_emergency", args, ctx))
    assert out["status"] == "staff_notified"


async def test_notify_reports_failure_honestly(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = service.NotificationOutcome(uuid.uuid4(), FAILED)
    monkeypatch.setattr(service, "notify_staff_of_emergency", AsyncMock(return_value=outcome))
    args = json.dumps({"summary": "Knocked out a front tooth 20 minutes ago, bleeding"})
    out = json.loads(await tool_registry.dispatch("notify_staff_of_emergency", args, ctx))
    assert out["status"] == "delivery_failed"
    assert "NOT" in out["message"]


async def test_notify_rejects_extra_and_short_arguments(ctx: ToolContext) -> None:
    for args in ({"summary": "short"}, {"summary": "long enough summary", "patient_id": str(uuid.uuid4())}):
        out = json.loads(await tool_registry.dispatch("notify_staff_of_emergency", json.dumps(args), ctx))
        assert out["error"] == "invalid_arguments"
