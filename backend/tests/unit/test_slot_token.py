import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.appointments.exceptions import SlotUnavailable
from src.appointments.slot_token import SlotClaims, issue_slot_token, verify_slot_token

SECRET = "unit-test-secret-" + "x" * 32
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _claims(**overrides) -> SlotClaims:
    values = {
        "provider_id": uuid.uuid4(),
        "appointment_type": "cleaning",
        "starts_at": NOW + timedelta(days=3),
        "expires_at": NOW + timedelta(minutes=30),
    }
    return SlotClaims(**{**values, **overrides})


def test_round_trip() -> None:
    claims = _claims()

    decoded = verify_slot_token(SECRET, issue_slot_token(SECRET, claims), NOW)

    assert decoded == claims


def test_token_fits_tool_argument_limit() -> None:
    assert len(issue_slot_token(SECRET, _claims(appointment_type="x" * 64))) <= 512


def test_tampered_payload_is_rejected() -> None:
    token = issue_slot_token(SECRET, _claims())
    body, sig = token.split(".")
    forged = issue_slot_token(SECRET, _claims(appointment_type="exam")).split(".")[0]

    with pytest.raises(SlotUnavailable):
        verify_slot_token(SECRET, f"{forged}.{sig}", NOW)
    assert body != forged


def test_wrong_secret_is_rejected() -> None:
    token = issue_slot_token(SECRET, _claims())

    with pytest.raises(SlotUnavailable):
        verify_slot_token("another-secret-" + "y" * 32, token, NOW)


@pytest.mark.parametrize("garbage", ["", "abc", "a.b", "....", "not-base64!.sig"])
def test_garbage_is_rejected(garbage: str) -> None:
    with pytest.raises(SlotUnavailable):
        verify_slot_token(SECRET, garbage, NOW)


def test_expired_token_is_rejected_unless_allowed() -> None:
    token = issue_slot_token(SECRET, _claims())
    later = NOW + timedelta(hours=1)

    with pytest.raises(SlotUnavailable):
        verify_slot_token(SECRET, token, later)
    assert verify_slot_token(SECRET, token, later, allow_expired=True).appointment_type == "cleaning"
