import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.appointments import proposals
from src.appointments.exceptions import NotAuthorized, NotFound, RequiresStaff, SlotUnavailable
from src.appointments.models import Appointment, BookingProposal
from src.appointments.seed import seed_sample_data
from src.chat.dependencies import get_llm_client
from src.chat.llm import LLMResponse
from src.main import app
from src.tools.base import ToolContext
from src.tools.registry import tool_registry
from tests.integration.test_appointments import Person, book, make_person, offered

pytestmark = pytest.mark.integration


@pytest.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as s:
        await seed_sample_data(s)
        yield s


@pytest.fixture
async def jane(session: AsyncSession) -> Person:
    return await make_person(session, "Jane")


@pytest.fixture
async def bob(session: AsyncSession) -> Person:
    return await make_person(session, "Bob")


async def _conversation(session: AsyncSession, person: Person) -> None:
    from src.chat.models import ChatConversation

    session.add(ChatConversation(id=person.conversation_id, user_id=person.user_id))
    await session.commit()


async def _propose(session: AsyncSession, person: Person, slot, **kwargs) -> proposals.ProposalView:
    params = {
        "actor_user_id": person.user_id,
        "actor_patient_id": person.patient_id,
        "target_patient_id": person.patient_id,
        "conversation_id": person.conversation_id,
        "slot_token": slot.token,
    }
    return await proposals.create_booking_proposal(session, **{**params, **kwargs})


async def _resolve(session: AsyncSession, person: Person, proposal_id: uuid.UUID, action: str):
    return await proposals.resolve_proposal(
        session,
        user_id=person.user_id,
        actor_patient_id=person.patient_id,
        proposal_id=proposal_id,
        action=action,
    )


async def _appointment_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Appointment))


async def test_proposing_books_and_reserves_nothing(session: AsyncSession, jane: Person) -> None:
    await _conversation(session, jane)
    slot = (await offered(session))[0]

    view = await _propose(session, jane, slot)

    assert view.status == "pending"
    assert view.starts_at == slot.starts_at
    assert view.patient_first_name == "Jane"
    assert await _appointment_count(session) == 0
    # The slot is still offered to everyone.
    assert any(s.starts_at == slot.starts_at and s.provider_id == slot.provider_id for s in await offered(session))


async def test_confirm_books_once_and_is_idempotent(session: AsyncSession, jane: Person) -> None:
    await _conversation(session, jane)
    slot = (await offered(session))[0]
    view = await _propose(session, jane, slot)

    first = await _resolve(session, jane, view.id, "confirm")
    second = await _resolve(session, jane, view.id, "confirm")

    assert first.status == "confirmed" and first.changed
    assert "booked" in first.message and slot.provider_name in first.message
    assert second.status == "confirmed" and not second.changed
    assert await _appointment_count(session) == 1
    assert await proposals.get_pending_proposal(
        session, user_id=jane.user_id, conversation_id=jane.conversation_id
    ) is None


async def test_decline_books_nothing_and_cannot_be_confirmed_later(session: AsyncSession, jane: Person) -> None:
    await _conversation(session, jane)
    view = await _propose(session, jane, (await offered(session))[0])

    declined = await _resolve(session, jane, view.id, "decline")
    late_confirm = await _resolve(session, jane, view.id, "confirm")

    assert declined.status == "declined"
    assert late_confirm.status == "declined" and not late_confirm.changed
    assert await _appointment_count(session) == 0


async def test_new_proposal_supersedes_the_old_one(session: AsyncSession, jane: Person) -> None:
    await _conversation(session, jane)
    slots = await offered(session)
    old = await _propose(session, jane, slots[0])
    new = await _propose(session, jane, slots[4])

    stale = await _resolve(session, jane, old.id, "confirm")

    assert stale.status == "superseded"
    assert await _appointment_count(session) == 0
    pending = await proposals.get_pending_proposal(
        session, user_id=jane.user_id, conversation_id=jane.conversation_id
    )
    assert pending is not None and pending.id == new.id


async def test_expired_proposal_cannot_be_confirmed(session: AsyncSession, jane: Person) -> None:
    await _conversation(session, jane)
    view = await _propose(session, jane, (await offered(session))[0])
    later = datetime.now(UTC) + timedelta(hours=1)

    outcome = await proposals.resolve_proposal(
        session,
        user_id=jane.user_id,
        actor_patient_id=jane.patient_id,
        proposal_id=view.id,
        action="confirm",
        now=later,
    )

    assert outcome.status == "expired"
    assert await _appointment_count(session) == 0


async def test_slot_taken_between_proposal_and_confirmation(session: AsyncSession, jane: Person, bob: Person) -> None:
    await _conversation(session, jane)
    slot = (await offered(session))[0]
    view = await _propose(session, jane, slot)
    await book(session, bob, slot)

    outcome = await _resolve(session, jane, view.id, "confirm")

    assert outcome.status == "failed"
    assert "could not complete" in outcome.message
    assert await _appointment_count(session) == 1  # only Bob's


async def test_other_users_cannot_resolve_my_proposal(session: AsyncSession, jane: Person, bob: Person) -> None:
    await _conversation(session, jane)
    view = await _propose(session, jane, (await offered(session))[0])

    with pytest.raises(NotFound):
        await _resolve(session, bob, view.id, "confirm")

    row = await session.get(BookingProposal, view.id)
    assert row.status == "pending"


async def test_proposal_rechecks_authorization_and_rules(session: AsyncSession, jane: Person, bob: Person) -> None:
    await _conversation(session, jane)
    slots = await offered(session)

    with pytest.raises(NotAuthorized):
        await _propose(session, jane, slots[0], target_patient_id=bob.patient_id)
    await book(session, bob, slots[0])
    with pytest.raises(SlotUnavailable):
        await _propose(session, jane, slots[0])
    with pytest.raises(SlotUnavailable):
        await _propose(session, jane, slots[0].__class__("bad.token", slots[0].starts_at, slots[0].provider_id, "x"))
    with pytest.raises(RequiresStaff):
        staff_only = (await offered(session, type_code="cleaning"))[0]
        # Same signed slot, but the type was flipped to staff-only after it was offered.
        from src.appointments.models import AppointmentType

        cleaning = await session.get(AppointmentType, "cleaning")
        cleaning.requires_staff_booking = True
        await session.commit()
        await _propose(session, jane, staff_only)


async def test_propose_booking_tool_returns_card_instruction_not_a_booking(session: AsyncSession, jane: Person) -> None:
    await _conversation(session, jane)
    slot = (await offered(session))[0]
    ctx = ToolContext(session, jane.user_id, jane.patient_id, jane.conversation_id, "call_1")

    out = json.loads(
        await tool_registry.dispatch("propose_booking", json.dumps({"slot_id": slot.token}), ctx)
    )

    assert out["status"] == "awaiting_patient_confirmation"
    assert "appointment_id" not in out
    assert await _appointment_count(session) == 0


# --------------------------------------------------------------------------- #
# HTTP: the buttons on the card
# --------------------------------------------------------------------------- #
PASSWORD = "a-strong-password"


class _StubLLM:
    async def complete(self, messages, tools):
        return LLMResponse(content="Please confirm the card below.")


async def _register(client: AsyncClient, email: str) -> dict:
    resp = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "first_name": "Jane",
            "last_name": "Doe",
            "birth_date": "1990-04-01",
        },
    )
    assert resp.status_code == 201, resp.text
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture
def stub_llm():
    app.dependency_overrides[get_llm_client] = lambda: _StubLLM()
    yield
    app.dependency_overrides.pop(get_llm_client, None)


async def test_confirm_endpoint_books_and_records_transcript(client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], stub_llm) -> None:
    headers = await _register(client, "jane@example.com")
    me = (await client.get("/auth/me", headers=headers)).json()
    first = await client.post("/chat/messages", json={"message": "book a cleaning"}, headers=headers)
    conversation_id = uuid.UUID(first.json()["conversation_id"])
    assert first.json()["proposal"] is None

    async with session_factory() as s:
        await seed_sample_data(s)
        slot = (await offered(s))[0]
        await proposals.create_booking_proposal(
            s,
            actor_user_id=uuid.UUID(me["id"]),
            actor_patient_id=uuid.UUID(me["patient"]["id"]),
            target_patient_id=uuid.UUID(me["patient"]["id"]),
            conversation_id=conversation_id,
            slot_token=slot.token,
        )

    # The card is returned with the next reply and when resuming the conversation.
    again = await client.post(
        "/chat/messages",
        json={"message": "that one", "conversation_id": str(conversation_id)},
        headers=headers,
    )
    card = again.json()["proposal"]
    assert card["appointment_type"] == "Cleaning" and card["display_time"]
    resumed = await client.get(f"/chat/conversations/{conversation_id}/proposal", headers=headers)
    assert resumed.json()["id"] == card["id"]

    confirmed = await client.post(f"/chat/proposals/{card['id']}/confirm", headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    repeat = await client.post(f"/chat/proposals/{card['id']}/confirm", headers=headers)
    assert repeat.json()["status"] == "confirmed"

    transcript = await client.get(
        f"/chat/conversations/{conversation_id}/messages", headers=headers
    )
    texts = [m["content"] for m in transcript.json()]
    assert sum("booked for" in t for t in texts) == 1  # not duplicated by the repeat click
    gone = await client.get(f"/chat/conversations/{conversation_id}/proposal", headers=headers)
    assert gone.json() is None


async def test_proposal_endpoints_require_auth_and_hide_other_users(client: AsyncClient, session_factory: async_sessionmaker[AsyncSession], stub_llm) -> None:
    assert (await client.post(f"/chat/proposals/{uuid.uuid4()}/confirm")).status_code == 401

    headers = await _register(client, "jane@example.com")
    other = await _register(client, "other@example.com")
    resp = await client.post(f"/chat/proposals/{uuid.uuid4()}/decline", headers=other)
    assert resp.status_code == 404
    assert headers != other
