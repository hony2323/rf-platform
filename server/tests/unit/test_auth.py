from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

import server.storage.db as db_module
from server.app.api import create_app
from server.auth.google_auth import GoogleTokenPayload
from server.auth.passwords import hash_password, validate_password_strength
from server.storage import models  # noqa: F401
from server.storage.repositories import users as users_repo

# Strong password used across tests that exercise the signup endpoint.
STRONG_PW = "Secret1@23"


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


@pytest.fixture
async def client():
    await db_module.init_db(":memory:")
    app = create_app(":memory:")  # lifespan init_db is no-op since already initialized
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def secure_client(monkeypatch):
    monkeypatch.setenv("RF_SESSION_COOKIE_SECURE", "true")
    await db_module.init_db(":memory:")
    app = create_app(":memory:")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def google_client(monkeypatch):
    monkeypatch.setenv("RF_GOOGLE_CLIENT_ID", "test-google-client-id")
    await db_module.init_db(":memory:")
    app = create_app(":memory:")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def registered_user(client: AsyncClient):
    engine = db_module._engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await users_repo.create_user(session, "alice@example.com", hash_password(STRONG_PW))
    return "alice@example.com", STRONG_PW


@pytest.fixture
async def registered_user_secure(secure_client: AsyncClient):
    engine = db_module._engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await users_repo.create_user(session, "alice@example.com", hash_password(STRONG_PW))
    return "alice@example.com", STRONG_PW


# ---------------------------------------------------------------------------
# Email / password login
# ---------------------------------------------------------------------------


async def test_login_success(client: AsyncClient, registered_user):
    email, password = registered_user
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    assert resp.json()["email"] == email
    assert "session" in resp.cookies


async def test_signup_success_sets_cookie(client: AsyncClient):
    resp = await client.post(
        "/auth/signup",
        json={"email": "new@example.com", "password": STRONG_PW},
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "new@example.com"
    assert "session" in resp.cookies


async def test_signup_rejects_duplicate_email(client: AsyncClient, registered_user):
    email, _ = registered_user
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": STRONG_PW},
    )
    assert resp.status_code == 409


async def test_signup_duplicate_email_wins_over_password_validation(
    client: AsyncClient,
    registered_user,
):
    email, _ = registered_user
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": "short"},
    )
    assert resp.status_code == 409


async def test_signup_rejects_short_password_for_new_user(client: AsyncClient):
    resp = await client.post(
        "/auth/signup",
        json={"email": "fresh@example.com", "password": "short"},
    )
    assert resp.status_code == 422


async def test_signup_allows_immediate_me(client: AsyncClient):
    await client.post(
        "/auth/signup",
        json={"email": "signedup@example.com", "password": STRONG_PW},
    )
    resp = await client.get("/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "signedup@example.com"


async def test_signup_rejects_sixth_user(client: AsyncClient):
    for i in range(5):
        resp = await client.post(
            "/auth/signup",
            json={"email": f"user{i}@example.com", "password": STRONG_PW},
        )
        assert resp.status_code == 201
        await client.post("/auth/logout")

    resp = await client.post(
        "/auth/signup",
        json={"email": "user5@example.com", "password": STRONG_PW},
    )
    assert resp.status_code == 409


async def test_login_sets_cookie_attributes(client: AsyncClient, registered_user):
    email, password = registered_user
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie


async def test_login_wrong_password(client: AsyncClient, registered_user):
    email, _ = registered_user
    resp = await client.post("/auth/login", json={"email": email, "password": "wrong"})
    assert resp.status_code == 401


async def test_login_unknown_email(client: AsyncClient):
    resp = await client.post("/auth/login", json={"email": "nobody@x.com", "password": "x"})
    assert resp.status_code == 401


async def test_me_requires_auth(client: AsyncClient):
    resp = await client.get("/me")
    assert resp.status_code == 401


async def test_me_returns_current_user(client: AsyncClient, registered_user):
    email, password = registered_user
    await client.post("/auth/login", json={"email": email, "password": password})
    resp = await client.get("/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == email


async def test_logout_clears_cookie(client: AsyncClient, registered_user):
    email, password = registered_user
    await client.post("/auth/login", json={"email": email, "password": password})
    await client.post("/auth/logout")
    resp = await client.get("/me")
    assert resp.status_code == 401


async def test_logout_deletes_cookie_with_path(client: AsyncClient, registered_user):
    email, password = registered_user
    await client.post("/auth/login", json={"email": email, "password": password})
    resp = await client.post("/auth/logout")
    set_cookie = resp.headers["set-cookie"].lower()
    assert "path=/" in set_cookie
    assert "samesite=lax" in set_cookie


async def test_delete_account_requires_auth(client: AsyncClient):
    resp = await client.request("DELETE", "/me", json={"password": STRONG_PW})
    assert resp.status_code == 401


async def test_delete_account_requires_correct_password(client: AsyncClient, registered_user):
    email, password = registered_user
    await client.post("/auth/login", json={"email": email, "password": password})
    resp = await client.request("DELETE", "/me", json={"password": "wrong"})
    assert resp.status_code == 401


async def test_delete_account_removes_user_and_clears_cookie(client: AsyncClient, registered_user):
    email, password = registered_user
    await client.post("/auth/login", json={"email": email, "password": password})

    resp = await client.request("DELETE", "/me", json={"password": password})

    assert resp.status_code == 204
    me_resp = await client.get("/me")
    assert me_resp.status_code == 401


async def test_delete_account_cascades_owned_agents_and_tokens(client: AsyncClient):
    signup = await client.post(
        "/auth/signup",
        json={"email": "owner@example.com", "password": STRONG_PW},
    )
    assert signup.status_code == 201

    created_agent = await client.post(
        "/agents",
        json={"name": "Owned Agent", "stable_node_id": "node_delete_me"},
    )
    agent_id = created_agent.json()["id"]

    created_token = await client.post(
        f"/agents/{agent_id}/tokens",
        json={"label": "temporary"},
    )
    assert created_token.status_code == 201

    delete_resp = await client.request("DELETE", "/me", json={"password": STRONG_PW})
    assert delete_resp.status_code == 204

    agents_resp = await client.get("/agents")
    assert agents_resp.status_code == 401


# --- secure=True (production) mode ---


async def test_login_sets_secure_none_cookie(secure_client: AsyncClient, registered_user_secure):
    email, password = registered_user_secure
    resp = await secure_client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=none" in set_cookie
    assert "secure" in set_cookie
    assert "path=/" in set_cookie


async def test_signup_sets_secure_none_cookie(secure_client: AsyncClient):
    resp = await secure_client.post(
        "/auth/signup",
        json={"email": "secure@example.com", "password": STRONG_PW},
    )
    assert resp.status_code == 201
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=none" in set_cookie
    assert "secure" in set_cookie
    assert "path=/" in set_cookie


async def test_logout_deletes_secure_cookie(secure_client: AsyncClient, registered_user_secure):
    email, password = registered_user_secure
    await secure_client.post("/auth/login", json={"email": email, "password": password})
    resp = await secure_client.post("/auth/logout")
    set_cookie = resp.headers["set-cookie"].lower()
    assert "path=/" in set_cookie
    assert "samesite=none" in set_cookie
    assert "secure" in set_cookie


async def test_delete_account_deletes_secure_cookie(
    secure_client: AsyncClient, registered_user_secure
):
    email, password = registered_user_secure
    login_resp = await secure_client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    session_cookie = login_resp.cookies.get("session")
    assert session_cookie is not None
    resp = await secure_client.request(
        "DELETE",
        "/me",
        json={"password": password},
        headers={"cookie": f"session={session_cookie}"},
    )
    assert resp.status_code == 204
    set_cookie = resp.headers["set-cookie"].lower()
    assert "path=/" in set_cookie
    assert "samesite=none" in set_cookie
    assert "secure" in set_cookie


async def test_tampered_cookie_rejected(client: AsyncClient):
    client.cookies.set("session", "tampered.garbage.value")
    resp = await client.get("/me")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Password strength validation (unit)
# ---------------------------------------------------------------------------


def test_password_strength_accepts_valid():
    assert validate_password_strength("Secret1@23") is None


def test_password_strength_rejects_short():
    assert validate_password_strength("Sec1@ab") is not None  # 7 chars


def test_password_strength_rejects_no_uppercase():
    assert validate_password_strength("secret1@23") is not None


def test_password_strength_rejects_no_lowercase():
    assert validate_password_strength("SECRET1@23") is not None


def test_password_strength_rejects_no_digit():
    assert validate_password_strength("SecretPass!") is not None


def test_password_strength_rejects_no_symbol():
    assert validate_password_strength("Secret12345") is not None  # 11 chars, no symbol


async def test_signup_rejects_no_uppercase(client: AsyncClient):
    resp = await client.post(
        "/auth/signup",
        json={"email": "u@example.com", "password": "secret1@23"},
    )
    assert resp.status_code == 422


async def test_signup_rejects_no_digit(client: AsyncClient):
    resp = await client.post(
        "/auth/signup",
        json={"email": "u@example.com", "password": "SecretPass!"},
    )
    assert resp.status_code == 422


async def test_signup_rejects_no_symbol(client: AsyncClient):
    resp = await client.post(
        "/auth/signup",
        json={"email": "u@example.com", "password": "SecretPass1"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Google login
# ---------------------------------------------------------------------------

_GOOGLE_PAYLOAD = GoogleTokenPayload(
    sub="google-sub-abc123",
    email="google@example.com",
    email_verified=True,
)


async def test_google_login_disabled_without_client_id(client: AsyncClient):
    resp = await client.post("/auth/google", json={"token": "tok"})
    assert resp.status_code == 501


async def test_google_login_rejects_invalid_token(google_client: AsyncClient):
    with patch("server.app.http_routes.verify_google_token", side_effect=ValueError("bad token")):
        resp = await google_client.post("/auth/google", json={"token": "bad"})
    assert resp.status_code == 401
    assert "bad token" in resp.json()["detail"]


async def test_google_login_rejects_unverified_email(google_client: AsyncClient):
    with patch(
        "server.app.http_routes.verify_google_token",
        side_effect=ValueError("Google account email is not verified"),
    ):
        resp = await google_client.post("/auth/google", json={"token": "tok"})
    assert resp.status_code == 401


async def test_google_login_creates_new_user(google_client: AsyncClient):
    with patch("server.app.http_routes.verify_google_token", return_value=_GOOGLE_PAYLOAD):
        resp = await google_client.post("/auth/google", json={"token": "tok"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "google@example.com"
    assert "session" in resp.cookies


async def test_google_login_reuses_user_on_second_login(google_client: AsyncClient):
    with patch("server.app.http_routes.verify_google_token", return_value=_GOOGLE_PAYLOAD):
        r1 = await google_client.post("/auth/google", json={"token": "tok"})
        r2 = await google_client.post("/auth/google", json={"token": "tok"})
    assert r1.json()["id"] == r2.json()["id"]


async def test_google_login_rejects_existing_email_user(google_client: AsyncClient):
    # A pre-existing password account with the same email must not be auto-linked.
    engine = db_module._engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await users_repo.create_user(session, "google@example.com", hash_password(STRONG_PW))

    with patch("server.app.http_routes.verify_google_token", return_value=_GOOGLE_PAYLOAD):
        resp = await google_client.post("/auth/google", json={"token": "tok"})

    assert resp.status_code == 409
    assert "password" in resp.json()["detail"].lower()


async def test_me_works_after_google_login(google_client: AsyncClient):
    with patch("server.app.http_routes.verify_google_token", return_value=_GOOGLE_PAYLOAD):
        await google_client.post("/auth/google", json={"token": "tok"})
    resp = await google_client.get("/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "google@example.com"


async def test_google_login_respects_user_cap(google_client: AsyncClient):
    engine = db_module._engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for i in range(5):
            await users_repo.create_user(session, f"cap{i}@example.com", hash_password(STRONG_PW))

    with patch("server.app.http_routes.verify_google_token", return_value=_GOOGLE_PAYLOAD):
        resp = await google_client.post("/auth/google", json={"token": "tok"})
    assert resp.status_code == 409


async def test_delete_google_only_account_success(google_client: AsyncClient):
    with patch("server.app.http_routes.verify_google_token", return_value=_GOOGLE_PAYLOAD):
        await google_client.post("/auth/google", json={"token": "tok"})
    resp = await google_client.request("DELETE", "/me", json={})
    assert resp.status_code == 204
    assert (await google_client.get("/me")).status_code == 401


async def test_delete_account_requires_password_for_password_users(
    client: AsyncClient, registered_user
):
    email, password = registered_user
    await client.post("/auth/login", json={"email": email, "password": password})
    resp = await client.request("DELETE", "/me", json={})  # no password field
    assert resp.status_code == 422
