from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.deps import get_current_user, get_db
from server.auth.browser_auth import make_session_cookie
from server.auth.passwords import hash_password, verify_password
from server.storage.models import User
from server.storage.repositories import agents as agents_repo
from server.storage.repositories import users as users_repo

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    email: str
    password: str


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1)


class UserResponse(BaseModel):
    id: str
    email: str

    model_config = {"from_attributes": True}


def _set_session_cookie(request: Request, response: Response, user_id: str) -> None:
    settings = request.app.state.settings
    cookie = make_session_cookie(user_id, settings.session_secret)
    secure = settings.session_cookie_secure
    response.set_cookie(
        settings.session_cookie_name,
        cookie,
        httponly=True,
        samesite="none" if secure else "lax",
        path="/",
        secure=secure,
    )


def _clear_session_cookie(request: Request, response: Response) -> None:
    settings = request.app.state.settings
    secure = settings.session_cookie_secure
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=secure,
        httponly=True,
        samesite="none" if secure else "lax",
    )


@router.post("/auth/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    user = await users_repo.get_user_by_email(db, body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _set_session_cookie(request, response, user.id)
    return UserResponse.model_validate(user)


@router.post("/auth/signup", response_model=UserResponse, status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    existing = await users_repo.get_user_by_email(db, body.email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already registered")
    if len(body.email.strip()) < 3:
        raise HTTPException(status_code=422, detail="Email must be at least 3 characters")
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")
    user = await users_repo.create_user(db, body.email, hash_password(body.password))
    _set_session_cookie(request, response, user.id)
    return UserResponse.model_validate(user)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response):
    _clear_session_cookie(request, response)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@router.delete("/me", status_code=204)
async def delete_account(
    body: DeleteAccountRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    registry = getattr(request.app.state, "registry", None)
    owned_agents = await agents_repo.get_agents_for_user(db, current_user.id)
    if registry is not None:
        for agent in owned_agents:
            live_session = registry.get_session_by_agent(agent.id)
            if live_session is not None:
                registry.remove_session(live_session.session_id)

    deleted = await users_repo.delete_user(db, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    _clear_session_cookie(request, response)
