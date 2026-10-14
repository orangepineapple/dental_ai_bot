from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.auth.config import auth_settings


class AppointmentConfig(BaseSettings):
    """Scheduling policy. The defaults are conservative placeholders: the clinic must confirm them."""

    model_config = SettingsConfigDict(env_prefix="APPT_", env_file=".env", extra="ignore")

    CLINIC_TIMEZONE: str = "America/New_York"

    # Granularity of offered start times within provider working hours.
    SLOT_INTERVAL_MINUTES: int = Field(default=15, ge=5, le=120)
    MAX_SLOTS_RETURNED: int = Field(default=20, ge=1, le=100)
    # Offered slots expire so a stale slot_id cannot be booked much later.
    SLOT_TOKEN_TTL_MINUTES: int = Field(default=30, ge=1, le=24 * 60)
    # How long a confirmation card stays actionable.
    PROPOSAL_TTL_MINUTES: int = Field(default=15, ge=1, le=24 * 60)
    # HMAC key for slot tokens. Falls back to the auth secret (domain-separated).
    SLOT_TOKEN_SECRET: str | None = Field(default=None, min_length=32)

    # --- Clinic policy (CONFIRM WITH THE CLINIC) -------------------------------------
    MIN_NOTICE_MINUTES: int = Field(default=60, ge=0)
    MAX_ADVANCE_DAYS: int = Field(default=180, ge=1)
    # Changes inside this window before the start time are "late".
    LATE_CHANGE_WINDOW_HOURS: int = Field(default=24, ge=0)
    # Rescheduling inside the window is always refused (patient is sent to staff).
    # Cancelling inside it: "requires_staff" (default, no decision made by the bot) or
    # "allow" (cancel and relay LATE_CANCEL_POLICY_NOTE).
    LATE_CANCEL_ACTION: Literal["requires_staff", "allow"] = "requires_staff"
    # Clinic-approved wording only. Never invented by the model or by us.
    LATE_CANCEL_POLICY_NOTE: str | None = None
    # None = unlimited.
    MAX_ACTIVE_APPOINTMENTS_PER_PATIENT: int | None = Field(default=None, ge=1)

    @property
    def slot_secret(self) -> str:
        return self.SLOT_TOKEN_SECRET or auth_settings.JWT_SECRET


appointment_settings = AppointmentConfig()
