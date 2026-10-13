import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.auth.config import auth_settings
from src.auth.models import AuthRefreshToken, AuthUser
from src.patients.models import Patient

pytestmark = pytest.mark.integration

PASSWORD = "a-strong-password"


def _register_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "email": "jane@example.com",
        "password": PASSWORD,
        "first_name": "Jane",
        "last_name": "Doe",
        "birth_date": "1990-04-01",
        "phone_number": "+1 555 123 4567",
    }
    payload.update(overrides)
    return payload


async def _register(client: AsyncClient, **overrides: object) -> dict:
    resp = await client.post("/auth/register", json=_register_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(client: AsyncClient, email: str = "jane@example.com") -> tuple[str, str]:
    resp = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"], resp.cookies[auth_settings.REFRESH_TOKEN_KEY]


async def _refresh(client: AsyncClient, refresh_token: str):
    # Set the cookie explicitly so each call uses exactly the token under test.
    client.cookies.clear()
    client.cookies.set(auth_settings.REFRESH_TOKEN_KEY, refresh_token)
    return await client.post("/auth/refresh")


async def test_healthcheck(client: AsyncClient) -> None:
    resp = await client.get("/healthcheck")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_register_creates_user_and_patient(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    body = await _register(client, email="Jane@Example.com")

    assert body["email"] == "jane@example.com"
    assert body["patient"]["first_name"] == "Jane"
    assert "password" not in body and "password_hash" not in body

    async with session_factory() as session:
        user = (await session.execute(select(AuthUser))).scalar_one()
        assert user.password_hash != PASSWORD
        patient = await session.get(Patient, user.patient_id)
        assert patient is not None and patient.last_name == "Doe"


async def test_register_duplicate_email_is_case_insensitive(client: AsyncClient) -> None:
    await _register(client)

    resp = await client.post("/auth/register", json=_register_payload(email="JANE@example.com"))

    assert resp.status_code == 409


async def test_failed_duplicate_registration_creates_no_patient(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client)
    await client.post("/auth/register", json=_register_payload())

    async with session_factory() as session:
        assert (await session.execute(select(func.count()).select_from(Patient))).scalar() == 1


async def test_register_rejects_invalid_payload(client: AsyncClient) -> None:
    resp = await client.post("/auth/register", json=_register_payload(password="short"))

    assert resp.status_code == 422


async def test_login_returns_access_token_and_http_only_refresh_cookie(
    client: AsyncClient,
) -> None:
    await _register(client)

    resp = await client.post(
        "/auth/login", json={"email": "JANE@example.com", "password": PASSWORD}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == auth_settings.JWT_EXP_MINUTES * 60
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "path=/auth" in set_cookie


async def test_login_errors_do_not_reveal_whether_account_exists(client: AsyncClient) -> None:
    await _register(client)

    wrong_password = await client.post(
        "/auth/login", json={"email": "jane@example.com", "password": "wrong-password"}
    )
    unknown_email = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_inactive_user_cannot_log_in(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client)
    async with session_factory() as session:
        await session.execute(update(AuthUser).values(is_active=False))
        await session.commit()

    resp = await client.post(
        "/auth/login", json={"email": "jane@example.com", "password": PASSWORD}
    )

    assert resp.status_code == 401


async def test_me_returns_current_user(client: AsyncClient) -> None:
    registered = await _register(client)
    access_token, _ = await _login(client)

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert resp.status_code == 200
    assert resp.json() == registered


async def test_me_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/auth/me")

    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_me_rejects_invalid_token(client: AsyncClient) -> None:
    resp = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})

    assert resp.status_code == 401


async def test_me_rejects_token_of_deactivated_user(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client)
    access_token, _ = await _login(client)
    async with session_factory() as session:
        await session.execute(update(AuthUser).values(is_active=False))
        await session.commit()

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert resp.status_code == 401


async def test_refresh_rotates_token(client: AsyncClient) -> None:
    await _register(client)
    _, refresh_token = await _login(client)

    resp = await _refresh(client, refresh_token)

    assert resp.status_code == 200
    new_refresh_token = resp.cookies[auth_settings.REFRESH_TOKEN_KEY]
    assert new_refresh_token != refresh_token
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_without_cookie_is_rejected(client: AsyncClient) -> None:
    resp = await client.post("/auth/refresh")

    assert resp.status_code == 401


async def test_reusing_rotated_refresh_token_revokes_all_sessions(client: AsyncClient) -> None:
    await _register(client)
    _, original = await _login(client)
    rotated = (await _refresh(client, original)).cookies[auth_settings.REFRESH_TOKEN_KEY]

    reuse = await _refresh(client, original)
    after_reuse = await _refresh(client, rotated)

    assert reuse.status_code == 401
    assert after_reuse.status_code == 401


async def test_expired_refresh_token_is_rejected(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client)
    _, refresh_token = await _login(client)
    async with session_factory() as session:
        await session.execute(
            update(AuthRefreshToken).values(expires_at=func.now() - func.make_interval(0, 0, 0, 1))
        )
        await session.commit()

    resp = await _refresh(client, refresh_token)

    assert resp.status_code == 401


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    await _register(client)
    _, refresh_token = await _login(client)

    client.cookies.clear()
    client.cookies.set(auth_settings.REFRESH_TOKEN_KEY, refresh_token)
    logout = await client.post("/auth/logout")
    after_logout = await _refresh(client, refresh_token)

    assert logout.status_code == 204
    assert after_logout.status_code == 401


async def test_logout_without_cookie_is_idempotent(client: AsyncClient) -> None:
    resp = await client.post("/auth/logout")

    assert resp.status_code == 204


async def test_refresh_tokens_are_stored_hashed(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _register(client)
    _, refresh_token = await _login(client)

    async with session_factory() as session:
        stored = (await session.execute(select(AuthRefreshToken.token_hash))).scalar_one()

    assert stored != refresh_token
