import uuid
from datetime import UTC, datetime

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth import utils
from src.auth.config import auth_settings
from src.auth.exceptions import EmailTaken, InvalidCredentials, RefreshTokenNotValid
from src.auth.models import AuthRefreshToken, AuthUser
from src.auth.schemas import LoginRequest, RegisterRequest
from src.patients import service as patients_service

_EMAIL_UNIQUE_CONSTRAINT = "auth_user_email_key"


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> AuthUser | None:
    result = await session.execute(
        select(AuthUser).options(selectinload(AuthUser.patient)).where(AuthUser.id == user_id)
    )
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> AuthUser | None:
    result = await session.execute(
        select(AuthUser).options(selectinload(AuthUser.patient)).where(AuthUser.email == email)
    )
    return result.scalar_one_or_none()


async def register_user(session: AsyncSession, data: RegisterRequest) -> AuthUser:
    """Create the login account and the account holder's patient record atomically."""
    if await get_user_by_email(session, data.email) is not None:
        raise EmailTaken()

    # Argon2 is deliberately slow; keep it off the event loop.
    password_hash = await run_in_threadpool(utils.hash_password, data.password)
    user = AuthUser(
        email=data.email,
        password_hash=password_hash,
        patient=patients_service.new_patient(data),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # A concurrent registration with the same email won the race.
        if _EMAIL_UNIQUE_CONSTRAINT in str(exc.orig):
            raise EmailTaken() from exc
        raise
    return user


async def authenticate_user(session: AsyncSession, data: LoginRequest) -> AuthUser:
    """Return the user for valid credentials. Every failure raises the same error."""
    user = await get_user_by_email(session, data.email)
    if user is None:
        await run_in_threadpool(utils.verify_dummy_password, data.password)
        raise InvalidCredentials()

    if not await run_in_threadpool(utils.verify_password, data.password, user.password_hash):
        raise InvalidCredentials()
    if not user.is_active:
        raise InvalidCredentials()

    if utils.password_needs_rehash(user.password_hash):
        user.password_hash = await run_in_threadpool(utils.hash_password, data.password)
    user.last_login_at = datetime.now(UTC)
    return user


def _add_refresh_token(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> str:
    raw_token = utils.generate_refresh_token()
    session.add(
        AuthRefreshToken(
            user_id=user_id,
            token_hash=utils.hash_refresh_token(raw_token),
            expires_at=now + auth_settings.REFRESH_TOKEN_EXP,
        )
    )
    return raw_token


async def issue_refresh_token(session: AsyncSession, user: AuthUser) -> str:
    """Persist a new refresh token (and any pending user changes) and return the raw value."""
    raw_token = _add_refresh_token(session, user.id, datetime.now(UTC))
    await session.commit()
    return raw_token


async def _revoke_all_refresh_tokens(
    session: AsyncSession, user_id: uuid.UUID, now: datetime
) -> None:
    await session.execute(
        update(AuthRefreshToken)
        .where(AuthRefreshToken.user_id == user_id, AuthRefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def rotate_refresh_token(session: AsyncSession, raw_token: str) -> tuple[AuthUser, str]:
    """Exchange a refresh token for a new one. Each refresh token is single-use."""
    result = await session.execute(
        select(AuthRefreshToken)
        .where(AuthRefreshToken.token_hash == utils.hash_refresh_token(raw_token))
        .with_for_update()
    )
    stored = result.scalar_one_or_none()
    if stored is None:
        raise RefreshTokenNotValid()

    now = datetime.now(UTC)
    if stored.revoked_at is not None:
        # A rotated token was presented again, which suggests it was stolen:
        # end every session for this user.
        await _revoke_all_refresh_tokens(session, stored.user_id, now)
        await session.commit()
        raise RefreshTokenNotValid()
    if stored.expires_at <= now:
        raise RefreshTokenNotValid()

    user = await get_user_by_id(session, stored.user_id)
    if user is None or not user.is_active:
        raise RefreshTokenNotValid()

    stored.revoked_at = now
    new_raw_token = _add_refresh_token(session, user.id, now)
    await session.commit()
    return user, new_raw_token


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    await session.execute(
        update(AuthRefreshToken)
        .where(
            AuthRefreshToken.token_hash == utils.hash_refresh_token(raw_token),
            AuthRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
