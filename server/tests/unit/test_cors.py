"""Tests for CORS settings parsing and middleware behaviour."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from server.app.api import create_app
from server.config.settings import load_settings


# ---------------------------------------------------------------------------
# Settings parsing
# ---------------------------------------------------------------------------


def test_cors_origins_default_empty():
    s = load_settings()
    assert s.cors_origins == []


def test_cors_origins_single(monkeypatch):
    monkeypatch.setenv("RF_CORS_ORIGINS", "https://app.example.com")
    s = load_settings()
    assert s.cors_origins == ["https://app.example.com"]


def test_cors_origins_multiple(monkeypatch):
    monkeypatch.setenv(
        "RF_CORS_ORIGINS",
        "https://app.example.com,https://preview.vercel.app",
    )
    s = load_settings()
    assert s.cors_origins == ["https://app.example.com", "https://preview.vercel.app"]


def test_cors_origins_trims_whitespace(monkeypatch):
    monkeypatch.setenv(
        "RF_CORS_ORIGINS",
        " https://app.example.com , https://preview.vercel.app ",
    )
    s = load_settings()
    assert s.cors_origins == ["https://app.example.com", "https://preview.vercel.app"]


def test_cors_origins_ignores_empty_entries(monkeypatch):
    monkeypatch.setenv("RF_CORS_ORIGINS", "https://app.example.com,,")
    s = load_settings()
    assert s.cors_origins == ["https://app.example.com"]


# ---------------------------------------------------------------------------
# Middleware behaviour
# ---------------------------------------------------------------------------

ALLOWED_ORIGIN = "https://app.example.com"
OTHER_ORIGIN = "https://evil.example.com"


@pytest.fixture()
def cors_app(monkeypatch):
    monkeypatch.setenv("RF_CORS_ORIGINS", ALLOWED_ORIGIN)
    return create_app(db_path=":memory:")


@pytest.fixture()
def no_cors_app():
    return create_app(db_path=":memory:")


async def test_preflight_allowed_origin(cors_app):
    async with AsyncClient(transport=ASGITransport(app=cors_app), base_url="http://test") as client:
        r = await client.options(
            "/auth/login",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert r.headers.get("access-control-allow-credentials") == "true"


async def test_preflight_disallowed_origin(cors_app):
    async with AsyncClient(transport=ASGITransport(app=cors_app), base_url="http://test") as client:
        r = await client.options(
            "/auth/login",
            headers={
                "Origin": OTHER_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert "access-control-allow-origin" not in r.headers


async def test_no_cors_middleware_when_origins_empty(no_cors_app):
    async with AsyncClient(
        transport=ASGITransport(app=no_cors_app), base_url="http://test"
    ) as client:
        r = await client.options(
            "/auth/login",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
    assert "access-control-allow-origin" not in r.headers
