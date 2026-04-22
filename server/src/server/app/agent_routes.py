from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.deps import get_current_user, get_db
from server.storage.models import AgentToken, User
from server.storage.repositories import agent_tokens as tokens_repo
from server.storage.repositories import agents as agents_repo

router = APIRouter(prefix="/agents")


# --- Pydantic schemas ---


class AgentCreate(BaseModel):
    name: str
    stable_node_id: str


class AgentResponse(BaseModel):
    id: str
    name: str
    stable_node_id: str

    model_config = {"from_attributes": True}


class AgentStatusResponse(BaseModel):
    agent_id: str
    online: bool
    session_id: str | None = None
    last_heartbeat_at: str | None = None
    last_status: Any | None = None


class TokenCreate(BaseModel):
    label: str | None = None


class TokenResponse(BaseModel):
    id: str
    label: str | None
    created_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, token: AgentToken) -> TokenResponse:
        return cls(
            id=token.id,
            label=token.label,
            created_at=token.created_at.isoformat(),
        )


class TokenCreateResponse(TokenResponse):
    token: str  # raw token — returned once only


# --- Routes ---


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agents_repo.get_agents_for_user(db, current_user.id)


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    body: AgentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await agents_repo.create_agent(db, current_user.id, body.name, body.stable_node_id)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agents_repo.get_agent_by_id(db, agent_id, current_user.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agents_repo.delete_agent(db, agent_id, current_user.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    registry = getattr(request.app.state, "registry", None)
    if registry is not None:
        live_session = registry.get_session_by_agent(agent_id)
        if live_session is not None:
            registry.remove_session(live_session.session_id)


@router.get("/{agent_id}/status", response_model=AgentStatusResponse)
async def get_agent_status(
    agent_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agents_repo.get_agent_by_id(db, agent_id, current_user.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    session = request.app.state.registry.get_session_by_agent(agent_id)
    if session is None:
        return AgentStatusResponse(agent_id=agent_id, online=False)
    return AgentStatusResponse(
        agent_id=agent_id,
        online=True,
        session_id=session.session_id,
        last_heartbeat_at=session.last_heartbeat_at.isoformat(),
        last_status=json.loads(session.last_status) if session.last_status else None,
    )


@router.get("/{agent_id}/tokens", response_model=list[TokenResponse])
async def list_tokens(
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agents_repo.get_agent_by_id(db, agent_id, current_user.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    tokens = await tokens_repo.get_tokens_for_agent(db, agent_id)
    return [TokenResponse.from_orm(t) for t in tokens]


@router.post("/{agent_id}/tokens", response_model=TokenCreateResponse, status_code=201)
async def create_token(
    agent_id: str,
    body: TokenCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agents_repo.get_agent_by_id(db, agent_id, current_user.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    token = await tokens_repo.create_token(db, agent_id, token_hash, body.label)
    return TokenCreateResponse(
        id=token.id,
        label=token.label,
        created_at=token.created_at.isoformat(),
        token=raw,
    )


@router.post("/{agent_id}/tokens/{token_id}/revoke", response_model=TokenResponse)
async def revoke_token(
    agent_id: str,
    token_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agents_repo.get_agent_by_id(db, agent_id, current_user.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    token = await tokens_repo.revoke_token(db, token_id, agent_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found or already revoked")
    return TokenResponse.from_orm(token)


@router.delete("/{agent_id}/tokens/{token_id}", response_model=TokenResponse)
async def delete_token(
    agent_id: str,
    token_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent = await agents_repo.get_agent_by_id(db, agent_id, current_user.id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    token = await tokens_repo.delete_token(db, token_id, agent_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    return TokenResponse.from_orm(token)
