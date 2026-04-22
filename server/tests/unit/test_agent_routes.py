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
    app = create_app(":memory:")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _register_and_login(client: AsyncClient, email: str, password: str) -> None:
    engine = db_module._engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await users_repo.create_user(session, email, hash_password(password))
    await client.post("/auth/login", json={"email": email, "password": password})


@pytest.fixture
async def auth_client(client: AsyncClient):
    await _register_and_login(client, "alice@example.com", "secret")
    return client


@pytest.fixture
async def other_client(client: AsyncClient):
    """A second logged-in user sharing the same in-memory DB."""
    await _register_and_login(client, "bob@example.com", "secret")
    # bob has now overwritten alice's session cookie — return a fresh client for bob
    await db_module.init_db(":memory:")  # no-op; DB already initialized
    app = create_app(":memory:")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob:
        await _register_and_login(bob, "bob@example.com", "secret")
        yield bob


# --- Agent CRUD ---


async def test_list_agents_empty(auth_client: AsyncClient):
    resp = await auth_client.get("/agents")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_agent(auth_client: AsyncClient):
    resp = await auth_client.post("/agents", json={"name": "My Agent", "stable_node_id": "node_1"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Agent"
    assert data["stable_node_id"] == "node_1"
    assert "id" in data


async def test_delete_agent(auth_client: AsyncClient):
    created = (
        await auth_client.post("/agents", json={"name": "A", "stable_node_id": "delete_agent_1"})
    ).json()
    resp = await auth_client.request("DELETE", f"/agents/{created['id']}")
    assert resp.status_code == 204
    missing = await auth_client.get(f"/agents/{created['id']}")
    assert missing.status_code == 404


async def test_delete_agent_removes_tokens(auth_client: AsyncClient):
    agent = (
        await auth_client.post("/agents", json={"name": "A", "stable_node_id": "delete_agent_2"})
    ).json()
    token = (await auth_client.post(f"/agents/{agent['id']}/tokens", json={"label": "x"})).json()
    resp = await auth_client.request("DELETE", f"/agents/{agent['id']}")
    assert resp.status_code == 204
    tokens = await auth_client.get(f"/agents/{agent['id']}/tokens")
    assert tokens.status_code == 404
    # Token should also be inaccessible through its old owner-scoped path.
    deleted_token = await auth_client.request(
        "DELETE", f"/agents/{agent['id']}/tokens/{token['id']}"
    )
    assert deleted_token.status_code == 404


async def test_get_agent(auth_client: AsyncClient):
    created = (await auth_client.post("/agents", json={"name": "A", "stable_node_id": "n1"})).json()
    resp = await auth_client.get(f"/agents/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


async def test_get_agent_not_found(auth_client: AsyncClient):
    resp = await auth_client.get("/agents/nonexistent-id")
    assert resp.status_code == 404


async def test_list_agents_returns_own_only(auth_client: AsyncClient):
    await auth_client.post("/agents", json={"name": "A1", "stable_node_id": "n1"})
    await auth_client.post("/agents", json={"name": "A2", "stable_node_id": "n2"})
    resp = await auth_client.get("/agents")
    assert len(resp.json()) == 2


async def test_create_agent_rejects_sixth_agent(auth_client: AsyncClient):
    for i in range(5):
        resp = await auth_client.post(
            "/agents",
            json={"name": f"A{i}", "stable_node_id": f"max_agent_{i}"},
        )
        assert resp.status_code == 201

    resp = await auth_client.post(
        "/agents",
        json={"name": "A5", "stable_node_id": "max_agent_5"},
    )
    assert resp.status_code == 409


# --- Ownership isolation ---


async def test_agent_not_visible_to_other_user(client: AsyncClient):
    await _register_and_login(client, "alice@example.com", "pw")
    created = (await client.post("/agents", json={"name": "A", "stable_node_id": "n_a"})).json()

    app = create_app(":memory:")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob:
        await _register_and_login(bob, "bob@example.com", "pw")
        resp = await bob.get(f"/agents/{created['id']}")
        assert resp.status_code == 404


async def test_other_user_cannot_delete_agent(client: AsyncClient):
    await _register_and_login(client, "alice@example.com", "pw")
    created = (
        await client.post("/agents", json={"name": "A", "stable_node_id": "n_delete_iso"})
    ).json()

    app = create_app(":memory:")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob:
        await _register_and_login(bob, "bob@example.com", "pw")
        resp = await bob.request("DELETE", f"/agents/{created['id']}")
        assert resp.status_code == 404


# --- Token CRUD ---


async def test_create_token_returns_raw_once(auth_client: AsyncClient):
    agent = (await auth_client.post("/agents", json={"name": "A", "stable_node_id": "n1"})).json()
    resp = await auth_client.post(f"/agents/{agent['id']}/tokens", json={"label": "dev"})
    assert resp.status_code == 201
    data = resp.json()
    assert "token" in data
    assert len(data["token"]) == 64  # hex(32 bytes)
    assert data["label"] == "dev"


async def test_create_token_rejects_second_active_token(auth_client: AsyncClient):
    agent = (
        await auth_client.post("/agents", json={"name": "A", "stable_node_id": "n_token_limit"})
    ).json()
    first = await auth_client.post(f"/agents/{agent['id']}/tokens", json={"label": "first"})
    assert first.status_code == 201
    second = await auth_client.post(f"/agents/{agent['id']}/tokens", json={"label": "second"})
    assert second.status_code == 409


async def test_create_token_allowed_after_delete(auth_client: AsyncClient):
    agent = (
        await auth_client.post(
            "/agents", json={"name": "A", "stable_node_id": "n_token_after_delete"}
        )
    ).json()
    tok = (await auth_client.post(f"/agents/{agent['id']}/tokens", json={})).json()
    deleted = await auth_client.request("DELETE", f"/agents/{agent['id']}/tokens/{tok['id']}")
    assert deleted.status_code == 200
    recreated = await auth_client.post(f"/agents/{agent['id']}/tokens", json={"label": "new"})
    assert recreated.status_code == 201


async def test_create_token_allowed_after_revoke(auth_client: AsyncClient):
    agent = (
        await auth_client.post(
            "/agents", json={"name": "A", "stable_node_id": "n_token_after_revoke"}
        )
    ).json()
    tok = (await auth_client.post(f"/agents/{agent['id']}/tokens", json={})).json()
    revoked = await auth_client.post(f"/agents/{agent['id']}/tokens/{tok['id']}/revoke")
    assert revoked.status_code == 200
    recreated = await auth_client.post(f"/agents/{agent['id']}/tokens", json={"label": "new"})
    assert recreated.status_code == 201


async def test_list_tokens_excludes_revoked(auth_client: AsyncClient):
    agent = (await auth_client.post("/agents", json={"name": "A", "stable_node_id": "n2"})).json()
    t1 = (await auth_client.post(f"/agents/{agent['id']}/tokens", json={})).json()
    await auth_client.post(f"/agents/{agent['id']}/tokens/{t1['id']}/revoke")
    t2 = (await auth_client.post(f"/agents/{agent['id']}/tokens", json={})).json()

    resp = await auth_client.get(f"/agents/{agent['id']}/tokens")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert t1["id"] not in ids
    assert t2["id"] in ids


async def test_revoke_token(auth_client: AsyncClient):
    agent = (await auth_client.post("/agents", json={"name": "A", "stable_node_id": "n3"})).json()
    tok = (await auth_client.post(f"/agents/{agent['id']}/tokens", json={})).json()
    resp = await auth_client.post(f"/agents/{agent['id']}/tokens/{tok['id']}/revoke")
    assert resp.status_code == 200


async def test_delete_token(auth_client: AsyncClient):
    agent = (
        await auth_client.post("/agents", json={"name": "A", "stable_node_id": "n_delete_tok"})
    ).json()
    tok = (await auth_client.post(f"/agents/{agent['id']}/tokens", json={})).json()
    resp = await auth_client.request("DELETE", f"/agents/{agent['id']}/tokens/{tok['id']}")
    assert resp.status_code == 200
    listed = await auth_client.get(f"/agents/{agent['id']}/tokens")
    ids = [item["id"] for item in listed.json()]
    assert tok["id"] not in ids


async def test_revoke_already_revoked_returns_404(auth_client: AsyncClient):
    agent = (await auth_client.post("/agents", json={"name": "A", "stable_node_id": "n4"})).json()
    tok = (await auth_client.post(f"/agents/{agent['id']}/tokens", json={})).json()
    await auth_client.post(f"/agents/{agent['id']}/tokens/{tok['id']}/revoke")
    resp = await auth_client.post(f"/agents/{agent['id']}/tokens/{tok['id']}/revoke")
    assert resp.status_code == 404


async def test_token_on_other_users_agent_returns_404(client: AsyncClient):
    await _register_and_login(client, "alice@example.com", "pw")
    agent = (await client.post("/agents", json={"name": "A", "stable_node_id": "n_tok"})).json()

    app = create_app(":memory:")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob:
        await _register_and_login(bob, "bob@example.com", "pw")
        resp = await bob.post(f"/agents/{agent['id']}/tokens", json={})
        assert resp.status_code == 404


async def test_other_user_cannot_delete_token(client: AsyncClient):
    await _register_and_login(client, "alice@example.com", "pw")
    agent = (
        await client.post("/agents", json={"name": "A", "stable_node_id": "n_tok_delete_iso"})
    ).json()
    tok = (await client.post(f"/agents/{agent['id']}/tokens", json={})).json()

    app = create_app(":memory:")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bob:
        await _register_and_login(bob, "bob@example.com", "pw")
        resp = await bob.request("DELETE", f"/agents/{agent['id']}/tokens/{tok['id']}")
        assert resp.status_code == 404
