from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    # Any OpenAI-compatible provider. Defaults target DeepSeek.
    # Unset key => the provider is treated as unavailable and the safe fallback is used.
    API_KEY: SecretStr | None = None
    BASE_URL: str = "http://localhost:11434/v1"
    # deepseek-chat supports tool calling; deepseek-reasoner does not.
    MODEL: str = "qwen3:4b"

    TEMPERATURE: float = Field(default=0.7, ge=0, le=2)
    MAX_OUTPUT_TOKENS: int = Field(default=700, ge=64, le=4096)
    REQUEST_TIMEOUT_SECONDS: float = Field(default=30, gt=0)

    # Agentic loop limits.
    MAX_TOOL_ROUNDS: int = Field(default=5, ge=0, le=10)
    MAX_TOOL_CALLS_PER_ROUND: int = Field(default=4, ge=1, le=8)
    TOOL_TIMEOUT_SECONDS: float = Field(default=15, gt=0)
    MAX_TOOL_OUTPUT_CHARS: int = Field(default=8_000, ge=500)

    MAX_INPUT_CHARS: int = Field(default=2_000, ge=1)
    # How many stored messages are replayed to the model each turn.
    HISTORY_MESSAGE_LIMIT: int = Field(default=30, ge=1)

    # Used only to tell the model what "today" is.
    CLINIC_TIMEZONE: str = "ET"


chat_settings = ChatConfig()
