import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import ValidationError

from src.auth.config import auth_settings
from src.auth.constants import ACCESS_TOKEN_TYPE, REFRESH_TOKEN_BYTES, REFRESH_TOKEN_COOKIE_PATH
from src.auth.exceptions import InvalidToken
from src.auth.schemas import AccessTokenResponse, JWTData

_password_hasher = PasswordHasher()
# Verified against when the email is unknown, so login timing does not reveal
# whether an account exists.
_DUMMY_PASSWORD_HASH = _password_hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def verify_dummy_password(password: str) -> None:
    verify_password(password, _DUMMY_PASSWORD_HASH)


def password_needs_rehash(password_hash: str) -> bool:
    return _password_hasher.check_needs_rehash(password_hash)


def create_access_token(user_id: uuid.UUID, *, now: datetime | None = None) -> str:
    issued_at = now or datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=auth_settings.JWT_EXP_MINUTES),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, auth_settings.JWT_SECRET, algorithm=auth_settings.JWT_ALG)


def decode_access_token(token: str) -> JWTData:
    try:
        payload = jwt.decode(
            token,
            auth_settings.JWT_SECRET,
            algorithms=[auth_settings.JWT_ALG],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidToken() from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidToken()

    try:
        return JWTData.model_validate(payload)
    except ValidationError as exc:
        raise InvalidToken() from exc


def build_access_token_response(user_id: uuid.UUID) -> AccessTokenResponse:
    return AccessTokenResponse(
        access_token=create_access_token(user_id),
        expires_in=auth_settings.JWT_EXP_MINUTES * 60,
    )


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _base_cookie_settings() -> dict[str, Any]:
    return {
        "key": auth_settings.REFRESH_TOKEN_KEY,
        "path": REFRESH_TOKEN_COOKIE_PATH,
        "httponly": True,
        "secure": auth_settings.SECURE_COOKIES,
        "samesite": auth_settings.COOKIE_SAMESITE,
    }


def get_refresh_token_cookie_settings(refresh_token: str) -> dict[str, Any]:
    return {
        **_base_cookie_settings(),
        "value": refresh_token,
        "max_age": int(auth_settings.REFRESH_TOKEN_EXP.total_seconds()),
    }


def get_delete_refresh_token_cookie_settings() -> dict[str, Any]:
    return _base_cookie_settings()
