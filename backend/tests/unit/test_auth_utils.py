import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from src.auth import utils
from src.auth.config import auth_settings
from src.auth.constants import REFRESH_TOKEN_COOKIE_PATH
from src.auth.exceptions import InvalidToken


def test_hash_password_round_trip() -> None:
    password_hash = utils.hash_password("correct horse battery")

    assert password_hash != "correct horse battery"
    assert utils.verify_password("correct horse battery", password_hash)


def test_verify_password_rejects_wrong_password() -> None:
    password_hash = utils.hash_password("correct horse battery")

    assert not utils.verify_password("wrong password", password_hash)


def test_verify_password_returns_false_for_malformed_hash() -> None:
    assert not utils.verify_password("anything", "not-an-argon2-hash")


def test_hash_password_is_salted() -> None:
    assert utils.hash_password("same-password") != utils.hash_password("same-password")


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()

    token = utils.create_access_token(user_id)

    assert utils.decode_access_token(token).user_id == user_id


def test_expired_access_token_is_rejected() -> None:
    long_ago = datetime.now(UTC) - timedelta(minutes=auth_settings.JWT_EXP_MINUTES + 5)
    token = utils.create_access_token(uuid.uuid4(), now=long_ago)

    with pytest.raises(InvalidToken):
        utils.decode_access_token(token)


def test_access_token_with_wrong_signature_is_rejected() -> None:
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        "a-different-secret-that-is-long-enough-123",
        algorithm="HS256",
    )

    with pytest.raises(InvalidToken):
        utils.decode_access_token(token)


def test_unsigned_access_token_is_rejected() -> None:
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        key=None,
        algorithm="none",
    )

    with pytest.raises(InvalidToken):
        utils.decode_access_token(token)


def test_token_of_wrong_type_is_rejected() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "refresh",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        auth_settings.JWT_SECRET,
        algorithm=auth_settings.JWT_ALG,
    )

    with pytest.raises(InvalidToken):
        utils.decode_access_token(token)


def test_token_with_non_uuid_subject_is_rejected() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "not-a-uuid", "type": "access", "iat": now, "exp": now + timedelta(minutes=5)},
        auth_settings.JWT_SECRET,
        algorithm=auth_settings.JWT_ALG,
    )

    with pytest.raises(InvalidToken):
        utils.decode_access_token(token)


def test_garbage_token_is_rejected() -> None:
    with pytest.raises(InvalidToken):
        utils.decode_access_token("not.a.jwt")


def test_refresh_tokens_are_random_and_hashed_deterministically() -> None:
    first, second = utils.generate_refresh_token(), utils.generate_refresh_token()

    assert first != second
    assert utils.hash_refresh_token(first) == utils.hash_refresh_token(first)
    assert utils.hash_refresh_token(first) != first
    assert len(utils.hash_refresh_token(first)) == 64


def test_refresh_cookie_is_http_only_and_scoped_to_auth_routes() -> None:
    cookie = utils.get_refresh_token_cookie_settings("raw-token")

    assert cookie["httponly"] is True
    assert cookie["path"] == REFRESH_TOKEN_COOKIE_PATH
    assert cookie["value"] == "raw-token"
    assert cookie["max_age"] == int(auth_settings.REFRESH_TOKEN_EXP.total_seconds())


def test_access_token_response_reports_lifetime_in_seconds() -> None:
    response = utils.build_access_token_response(uuid.uuid4())

    assert response.token_type == "bearer"
    assert response.expires_in == auth_settings.JWT_EXP_MINUTES * 60
