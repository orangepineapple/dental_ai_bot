from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.auth import service, utils
from src.auth.config import auth_settings
from src.auth.exceptions import AuthRequired, InvalidToken, RefreshTokenNotValid
from src.auth.models import AuthUser
from src.auth.schemas import JWTData
from src.database import DbSession

_bearer_scheme = HTTPBearer(auto_error=False)


async def parse_jwt_data(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> JWTData:
    if credentials is None:
        raise AuthRequired()
    return utils.decode_access_token(credentials.credentials)


async def get_current_user(
    jwt_data: Annotated[JWTData, Depends(parse_jwt_data)],
    session: DbSession,
) -> AuthUser:
    # Re-checked on every request so a disabled account loses access immediately.
    user = await service.get_user_by_id(session, jwt_data.user_id)
    if user is None or not user.is_active:
        raise InvalidToken()
    return user


async def get_refresh_token_cookie(request: Request) -> str:
    refresh_token = request.cookies.get(auth_settings.REFRESH_TOKEN_KEY)
    if not refresh_token:
        raise RefreshTokenNotValid()
    return refresh_token


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
RefreshTokenCookie = Annotated[str, Depends(get_refresh_token_cookie)]
