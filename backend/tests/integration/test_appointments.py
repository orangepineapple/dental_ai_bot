import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.appointments import cancel_proposals, proposals, service
from src.appointments.config import appointment_settings
from src.appointments.exceptions import (
    NeedsConfirmation,
    NotAuthorized,
    NotFound,
    PolicyViolation,
    RequiresStaff,
    SlotUnavailable,
    UnknownAppointmentType,
)
from src.appointments.models import Appointment, AppointmentType, ClinicClosure, Provider
from src.appointments.seed import seed_sample_data
from src.auth.models import AuthUser
from src.chat.models import ChatConversation
from src.family.models import PatientGuardian
from src.patients.models import Patient
from src.tools.base import ToolContext
from src.tools.registry import tool_registry

pytestmark = pytest.mark.integration

TZ = ZoneInfo(appointment_settings.CLINIC_TIMEZONE)


def next_monday(min_days_ahead: int = 14) -> date:
    day = datetime.now(TZ).date() + timedelta(days=min_days_ahead)
    return day + timedelta(days=(7 - day.weekday()) % 7)


class Person:
    def __init__(self, patient: Patient, user: AuthUser) -> None:
        # Plain ids: the ORM objects expire on commit and cannot lazy-load in async.
        self.patient_id = patient.id
        self.user_id = user.id
        self.conversation_id = uuid.uuid4()


async def make_person(session: AsyncSession, name: str = "Jane", birth: date = date(1990, 4, 1)) -> Person:
    patient = Patient(first_name=name, last_name="Doe", birth_date=birth)
    session.add(patient)
    await session.flush()
    user = AuthUser(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", patient_id=patient.id)
    session.add(user)
    await session.commit()
    return Person(patient, user)


@pytest.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        await seed_sample_data(s)
        yield s


@pytest.fixture
async def jane(session: AsyncSession) -> Person:
    return await make_person(session, "Jane")


@pytest.fixture
async def bob(session: AsyncSession) -> Person:
    return await make_person(session, "Bob")


async def offered(session: AsyncSession, day: date | None = None, type_code: str = "cleaning") -> list[service.SlotOption]:
    day = day or next_monday()
    result = await service.get_available_slots(
        session, appointment_type_code=type_code, start_date=day, end_date=day
    )
    return result.slots


async def book(session: AsyncSession, person: Person, slot: service.SlotOption, **kwargs) -> service.AppointmentView:
    params = {
        "actor_user_id": person.user_id,
        "actor_patient_id": person.patient_id,
        "target_patient_id": person.patient_id,
        "conversation_id": person.conversation_id,
        "slot_token": slot.token,
        "confirmed": True,
    }
    return await service.book_appointment(session, **{**params, **kwargs})


async def insert_appointment(session: AsyncSession, patient_id: uuid.UUID, starts_at: datetime, status: str = "booked", type_code: str = "exam") -> Appointment:
    provider = (await session.execute(select(Provider).order_by(Provider.name))).scalars().first()
    appt = Appointment(
        patient_id=patient_id,
        provider_id=provider.id,
        appointment_type_code=type_code,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        status=status,
    )
    session.add(appt)
    await session.commit()
    return appt


# --------------------------------------------------------------------------- #
# reference data and availability
# --------------------------------------------------------------------------- #
async def test_appointment_types_list_active_only(session: AsyncSession) -> None:
    exam = await session.get(AppointmentType, "exam")
    exam.is_active = False
    await session.commit()

    codes = {t.code for t in await service.list_appointment_types(session)}

    assert "exam" not in codes
    assert {"cleaning", "filling"} <= codes


async def test_slots_come_from_hours_and_type_duration(session: AsyncSession) -> None:
    slots = await offered(session)

    assert slots
    assert len(slots) <= appointment_settings.MAX_SLOTS_RETURNED
    first = slots[0].starts_at.astimezone(TZ)
    assert (first.hour, first.minute) == (9, 0)
    assert {s.provider_name for s in slots} <= {"Dr. Lee", "Dr. Patel"}
    assert [s.starts_at for s in slots] == sorted(s.starts_at for s in slots)


async def test_weekend_and_closure_have_no_slots(session: AsyncSession) -> None:
    saturday = next_monday() + timedelta(days=5)
    assert await offered(session, saturday) == []

    monday = next_monday(21)
    session.add(ClinicClosure(closure_date=monday, reason="holiday"))
    await session.commit()
    assert await offered(session, monday) == []


async def test_past_dates_and_far_future_return_nothing(session: AsyncSession) -> None:
    assert await offered(session, date.today() - timedelta(days=3)) == []
    far = date.today() + timedelta(days=appointment_settings.MAX_ADVANCE_DAYS + 30)
    assert await offered(session, far) == []


async def test_unknown_and_staff_only_types(session: AsyncSession) -> None:
    with pytest.raises(UnknownAppointmentType):
        await offered(session, type_code="teleportation")
    with pytest.raises(RequiresStaff):
        await offered(session, type_code="wisdom_tooth_extraction")


async def test_booked_slot_disappears_but_searching_reserves_nothing(session: AsyncSession, jane: Person) -> None:
    before = await offered(session)
    assert await session.scalar(select(func.count()).select_from(Appointment)) == 0

    await book(session, jane, before[0])
    after = await offered(session)

    assert (before[0].provider_id, before[0].starts_at) not in {(s.provider_id, s.starts_at) for s in after}
    # The other provider is still free at that time.
    assert any(s.starts_at == before[0].starts_at for s in after)


# --------------------------------------------------------------------------- #
# booking
# --------------------------------------------------------------------------- #
async def test_booking_requires_confirmation_and_writes_nothing(session: AsyncSession, jane: Person) -> None:
    slot = (await offered(session))[0]

    with pytest.raises(NeedsConfirmation):
        await book(session, jane, slot, confirmed=False)

    assert await session.scalar(select(func.count()).select_from(Appointment)) == 0


async def test_booking_succeeds_and_records_audit(session: AsyncSession, jane: Person) -> None:
    slot = (await offered(session))[0]

    view = await book(session, jane, slot, reason="tooth hurts a bit")

    assert view.status == "booked"
    assert view.patient_first_name == "Jane"
    assert view.starts_at == slot.starts_at
    assert view.ends_at - view.starts_at == timedelta(minutes=60)  # cleaning duration from the type
    row = await session.get(Appointment, view.id)
    assert row.booked_by_user_id == jane.user_id


async def test_retry_is_idempotent(session: AsyncSession, jane: Person) -> None:
    slot = (await offered(session))[0]

    first = await book(session, jane, slot)
    second = await book(session, jane, slot)

    assert first.id == second.id
    assert await session.scalar(select(func.count()).select_from(Appointment)) == 1
    status = await service.get_booking_status(
        session,
        actor_patient_id=jane.patient_id,
        target_patient_id=jane.patient_id,
        conversation_id=jane.conversation_id,
        slot_token=slot.token,
    )
    assert status is not None and status.id == first.id


async def test_booking_status_is_none_when_not_booked(session: AsyncSession, jane: Person) -> None:
    slot = (await offered(session))[0]

    status = await service.get_booking_status(
        session,
        actor_patient_id=jane.patient_id,
        target_patient_id=jane.patient_id,
        conversation_id=jane.conversation_id,
        slot_token=slot.token,
    )

    assert status is None


async def test_second_patient_cannot_take_booked_slot(session: AsyncSession, jane: Person, bob: Person) -> None:
    slot = (await offered(session))[0]
    await book(session, jane, slot)

    with pytest.raises(SlotUnavailable):
        await book(session, bob, slot)


async def test_concurrent_bookings_of_one_slot_yield_one_winner(session_factory: async_sessionmaker[AsyncSession], session: AsyncSession, jane: Person, bob: Person) -> None:
    slot = (await offered(session))[0]

    async def attempt(person: Person):
        async with session_factory() as s:
            try:
                return await book(s, person, slot)
            except SlotUnavailable:
                await s.rollback()
                return None

    results = await asyncio.gather(attempt(jane), attempt(bob))

    assert len([r for r in results if r is not None]) == 1
    assert await session.scalar(select(func.count()).select_from(Appointment)) == 1


async def test_forged_or_foreign_slot_token_is_rejected(session: AsyncSession, jane: Person) -> None:
    slot = (await offered(session))[0]
    forged = service.SlotOption("abc.def", slot.starts_at, slot.provider_id, slot.provider_name)

    with pytest.raises(SlotUnavailable):
        await book(session, jane, forged)


async def test_patient_cannot_double_book_themselves(session: AsyncSession, jane: Person) -> None:
    slots = await offered(session)
    same_time = [s for s in slots if s.starts_at == slots[0].starts_at]
    assert len(same_time) == 2  # two providers
    await book(session, jane, same_time[0])

    with pytest.raises(PolicyViolation):
        await book(session, jane, same_time[1])


async def test_cannot_book_for_stranger(session: AsyncSession, jane: Person, bob: Person) -> None:
    slot = (await offered(session))[0]

    with pytest.raises(NotAuthorized):
        await book(session, jane, slot, target_patient_id=bob.patient_id)

    assert await session.scalar(select(func.count()).select_from(Appointment)) == 0


async def test_guardian_can_book_for_dependent_only_with_link(session: AsyncSession, jane: Person) -> None:
    child = await make_person(session, "Kid", birth=date(2018, 5, 5))
    slot = (await offered(session))[0]
    with pytest.raises(NotAuthorized):
        await book(session, jane, slot, target_patient_id=child.patient_id)

    session.add(PatientGuardian(guardian_patient_id=jane.patient_id, dependent_patient_id=child.patient_id))
    await session.commit()
    view = await book(session, jane, slot, target_patient_id=child.patient_id)

    assert view.patient_id == child.patient_id
    assert view.patient_first_name == "Kid"


async def test_dependent_link_without_can_book_is_not_enough(session: AsyncSession, jane: Person) -> None:
    child = await make_person(session, "Kid", birth=date(2018, 5, 5))
    session.add(
        PatientGuardian(
            guardian_patient_id=jane.patient_id, dependent_patient_id=child.patient_id, can_book=False
        )
    )
    await session.commit()
    slot = (await offered(session))[0]

    with pytest.raises(NotAuthorized):
        await book(session, jane, slot, target_patient_id=child.patient_id)


async def test_minimum_age_is_enforced(session: AsyncSession, jane: Person) -> None:
    exam = await session.get(AppointmentType, "exam")
    exam.min_age_years = 18
    await session.commit()
    child = await make_person(session, "Kid", birth=date(2018, 5, 5))
    session.add(PatientGuardian(guardian_patient_id=jane.patient_id, dependent_patient_id=child.patient_id))
    await session.commit()
    slot = (await offered(session, type_code="exam"))[0]

    with pytest.raises(PolicyViolation):
        await book(session, jane, slot, target_patient_id=child.patient_id)


# --------------------------------------------------------------------------- #
# read access
# --------------------------------------------------------------------------- #
async def test_listing_and_details_are_scoped_to_the_patient(session: AsyncSession, jane: Person, bob: Person) -> None:
    view = await book(session, jane, (await offered(session))[0])

    mine = await service.list_patient_appointments(
        session, actor_patient_id=jane.patient_id, patient_id=None, include_past=False, limit=10
    )
    theirs = await service.list_patient_appointments(
        session, actor_patient_id=bob.patient_id, patient_id=None, include_past=False, limit=10
    )
    assert [a.id for a in mine] == [view.id]
    assert theirs == []

    with pytest.raises(NotAuthorized):
        await service.list_patient_appointments(
            session, actor_patient_id=bob.patient_id, patient_id=jane.patient_id, include_past=False, limit=10
        )
    with pytest.raises(NotFound):
        await service.get_appointment_details(
            session, actor_patient_id=bob.patient_id, appointment_id=view.id
        )
    with pytest.raises(NotFound):  # same answer as a nonexistent id
        await service.get_appointment_details(
            session, actor_patient_id=bob.patient_id, appointment_id=uuid.uuid4()
        )


# --------------------------------------------------------------------------- #
# cancel
# --------------------------------------------------------------------------- #
async def _cancel(session: AsyncSession, person: Person, appointment_id: uuid.UUID, **kwargs):
    params = {
        "actor_user_id": person.user_id,
        "actor_patient_id": person.patient_id,
        "appointment_id": appointment_id,
        "confirmed": True,
    }
    return await service.cancel_appointment(session, **{**params, **kwargs})


async def test_cancel_frees_the_slot_and_is_idempotent(session: AsyncSession, jane: Person) -> None:
    slot = (await offered(session))[0]
    view = await book(session, jane, slot)

    outcome = await _cancel(session, jane, view.id, reason="conflict")
    again = await _cancel(session, jane, view.id)

    assert outcome.appointment.status == "cancelled"
    assert outcome.late_cancellation is False
    assert again.appointment.status == "cancelled"
    row = await session.get(Appointment, view.id)
    assert row.cancelled_at is not None and row.cancellation_reason == "conflict"
    # Soft cancel: row kept, slot bookable again, even in the same conversation.
    rebooked = await book(session, jane, slot)
    assert rebooked.id != view.id


async def test_cancel_needs_confirmation_and_authorization(session: AsyncSession, jane: Person, bob: Person) -> None:
    view = await book(session, jane, (await offered(session))[0])

    with pytest.raises(NeedsConfirmation):
        await _cancel(session, jane, view.id, confirmed=False)
    with pytest.raises(NotFound):
        await _cancel(session, bob, view.id)

    assert (await session.get(Appointment, view.id)).status == "booked"


async def test_late_cancellation_goes_to_staff_by_default(session: AsyncSession, jane: Person) -> None:
    soon = datetime.now(UTC) + timedelta(hours=2)
    appt = await insert_appointment(session, jane.patient_id, soon)

    with pytest.raises(RequiresStaff):
        await _cancel(session, jane, appt.id)

    assert (await session.get(Appointment, appt.id)).status == "booked"


async def test_late_cancellation_allowed_when_clinic_policy_says_so(session: AsyncSession, jane: Person) -> None:
    config = appointment_settings.model_copy(
        update={"LATE_CANCEL_ACTION": "allow", "LATE_CANCEL_POLICY_NOTE": "Clinic wording."}
    )
    appt = await insert_appointment(session, jane.patient_id, datetime.now(UTC) + timedelta(hours=2))

    outcome = await _cancel(session, jane, appt.id, config=config)

    assert outcome.late_cancellation is True
    assert outcome.policy_note == "Clinic wording."


async def test_past_appointment_cannot_be_cancelled(session: AsyncSession, jane: Person) -> None:
    appt = await insert_appointment(session, jane.patient_id, datetime.now(UTC) - timedelta(days=1))

    with pytest.raises(PolicyViolation):
        await _cancel(session, jane, appt.id)


# --------------------------------------------------------------------------- #
# reschedule
# --------------------------------------------------------------------------- #
async def _reschedule(session: AsyncSession, person: Person, appointment_id: uuid.UUID, slot: service.SlotOption, **kwargs):
    params = {
        "actor_user_id": person.user_id,
        "actor_patient_id": person.patient_id,
        "appointment_id": appointment_id,
        "new_slot_token": slot.token,
        "confirmed": True,
    }
    return await service.reschedule_appointment(session, **{**params, **kwargs})


async def test_reschedule_moves_the_same_appointment(session: AsyncSession, jane: Person) -> None:
    slots = await offered(session)
    view = await book(session, jane, slots[0])
    target = next(s for s in slots if s.starts_at >= slots[0].starts_at + timedelta(hours=2))

    outcome = await _reschedule(session, jane, view.id, target)

    assert outcome.appointment.id == view.id
    assert outcome.appointment.starts_at == target.starts_at
    assert outcome.old_starts_at == slots[0].starts_at
    # Old time is free again and there is still exactly one appointment.
    assert await session.scalar(select(func.count()).select_from(Appointment)) == 1
    assert any(s.starts_at == slots[0].starts_at for s in await offered(session))
    # Retrying the same reschedule is a no-op success.
    again = await _reschedule(session, jane, view.id, target)
    assert again.appointment.starts_at == target.starts_at


async def test_reschedule_to_taken_slot_fails_and_keeps_original(session: AsyncSession, jane: Person, bob: Person) -> None:
    slots = await offered(session)
    mine = await book(session, jane, slots[0])
    other = next(s for s in slots if s.starts_at >= slots[0].starts_at + timedelta(hours=2))
    await book(session, bob, other)

    with pytest.raises(SlotUnavailable):
        await _reschedule(session, jane, mine.id, other)

    assert (await session.get(Appointment, mine.id)).starts_at == slots[0].starts_at


async def test_reschedule_authorization_confirmation_type_and_window(session: AsyncSession, jane: Person, bob: Person) -> None:
    slots = await offered(session)
    view = await book(session, jane, slots[0])
    later = next(s for s in slots if s.starts_at >= slots[0].starts_at + timedelta(hours=2))

    with pytest.raises(NeedsConfirmation):
        await _reschedule(session, jane, view.id, later, confirmed=False)
    with pytest.raises(NotFound):
        await _reschedule(session, bob, view.id, later)

    exam_slot = (await offered(session, type_code="exam"))[0]
    with pytest.raises(PolicyViolation):  # different appointment type
        await _reschedule(session, jane, view.id, exam_slot)

    soon = await insert_appointment(session, jane.patient_id, datetime.now(UTC) + timedelta(hours=3))
    with pytest.raises(PolicyViolation):  # inside the late-change window
        await _reschedule(session, jane, soon.id, later)


# --------------------------------------------------------------------------- #
# the model-facing tools end to end (identity comes from ToolContext only)
# --------------------------------------------------------------------------- #
def _ctx(session: AsyncSession, person: Person) -> ToolContext:
    return ToolContext(
        session=session,
        user_id=person.user_id,
        patient_id=person.patient_id,
        conversation_id=person.conversation_id,
        tool_call_id="call_1",
    )


async def _call(session: AsyncSession, person: Person, name: str, args: dict) -> dict:
    return json.loads(await tool_registry.dispatch(name, json.dumps(args), _ctx(session, person)))


async def test_tool_flow_search_propose_confirm_list_cancel(session: AsyncSession, jane: Person) -> None:
    # A proposal belongs to a real conversation (FK), as it would in the chat flow.
    session.add(ChatConversation(id=jane.conversation_id, user_id=jane.user_id))
    await session.commit()
    day = next_monday().isoformat()

    types = await _call(session, jane, "get_appointment_types", {})
    assert any(t["code"] == "cleaning" for t in types["appointment_types"])

    found = await _call(
        session, jane, "get_available_slots",
        {"appointment_type": "cleaning", "start_date": day, "end_date": day},
    )
    assert found["status"] == "ok" and found["duration_minutes"] == 60
    slot_id = found["slots"][0]["slot_id"]

    # The model has no tool that books; proposing only creates a pending confirmation.
    assert "book_appointment" not in tool_registry.names
    proposed = await _call(session, jane, "propose_booking", {"slot_id": slot_id})
    assert proposed["status"] == "awaiting_patient_confirmation"
    assert await session.scalar(select(func.count()).select_from(Appointment)) == 0

    pending = await proposals.get_pending_proposal(
        session, user_id=jane.user_id, conversation_id=jane.conversation_id
    )
    assert pending is not None
    outcome = await proposals.resolve_proposal(
        session,
        user_id=jane.user_id,
        actor_patient_id=jane.patient_id,
        proposal_id=pending.id,
        action="confirm",
    )
    assert outcome.status == "confirmed"

    listed = await _call(session, jane, "get_patient_appointments", {})
    assert len(listed["appointments"]) == 1
    appointment_id = listed["appointments"][0]["appointment_id"]

    details = await _call(session, jane, "get_appointment_details", {"appointment_id": appointment_id})
    assert details["provider_name"] == proposed["provider_name"]

    status = await _call(session, jane, "get_booking_status", {"slot_id": slot_id})
    assert status["status"] == "booked"

    proposed_cancel = await _call(session, jane, "propose_cancellation", {"appointment_id": appointment_id})
    assert proposed_cancel["status"] == "awaiting_patient_confirmation"
    pending_cancel = await cancel_proposals.get_pending_cancellation(
        session, user_id=jane.user_id, conversation_id=jane.conversation_id
    )
    assert pending_cancel is not None
    cancelled = await cancel_proposals.resolve_cancellation(
        session,
        user_id=jane.user_id,
        actor_patient_id=jane.patient_id,
        proposal_id=pending_cancel.id,
        action="confirm",
    )
    assert cancelled.status == "confirmed"


async def test_tool_hides_other_patients_appointments(session: AsyncSession, jane: Person, bob: Person) -> None:
    view = await book(session, jane, (await offered(session))[0])

    out = await _call(session, bob, "get_appointment_details", {"appointment_id": str(view.id)})
    assert out["status"] == "not_found"

    out = await _call(session, bob, "propose_cancellation", {"appointment_id": str(view.id)})
    assert out["status"] == "not_found"

    out = await _call(session, bob, "get_patient_appointments", {"patient_id": str(jane.patient_id)})
    assert out["status"] == "not_authorized"
