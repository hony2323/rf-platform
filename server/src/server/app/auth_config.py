from __future__ import annotations

from server.config.settings import load_settings

_settings = load_settings()
SESSION_SECRET = _settings.session_secret
SESSION_COOKIE_NAME = _settings.session_cookie_name
SESSION_COOKIE_SECURE = _settings.session_cookie_secure
