from __future__ import annotations

from sqlalchemy import text
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


async def _migrate(conn) -> None:
    """Idempotently add columns introduced after initial schema creation."""
    result = await conn.execute(text("PRAGMA table_info(users)"))
    columns = {row[1] for row in result.fetchall()}
    if "google_sub" not in columns:
        await conn.execute(text("ALTER TABLE users ADD COLUMN google_sub TEXT"))
        await conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_sub ON users (google_sub)")
        )


async def init_db(db_path: str = "rf_platform.db") -> None:
    global _engine, _session_factory
    if _engine is not None:
        return

    from server.storage import models  # noqa: F401 — registers ORM models

    _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)
