AUTH_ROUTE_PREFIX = "/auth"

# The refresh cookie is only sent to auth endpoints, never to the rest of the API.
REFRESH_TOKEN_COOKIE_PATH = AUTH_ROUTE_PREFIX
REFRESH_TOKEN_BYTES = 48

ACCESS_TOKEN_TYPE = "access"

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class ErrorCode:
    AUTHENTICATION_REQUIRED = "Authentication required."
    INVALID_TOKEN = "Invalid or expired token."
    INVALID_CREDENTIALS = "Invalid email or password."
    REFRESH_TOKEN_NOT_VALID = "Refresh token is not valid."
    EMAIL_TAKEN = "An account with this email already exists."
