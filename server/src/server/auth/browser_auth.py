from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SALT = "browser-session"
_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_SALT)


def make_session_cookie(user_id: str, secret: str) -> str:
    return _serializer(secret).dumps(user_id)


def read_session_cookie(cookie: str, secret: str) -> str | None:
    try:
        return _serializer(secret).loads(cookie, max_age=_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
