from datetime import timedelta
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_", env_file=".env", extra="ignore")

    JWT_SECRET: str = Field(min_length=32)
    # HMAC only; asymmetric algs would need key handling, and "none" must never be accepted.
    JWT_ALG: Literal["HS256", "HS384", "HS512"] = "HS256"
    JWT_EXP_MINUTES: int = Field(default=15, ge=1, le=60)

    REFRESH_TOKEN_KEY: str = "refresh_token"
    REFRESH_TOKEN_EXP: timedelta = timedelta(days=7)

    SECURE_COOKIES: bool = True
    COOKIE_SAMESITE: Literal["lax", "strict"] = "lax"


auth_settings = AuthConfig()
