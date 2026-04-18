from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

_engine = None
_session_factory = None


class Base(DeclarativeBase):
    pass


def get_engine():
    if _engine is None:
        raise RuntimeError("Database not initialized — call init_db() at startup.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database not initialized — call init_db() at startup.")
    return _session_factory


async def init_db(db_path: str = "rf_platform.db") -> None:
    global _engine, _session_factory
    from server.storage import models  # noqa: F401 — registers ORM models

    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
