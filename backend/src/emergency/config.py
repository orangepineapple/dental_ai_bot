from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmergencyConfig(BaseSettings):
    """Emergency escalation settings. The clinic must supply the real channel and wording."""

    model_config = SettingsConfigDict(env_prefix="EMERGENCY_", env_file=".env", extra="ignore")

    # DUMMY endpoint: point this at the monitored staff-notification service when it exists.
    # Until something answers 2xx here, notifications are recorded as FAILED, never as delivered.
    STAFF_WEBHOOK_URL: str = "http://localhost:9000/staff-notifications"
    STAFF_WEBHOOK_TOKEN: str | None = None
    STAFF_WEBHOOK_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0, le=10)

    # PLACEHOLDER wording: the clinic must provide and approve the real text.
    INSTRUCTIONS: str = (
        "If this is a medical emergency or you have trouble breathing or swallowing, call your "
        "local emergency number now. For urgent dental problems, call the clinic directly."
    )


emergency_settings = EmergencyConfig()
