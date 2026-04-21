"""Smoke tests for the bootstrap command and settings loading."""

from __future__ import annotations

import pytest

import server.storage.db as db_module
from server.app.bootstrap import _run
from server.config.settings import load_settings
from server.storage.db import get_session_factory
from server.storage.repositories.users import get_user_by_email


@pytest.fixture(autouse=True)
async def reset_db_globals():
    saved_engine = db_module._engine
    saved_factory = db_module._session_factory
    db_module._engine = None
    db_module._session_factory = None
    yield
    if db_module._engine is not None:
        await db_module._engine.dispose()
    db_module._engine = saved_engine
    db_module._session_factory = saved_factory


async def test_bootstrap_creates_user():
    await _run("admin@example.com", "secret", ":memory:")
    async with get_session_factory()() as session:
        user = await get_user_by_email(session, "admin@example.com")
    assert user is not None
    assert user.email == "admin@example.com"


async def test_bootstrap_duplicate_email_raises(capsys):
    await _run("admin@example.com", "secret", ":memory:")
    with pytest.raises(SystemExit) as exc_info:
        await _run("admin@example.com", "other", ":memory:")
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "already exists" in captured.err


async def test_bootstrap_hashes_password():
    await _run("admin@example.com", "secret", ":memory:")
    async with get_session_factory()() as session:
        user = await get_user_by_email(session, "admin@example.com")
    assert user.password_hash != "secret"
    assert len(user.password_hash) > 20


def test_settings_defaults():
    s = load_settings()
    assert s.db_path == "rf_platform.db"
    assert s.host == "0.0.0.0"
    assert s.port == 8000
    assert s.session_secret == "dev-secret-change-in-production"
    assert s.session_cookie_name == "session"
    assert s.session_cookie_secure is False


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("RF_DB_PATH", "/data/test.db")
    monkeypatch.setenv("RF_PORT", "9000")
    monkeypatch.setenv("RF_SESSION_SECRET", "prod-secret")
    monkeypatch.setenv("RF_SESSION_COOKIE_SECURE", "true")
    s = load_settings()
    assert s.db_path == "/data/test.db"
    assert s.port == 9000
    assert s.session_secret == "prod-secret"
    assert s.session_cookie_secure is True


def test_settings_invalid_port(monkeypatch):
    monkeypatch.setenv("RF_PORT", "not_a_number")
    with pytest.raises(ValueError, match="RF_PORT must be an integer"):
        load_settings()


def test_settings_invalid_cookie_secure(monkeypatch):
    monkeypatch.setenv("RF_SESSION_COOKIE_SECURE", "maybe")
    with pytest.raises(ValueError, match="RF_SESSION_COOKIE_SECURE"):
        load_settings()
