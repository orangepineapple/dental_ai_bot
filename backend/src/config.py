from enum import StrEnum

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_debug(self) -> bool:
        return self in {Environment.LOCAL, Environment.TESTING, Environment.STAGING}

    @property
    def is_deployed(self) -> bool:
        return self in {Environment.STAGING, Environment.PRODUCTION}


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: PostgresDsn
    ENVIRONMENT: Environment = Environment.LOCAL

    # The Next.js frontend origin(s). JSON list in env, e.g. '["http://localhost:3000"]'.
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    CORS_HEADERS: list[str] = ["Authorization", "Content-Type", "Idempotency-Key"]

    APP_VERSION: str = "0.1.0"


settings = Config()
