from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoogleTokenPayload:
    sub: str
    email: str
    email_verified: bool


def verify_google_token(token: str, client_id: str) -> GoogleTokenPayload:
    """Verify a Google ID token and return its claims.

    Raises ValueError on invalid/expired token or unverified email.
    Callers should catch ValueError and return HTTP 401.
    """
    try:
        from google.auth.transport import requests as _requests
        from google.oauth2 import id_token as _id_token

        idinfo = _id_token.verify_oauth2_token(
            token,
            _requests.Request(),
            client_id,
        )
    except Exception as exc:
        raise ValueError(f"Invalid Google token: {exc}") from exc

    if not idinfo.get("email_verified"):
        raise ValueError("Google account email is not verified")

    return GoogleTokenPayload(
        sub=str(idinfo["sub"]),
        email=str(idinfo["email"]),
        email_verified=True,
    )
