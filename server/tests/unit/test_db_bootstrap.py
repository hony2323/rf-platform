from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import server.storage.db as db_module


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


async def test_get_engine_raises_before_init():
    with pytest.raises(RuntimeError, match="init_db"):
        db_module.get_engine()


async def test_get_session_factory_raises_before_init():
    with pytest.raises(RuntimeError, match="init_db"):
        db_module.get_session_factory()


async def test_init_db_sets_globals():
    await db_module.init_db(":memory:")
    assert db_module._engine is not None
    assert db_module._session_factory is not None


async def test_session_factory_yields_async_session():
    await db_module.init_db(":memory:")
    factory = db_module.get_session_factory()
    async with factory() as session:
        assert isinstance(session, AsyncSession)


async def test_init_db_creates_tables():
    from sqlalchemy import text
    await db_module.init_db(":memory:")
    async with db_module.get_engine().connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        tables = {row[0] for row in result}
    assert {"users", "agents", "agent_tokens"} <= tables
