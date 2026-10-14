import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.appointments import cancel_proposals, proposals
from src.appointments.config import appointment_settings
from src.appointments.exceptions import NotFound, PolicyViolation, RequiresStaff
from src.appointments.models import Appointment, BookingProposal
from src.appointments.seed import seed_sample_data
from src.tools.base import ToolContext
from src.tools.registry import tool_registry
from tests.integration.test_appointments import Person, book, insert_appointment, make_person, offered

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


async def _booked(session: AsyncSession, person: Person):
    await _conversation(session, person)
    return await book(session, person, (await offered(session))[0])


async def _propose(session: AsyncSession, person: Person, appointment_id: uuid.UUID, **kwargs):
    params = {
        "actor_user_id": person.user_id,
        "actor_patient_id": person.patient_id,
        "conversation_id": person.conversation_id,
        "appointment_id": appointment_id,
    }
    return await cancel_proposals.create_cancellation_proposal(session, **{**params, **kwargs})


async def _resolve(session: AsyncSession, person: Person, proposal_id: uuid.UUID, action: str, **kwargs):
    return await cancel_proposals.resolve_cancellation(
        session,
        user_id=person.user_id,
        actor_patient_id=person.patient_id,
        proposal_id=proposal_id,
        action=action,
        **kwargs,
    )


async def test_proposing_cancels_nothing(session: AsyncSession, jane: Person) -> None:
    appt = await _booked(session, jane)

    view = await _propose(session, jane, appt.id)

    assert view.status == "pending"
    assert view.appointment_id == appt.id
    assert view.patient_first_name == "Jane"
    assert (await session.get(Appointment, appt.id)).status == "booked"


async def test_confirm_cancels_once_and_is_idempotent(session: AsyncSession, jane: Person) -> None:
    appt = await _booked(session, jane)
    view = await _propose(session, jane, appt.id, reason="conflict")

    first = await _resolve(session, jane, view.id, "confirm")
    second = await _resolve(session, jane, view.id, "confirm")

    assert first.status == "confirmed" and first.changed
    assert "cancelled" in first.message
    assert second.status == "confirmed" and not second.changed
    assert second.message == first.message
    assert (await session.get(Appointment, appt.id)).status == "cancelled"
    assert await cancel_proposals.get_pending_cancellation(
        session, user_id=jane.user_id, conversation_id=jane.conversation_id
    ) is None


async def test_decline_keeps_the_appointment_and_cannot_be_confirmed_later(session: AsyncSession, jane: Person) -> None:
    appt = await _booked(session, jane)
    view = await _propose(session, jane, appt.id)

    declined = await _resolve(session, jane, view.id, "decline")
    late_confirm = await _resolve(session, jane, view.id, "confirm")

    assert declined.status == "declined"
    assert late_confirm.status == "declined" and not late_confirm.changed
    assert (await session.get(Appointment, appt.id)).status == "booked"


async def test_expired_proposal_cannot_be_confirmed(session: AsyncSession, jane: Person) -> None:
    appt = await _booked(session, jane)
    view = await _propose(session, jane, appt.id)

    outcome = await _resolve(session, jane, view.id, "confirm", now=datetime.now(UTC) + timedelta(hours=1))

    assert outcome.status == "expired"
    assert (await session.get(Appointment, appt.id)).status == "booked"


async def test_other_patient_cannot_propose_or_resolve(session: AsyncSession, jane: Person, bob: Person) -> None:
    appt = await _booked(session, jane)
    await _conversation(session, bob)

    with pytest.raises(NotFound):
        await _propose(session, bob, appt.id)

    view = await _propose(session, jane, appt.id)
    with pytest.raises(NotFound):
        await _resolve(session, bob, view.id, "confirm")
    assert (await session.get(Appointment, appt.id)).status == "booked"


async def test_late_appointment_is_refused_up_front_by_default(session: AsyncSession, jane: Person) -> None:
    await _conversation(session, jane)
    appt = await insert_appointment(session, jane.patient_id, datetime.now(UTC) + timedelta(hours=2))

    with pytest.raises(RequiresStaff):
        await _propose(session, jane, appt.id)


async def test_late_cancellation_card_carries_policy_note_when_allowed(session: AsyncSession, jane: Person) -> None:
    config = appointment_settings.model_copy(
        update={"LATE_CANCEL_ACTION": "allow", "LATE_CANCEL_POLICY_NOTE": "Clinic wording."}
    )
    await _conversation(session, jane)
    appt = await insert_appointment(session, jane.patient_id, datetime.now(UTC) + timedelta(hours=2))

    view = await _propose(session, jane, appt.id, config=config)

    assert view.late_cancellation is True
    assert view.policy_note == "Clinic wording."


async def test_past_or_already_cancelled_appointment_is_refused(session: AsyncSession, jane: Person) -> None:
    await _conversation(session, jane)
    past = await insert_appointment(session, jane.patient_id, datetime.now(UTC) - timedelta(days=1))
    cancelled = await insert_appointment(
        session, jane.patient_id, datetime.now(UTC) + timedelta(days=30), status="cancelled"
    )

    with pytest.raises(PolicyViolation):
        await _propose(session, jane, past.id)
    with pytest.raises(PolicyViolation):
        await _propose(session, jane, cancelled.id)


async def test_new_cancellation_supersedes_older_cards(session: AsyncSession, jane: Person) -> None:
    appt = await _booked(session, jane)
    slot = (await offered(session))[4]
    booking = await proposals.create_booking_proposal(
        session,
        actor_user_id=jane.user_id,
        actor_patient_id=jane.patient_id,
        target_patient_id=jane.patient_id,
        conversation_id=jane.conversation_id,
        slot_token=slot.token,
    )

    cancellation = await _propose(session, jane, appt.id)

    assert (await session.get(BookingProposal, booking.id)).status == "superseded"
    pending = await cancel_proposals.get_pending_cancellation(
        session, user_id=jane.user_id, conversation_id=jane.conversation_id
    )
    assert pending is not None and pending.id == cancellation.id

    # ...and a newer booking proposal supersedes the cancellation card in turn.
    await proposals.create_booking_proposal(
        session,
        actor_user_id=jane.user_id,
        actor_patient_id=jane.patient_id,
        target_patient_id=jane.patient_id,
        conversation_id=jane.conversation_id,
        slot_token=slot.token,
    )
    stale = await _resolve(session, jane, cancellation.id, "confirm")
    assert stale.status == "superseded"
    assert (await session.get(Appointment, appt.id)).status == "booked"


async def test_propose_cancellation_tool_returns_card_instruction_not_a_cancellation(session: AsyncSession, jane: Person) -> None:
    appt = await _booked(session, jane)
    ctx = ToolContext(session, jane.user_id, jane.patient_id, jane.conversation_id, "call_1")

    out = json.loads(
        await tool_registry.dispatch(
            "propose_cancellation", json.dumps({"appointment_id": str(appt.id)}), ctx
        )
    )

    assert out["status"] == "awaiting_patient_confirmation"
    assert (await session.get(Appointment, appt.id)).status == "booked"
