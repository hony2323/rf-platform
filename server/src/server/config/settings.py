from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    db_path: str
    host: str
    port: int
    session_secret: str
    session_cookie_name: str
    session_cookie_secure: bool
    cors_origins: list[str]
    google_client_id: str | None


def load_settings() -> Settings:
    port_raw = os.getenv("RF_PORT", "8000")
    try:
        port = int(port_raw)
    except ValueError:
        raise ValueError(f"RF_PORT must be an integer, got {port_raw!r}") from None

    secure_raw = os.getenv("RF_SESSION_COOKIE_SECURE", "")
    if secure_raw and secure_raw.lower() not in ("0", "1", "true", "false", "yes", "no"):
        raise ValueError(
            f"RF_SESSION_COOKIE_SECURE must be 1/true/yes or 0/false/no, got {secure_raw!r}"
        )
    secure = secure_raw.lower() in ("1", "true", "yes")

    cors_raw = os.getenv("RF_CORS_ORIGINS", "")
    cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]

    google_client_id_raw = os.getenv("RF_GOOGLE_CLIENT_ID", "").strip()

    return Settings(
        db_path=os.getenv("RF_DB_PATH", "rf_platform.db"),
        host=os.getenv("RF_HOST", "0.0.0.0"),
        port=port,
        session_secret=os.getenv("RF_SESSION_SECRET", "dev-secret-change-in-production"),
        session_cookie_name=os.getenv("RF_SESSION_COOKIE_NAME", "session"),
        session_cookie_secure=secure,
        cors_origins=cors_origins,
        google_client_id=google_client_id_raw or None,
    )
