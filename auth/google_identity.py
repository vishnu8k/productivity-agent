import os

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from models.schemas import AuthenticatedUser


class GoogleIdentityError(ValueError):
    """Raised when a Google identity credential cannot be trusted."""


def verify_google_credential(credential: str) -> AuthenticatedUser:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    if not client_id:
        raise GoogleIdentityError("Google login is not configured on the server.")

    try:
        id_info = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            client_id,
        )
    except Exception as exc:  # pragma: no cover - library-specific failures
        raise GoogleIdentityError("Google sign-in could not be verified.") from exc

    issuer = id_info.get("iss")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise GoogleIdentityError("Unexpected Google token issuer.")

    if not id_info.get("email_verified"):
        raise GoogleIdentityError("Google account email must be verified.")

    subject = id_info.get("sub")
    email = id_info.get("email")
    if not subject or not email:
        raise GoogleIdentityError("Google account information is incomplete.")

    return AuthenticatedUser(
        sub=subject,
        email=email,
        name=id_info.get("name"),
        picture=id_info.get("picture"),
        email_verified=bool(id_info.get("email_verified")),
    )
