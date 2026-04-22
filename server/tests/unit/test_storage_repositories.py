from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.storage.db import Base
from server.storage import models  # noqa: F401
from server.storage.repositories import agents as agents_repo
from server.storage.repositories import agent_tokens as tokens_repo
from server.storage.repositories import users as users_repo


@pytest.fixture
async def db() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_create_and_get_user(db: AsyncSession):
    user = await users_repo.create_user(db, email="a@example.com", password_hash="hash1")
    assert user.id is not None
    assert user.email == "a@example.com"

    fetched = await users_repo.get_user_by_id(db, user.id)
    assert fetched is not None
    assert fetched.email == "a@example.com"


async def test_get_user_by_email(db: AsyncSession):
    await users_repo.create_user(db, email="b@example.com", password_hash="hash2")
    found = await users_repo.get_user_by_email(db, "b@example.com")
    assert found is not None
    assert found.email == "b@example.com"


async def test_get_user_by_email_missing(db: AsyncSession):
    result = await users_repo.get_user_by_email(db, "nobody@example.com")
    assert result is None


async def test_create_and_get_agent(db: AsyncSession):
    user = await users_repo.create_user(db, "c@example.com", "hash3")
    agent = await agents_repo.create_agent(db, user.id, name="My Agent", stable_node_id="node_abc")
    assert agent.id is not None
    assert agent.user_id == user.id

    fetched = await agents_repo.get_agent_by_id(db, agent.id, user.id)
    assert fetched is not None
    assert fetched.stable_node_id == "node_abc"


async def test_get_agent_ownership_isolation(db: AsyncSession):
    u1 = await users_repo.create_user(db, "u1@example.com", "h1")
    u2 = await users_repo.create_user(db, "u2@example.com", "h2")
    agent = await agents_repo.create_agent(db, u1.id, "Agent", "node_x")

    # u2 cannot see u1's agent
    result = await agents_repo.get_agent_by_id(db, agent.id, u2.id)
    assert result is None


async def test_get_agents_for_user(db: AsyncSession):
    u = await users_repo.create_user(db, "d@example.com", "h4")
    await agents_repo.create_agent(db, u.id, "A1", "node_1")
    await agents_repo.create_agent(db, u.id, "A2", "node_2")
    agents = await agents_repo.get_agents_for_user(db, u.id)
    assert len(agents) == 2


async def test_token_create_and_lookup(db: AsyncSession):
    u = await users_repo.create_user(db, "e@example.com", "h5")
    ag = await agents_repo.create_agent(db, u.id, "Ag", "node_3")
    tok = await tokens_repo.create_token(db, ag.id, token_hash="sha256_abc", label="dev")
    assert tok.id is not None
    assert tok.revoked_at is None

    found = await tokens_repo.get_active_token_by_hash(db, "sha256_abc")
    assert found is not None
    assert found.id == tok.id


async def test_token_revocation(db: AsyncSession):
    u = await users_repo.create_user(db, "f@example.com", "h6")
    ag = await agents_repo.create_agent(db, u.id, "Ag2", "node_4")
    tok = await tokens_repo.create_token(db, ag.id, "sha256_xyz")

    revoked = await tokens_repo.revoke_token(db, tok.id, ag.id)
    assert revoked is not None
    assert revoked.revoked_at is not None

    # Revoked token should not be found as active
    found = await tokens_repo.get_active_token_by_hash(db, "sha256_xyz")
    assert found is None


async def test_get_tokens_for_agent_excludes_revoked(db: AsyncSession):
    u = await users_repo.create_user(db, "g@example.com", "h7")
    ag = await agents_repo.create_agent(db, u.id, "Ag3", "node_5")
    t1 = await tokens_repo.create_token(db, ag.id, "hash_active")
    t2 = await tokens_repo.create_token(db, ag.id, "hash_revoked")
    await tokens_repo.revoke_token(db, t2.id, ag.id)

    active = await tokens_repo.get_tokens_for_agent(db, ag.id)
    assert len(active) == 1
    assert active[0].id == t1.id


async def test_delete_user_cascades_agents_and_tokens(db: AsyncSession):
    user = await users_repo.create_user(db, "cascade@example.com", "hash")
    agent = await agents_repo.create_agent(db, user.id, "Cascade Agent", "node_cascade")
    token = await tokens_repo.create_token(db, agent.id, "cascade_hash")

    deleted = await users_repo.delete_user(db, user.id)

    assert deleted is True
    assert await users_repo.get_user_by_id(db, user.id) is None
    assert await agents_repo.get_agent_by_id_unscoped(db, agent.id) is None
    assert await tokens_repo.get_active_token_by_hash(db, "cascade_hash") is None

    # Sanity check the old token row is gone entirely.
    assert await tokens_repo.get_token_by_id(db, token.id, agent.id) is None


async def test_delete_token_removes_row(db: AsyncSession):
    user = await users_repo.create_user(db, "token-delete@example.com", "hash")
    agent = await agents_repo.create_agent(db, user.id, "Token Agent", "node_token_delete")
    token = await tokens_repo.create_token(db, agent.id, "token_delete_hash")

    deleted = await tokens_repo.delete_token(db, token.id, agent.id)

    assert deleted is not None
    assert deleted.id == token.id
    assert await tokens_repo.get_token_by_id(db, token.id, agent.id) is None


async def test_delete_agent_cascades_tokens(db: AsyncSession):
    user = await users_repo.create_user(db, "agent-delete@example.com", "hash")
    agent = await agents_repo.create_agent(db, user.id, "Delete Me", "node_agent_delete")
    token = await tokens_repo.create_token(db, agent.id, "agent_delete_hash")

    deleted = await agents_repo.delete_agent(db, agent.id, user.id)

    assert deleted is not None
    assert deleted.id == agent.id
    assert await agents_repo.get_agent_by_id_unscoped(db, agent.id) is None
    assert await tokens_repo.get_token_by_id(db, token.id, agent.id) is None
