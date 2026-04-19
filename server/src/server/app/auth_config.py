from __future__ import annotations

import os

SESSION_SECRET = os.getenv("RF_SESSION_SECRET", "dev-secret-change-in-production")
SESSION_COOKIE_NAME = os.getenv("RF_SESSION_COOKIE_NAME", "session")
SESSION_COOKIE_SECURE = os.getenv("RF_SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
