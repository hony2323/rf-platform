from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, Cookie
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.auth_config import SESSION_COOKIE_NAME, SESSION_COOKIE_SECURE, SESSION_SECRET
from server.app.deps import get_db, get_current_user
from server.auth.passwords import verify_password
from server.auth.browser_auth import make_session_cookie
from server.storage.repositories import users as users_repo
from server.storage.models import User

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str

    model_config = {"from_attributes": True}


@router.post("/auth/login")
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    user = await users_repo.get_user_by_email(db, body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    cookie = make_session_cookie(user.id, SESSION_SECRET)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie,
        httponly=True,
        samesite="lax",
        path="/",
        secure=SESSION_COOKIE_SECURE,
    )
    return UserResponse.model_validate(user)


@router.post("/auth/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
