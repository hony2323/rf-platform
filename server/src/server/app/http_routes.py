from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.deps import get_current_user, get_db
from server.auth.browser_auth import make_session_cookie
from server.auth.passwords import verify_password
from server.storage.models import User
from server.storage.repositories import users as users_repo

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str

    model_config = {"from_attributes": True}


@router.post("/auth/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    settings = request.app.state.settings
    user = await users_repo.get_user_by_email(db, body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    cookie = make_session_cookie(user.id, settings.session_secret)
    response.set_cookie(
        settings.session_cookie_name,
        cookie,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.session_cookie_secure,
    )
    return UserResponse.model_validate(user)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response):
    settings = request.app.state.settings
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
