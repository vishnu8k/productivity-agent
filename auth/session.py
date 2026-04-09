import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request, status

from models.schemas import AuthenticatedUser

SESSION_USER_KEY = "user"
SESSION_CSRF_KEY = "csrf_token"
SESSION_CALENDAR_STATE_KEY = "calendar_oauth_state"


def get_session_cookie_secure() -> bool:
    env = os.getenv("APP_ENV", "development").lower()
    return env in {"production", "staging"}


def get_authenticated_user(request: Request) -> Optional[AuthenticatedUser]:
    raw_user = request.session.get(SESSION_USER_KEY)
    if not raw_user:
        return None
    try:
        return AuthenticatedUser(**raw_user)
    except Exception:
        request.session.pop(SESSION_USER_KEY, None)
        return None


def require_authenticated_user(request: Request) -> AuthenticatedUser:
    user = get_authenticated_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with Google to continue.",
        )
    return user


def set_authenticated_session(request: Request, user: AuthenticatedUser) -> str:
    csrf_token = secrets.token_urlsafe(32)
    request.session[SESSION_USER_KEY] = user.model_dump()
    request.session[SESSION_CSRF_KEY] = csrf_token
    return csrf_token


def clear_authenticated_session(request: Request) -> None:
    request.session.clear()


def get_csrf_token(request: Request) -> Optional[str]:
    return request.session.get(SESSION_CSRF_KEY)


def require_csrf(request: Request) -> None:
    expected = request.session.get(SESSION_CSRF_KEY)
    provided = request.headers.get("X-CSRF-Token")
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The request could not be verified. Refresh and try again.",
        )
