import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.chat.models import ChatConversation
from src.emergency import sender, service
from src.emergency.models import DELIVERED, FAILED, EmergencyNotification
from tests.integration.test_appointments import make_person

pytestmark = pytest.mark.integration

SUMMARY = "Knocked out a front tooth 20 minutes ago, heavy bleeding, pain 9/10"


async def _setup(session: AsyncSession):
    person = await make_person(session)
    conversation = ChatConversation(user_id=person.user_id)
    session.add(conversation)
    await session.commit()
    return person, conversation


def _fake_sender(monkeypatch: pytest.MonkeyPatch, *, fail: bool) -> list[dict]:
    calls: list[dict] = []

    async def fake(payload, *, idempotency_key, **kwargs):
        calls.append(payload)
        if fail:
            raise sender.StaffChannelError("staff channel unreachable (ConnectError)")

    monkeypatch.setattr(sender, "post_to_staff_channel", fake)
    return calls


async def test_delivered_notification_is_recorded(session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_sender(monkeypatch, fail=False)
    async with session_factory() as session:
        person, conversation = await _setup(session)
        outcome = await service.notify_staff_of_emergency(session, user_id=person.user_id, patient_id=person.patient_id, conversation_id=conversation.id, summary=SUMMARY)
        row = await session.get(EmergencyNotification, outcome.notification_id)

    assert outcome.status == DELIVERED
    assert row.status == DELIVERED and row.attempts == 1 and row.delivered_at is not None
    assert calls[0]["patient"]["first_name"] == "Jane"
    assert calls[0]["summary"] == SUMMARY


async def test_failed_delivery_is_not_reported_as_delivered_and_can_retry(session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_sender(monkeypatch, fail=True)
    async with session_factory() as session:
        person, conversation = await _setup(session)
        kwargs = dict(user_id=person.user_id, patient_id=person.patient_id, conversation_id=conversation.id, summary=SUMMARY)
        first = await service.notify_staff_of_emergency(session, **kwargs)
        row = await session.get(EmergencyNotification, first.notification_id)
        assert first.status == FAILED and row.delivered_at is None and "unreachable" in row.last_error

        _fake_sender(monkeypatch, fail=False)
        second = await service.notify_staff_of_emergency(session, **kwargs)
        rows = (await session.execute(select(EmergencyNotification))).scalars().all()

    assert second.notification_id == first.notification_id  # same row, not a duplicate report
    assert second.status == DELIVERED
    assert len(rows) == 1 and rows[0].attempts == 2


async def test_delivered_notification_is_not_resent(session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_sender(monkeypatch, fail=False)
    async with session_factory() as session:
        person, conversation = await _setup(session)
        kwargs = dict(user_id=person.user_id, patient_id=person.patient_id, conversation_id=conversation.id, summary=SUMMARY)
        await service.notify_staff_of_emergency(session, **kwargs)
        await service.notify_staff_of_emergency(session, **kwargs)

    assert len(calls) == 1


async def test_unreachable_dummy_endpoint_end_to_end(session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nothing listening")

    real = sender.post_to_staff_channel

    async def with_mock(payload, *, idempotency_key, **kwargs):
        await real(payload, idempotency_key=idempotency_key, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(sender, "post_to_staff_channel", with_mock)
    async with session_factory() as session:
        person, conversation = await _setup(session)
        outcome = await service.notify_staff_of_emergency(session, user_id=person.user_id, patient_id=person.patient_id, conversation_id=conversation.id, summary=SUMMARY)

    assert outcome.status == FAILED
