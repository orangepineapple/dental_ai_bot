from fastapi import APIRouter, Request, Response, status

from src.auth import service, utils
from src.auth.config import auth_settings
from src.auth.constants import AUTH_ROUTE_PREFIX
from src.auth.dependencies import CurrentUser, RefreshTokenCookie
from src.auth.schemas import AccessTokenResponse, LoginRequest, RegisterRequest, UserResponse
from src.database import DbSession
from src.exceptions import ErrorResponse

router = APIRouter(prefix=AUTH_ROUTE_PREFIX, tags=["auth"])

_UNAUTHORIZED = {status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}}


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a patient account",
    description="Creates the login account and the account holder's patient record.",
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def register(data: RegisterRequest, session: DbSession) -> UserResponse:
    user = await service.register_user(session, data)
    return UserResponse.model_validate(user)


@router.post(
    "/login",
    summary="Log in",
    description=(
        "Returns a short-lived bearer access token and sets an httpOnly refresh-token cookie."
    ),
    responses=_UNAUTHORIZED,
)
async def login(data: LoginRequest, response: Response, session: DbSession) -> AccessTokenResponse:
    user = await service.authenticate_user(session, data)
    refresh_token = await service.issue_refresh_token(session, user)
    response.set_cookie(**utils.get_refresh_token_cookie_settings(refresh_token))
    return utils.build_access_token_response(user.id)


@router.post(
    "/refresh",
    summary="Refresh the access token",
    description="Rotates the refresh-token cookie and returns a new access token.",
    responses=_UNAUTHORIZED,
)
async def refresh(
    refresh_token: RefreshTokenCookie, response: Response, session: DbSession
) -> AccessTokenResponse:
    user, new_refresh_token = await service.rotate_refresh_token(session, refresh_token)
    response.set_cookie(**utils.get_refresh_token_cookie_settings(new_refresh_token))
    return utils.build_access_token_response(user.id)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out",
    description="Revokes the current refresh token and clears its cookie. Idempotent.",
)
async def logout(request: Request, session: DbSession) -> Response:
    refresh_token = request.cookies.get(auth_settings.REFRESH_TOKEN_KEY)
    if refresh_token:
        await service.revoke_refresh_token(session, refresh_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(**utils.get_delete_refresh_token_cookie_settings())
    return response


@router.get("/me", summary="Current user", responses=_UNAUTHORIZED)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
