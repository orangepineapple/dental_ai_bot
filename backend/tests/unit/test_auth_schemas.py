from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from src.auth.schemas import LoginRequest, RegisterRequest


def _register_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "email": "Jane.Doe@Example.com",
        "password": "a-strong-password",
        "first_name": "Jane",
        "last_name": "Doe",
        "birth_date": "1990-04-01",
    }
    payload.update(overrides)
    return payload


def test_register_normalizes_email_to_lowercase() -> None:
    data = RegisterRequest.model_validate(_register_payload())

    assert data.email == "jane.doe@example.com"


def test_register_strips_names_but_not_password() -> None:
    data = RegisterRequest.model_validate(
        _register_payload(first_name="  Jane ", password="  spaced password  ")
    )

    assert data.first_name == "Jane"
    assert data.password == "  spaced password  "


def test_register_rejects_short_password() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(_register_payload(password="short"))


def test_register_rejects_overlong_password() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(_register_payload(password="x" * 129))


def test_register_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(_register_payload(email="not-an-email"))


def test_register_rejects_future_birth_date() -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(_register_payload(birth_date=tomorrow))


def test_register_rejects_blank_name() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(_register_payload(last_name="   "))


def test_register_rejects_invalid_phone_number() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(_register_payload(phone_number="call me maybe"))


def test_register_accepts_valid_phone_number() -> None:
    data = RegisterRequest.model_validate(_register_payload(phone_number="+1 (555) 123-4567"))

    assert data.phone_number == "+1 (555) 123-4567"


def test_register_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(_register_payload(is_active=False))


def test_login_normalizes_email_and_forbids_unknown_fields() -> None:
    data = LoginRequest.model_validate({"email": "A@B.COM", "password": "x"})
    assert data.email == "a@b.com"

    with pytest.raises(ValidationError):
        LoginRequest.model_validate({"email": "a@b.com", "password": "x", "role": "admin"})
