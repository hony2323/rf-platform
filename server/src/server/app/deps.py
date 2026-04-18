from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.auth_config import SESSION_COOKIE_NAME, SESSION_SECRET
from server.auth.browser_auth import read_session_cookie
from server.storage.db import get_session_factory
from server.storage.models import User
from server.storage.repositories import users as users_repo


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session


async def get_current_user(
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> User:
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id: str | None = read_session_cookie(session, SESSION_SECRET)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await users_repo.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
