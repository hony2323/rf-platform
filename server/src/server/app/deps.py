from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.browser_auth import read_session_cookie
from server.storage.db import get_session_factory
from server.storage.models import User
from server.storage.repositories import users as users_repo


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    settings = request.app.state.settings
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id: str | None = read_session_cookie(cookie, settings.session_secret)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await users_repo.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
