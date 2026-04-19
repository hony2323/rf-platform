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


def load_settings() -> Settings:
    return Settings(
        db_path=os.getenv("RF_DB_PATH", "rf_platform.db"),
        host=os.getenv("RF_HOST", "0.0.0.0"),
        port=int(os.getenv("RF_PORT", "8000")),
        session_secret=os.getenv("RF_SESSION_SECRET", "dev-secret-change-in-production"),
        session_cookie_name=os.getenv("RF_SESSION_COOKIE_NAME", "session"),
        session_cookie_secure=os.getenv("RF_SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
    )
