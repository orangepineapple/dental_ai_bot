from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    # Any OpenAI-compatible provider. Defaults target DeepSeek.
    # Unset key => the provider is treated as unavailable and the safe fallback is used.
    API_KEY: SecretStr | None = None
    BASE_URL: str = "http://localhost:11434/v1"
    # qwen model used in testing with a enlarged context window
    MODEL: str = "qwen3-32k"

    TEMPERATURE: float = Field(default=0.7, ge=0, le=2)
    # Reasoning models spend this budget on thinking before answering; too low => empty replies.
    MAX_OUTPUT_TOKENS: int = Field(default=2048, ge=64, le=8192)
    # Passed as reasoning_effort (Ollama: none/low/medium/high). None = provider default.
    REASONING_EFFORT: str | None = "none"
    REQUEST_TIMEOUT_SECONDS: float = Field(default=120, gt=0)

    # Agentic loop limits.
    MAX_TOOL_ROUNDS: int = Field(default=5, ge=0, le=10)
    MAX_TOOL_CALLS_PER_ROUND: int = Field(default=4, ge=1, le=8)
    TOOL_TIMEOUT_SECONDS: float = Field(default=15, gt=0)
    MAX_TOOL_OUTPUT_CHARS: int = Field(default=8_000, ge=500)

    MAX_INPUT_CHARS: int = Field(default=2_000, ge=1)
    # Hard cap on user + assistant messages in one conversation.
    MAX_CONVERSATION_MESSAGES: int = Field(default=100, ge=2)
    # How many stored messages are replayed to the model each turn.
    HISTORY_MESSAGE_LIMIT: int = Field(default=30, ge=1)

    # Dev only: print raw model output, reasoning and error details to stdout.
    # Output can contain patient text, so keep this off in production (LLM_DEBUG_PRINT).
    DEBUG_PRINT: bool = False

    # Used only to tell the model what "today" is.
    CLINIC_TIMEZONE: str = "America/New_York"


chat_settings = ChatConfig()
