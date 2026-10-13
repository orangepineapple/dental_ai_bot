import uuid
from typing import Annotated, Literal

from pydantic import ConfigDict, EmailStr, Field, field_validator

from src.auth.constants import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from src.models import CustomModel
from src.patients.schemas import PatientCreate, PatientResponse

# Passwords are deliberately not whitespace-stripped.
Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


def _normalize_email(value: str) -> str:
    return value.strip().lower()


class RegisterRequest(PatientCreate):
    """Body posted by the Next.js registration form."""

    email: EmailStr
    password: Password

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class LoginRequest(CustomModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    # Only bound the length on login; policy is enforced at registration.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return _normalize_email(value)


class AccessTokenResponse(CustomModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")


class UserResponse(CustomModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    patient: PatientResponse


class JWTData(CustomModel):
    user_id: uuid.UUID = Field(alias="sub")
