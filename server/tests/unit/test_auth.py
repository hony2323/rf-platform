from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

import server.storage.db as db_module
from server.app.api import create_app
from server.auth.passwords import hash_password
from server.storage import models  # noqa: F401
from server.storage.repositories import users as users_repo


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
async def registered_user(client: AsyncClient):
    engine = db_module._engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await users_repo.create_user(session, "alice@example.com", hash_password("secret123"))
    return "alice@example.com", "secret123"


@pytest.fixture
async def registered_user_secure(secure_client: AsyncClient):
    engine = db_module._engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await users_repo.create_user(session, "alice@example.com", hash_password("secret123"))
    return "alice@example.com", "secret123"


async def test_login_success(client: AsyncClient, registered_user):
    email, password = registered_user
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    assert resp.json()["email"] == email
    assert "session" in resp.cookies


async def test_signup_success_sets_cookie(client: AsyncClient):
    resp = await client.post(
        "/auth/signup",
        json={"email": "new@example.com", "password": "secret123"},
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "new@example.com"
    assert "session" in resp.cookies


async def test_signup_rejects_duplicate_email(client: AsyncClient, registered_user):
    email, _ = registered_user
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": "secret123"},
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
        json={"email": "signedup@example.com", "password": "secret123"},
    )
    resp = await client.get("/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "signedup@example.com"


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
    resp = await client.request("DELETE", "/me", json={"password": "secret123"})
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
        json={"email": "owner@example.com", "password": "secret123"},
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

    delete_resp = await client.request("DELETE", "/me", json={"password": "secret123"})
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
        json={"email": "secure@example.com", "password": "secret123"},
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
