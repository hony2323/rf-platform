from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.storage.models import AgentToken


async def create_token(
    db: AsyncSession, agent_id: str, token_hash: str, label: str | None = None
) -> AgentToken:
    token = AgentToken(agent_id=agent_id, token_hash=token_hash, label=label)
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return token


async def get_tokens_for_agent(
    db: AsyncSession, agent_id: str, include_revoked: bool = False
) -> list[AgentToken]:
    q = select(AgentToken).where(AgentToken.agent_id == agent_id)
    if not include_revoked:
        q = q.where(AgentToken.revoked_at.is_(None))
    result = await db.execute(q)
    return list(result.scalars().all())


async def count_tokens_for_agent(
    db: AsyncSession, agent_id: str, include_revoked: bool = False
) -> int:
    return len(await get_tokens_for_agent(db, agent_id, include_revoked=include_revoked))


async def get_token_by_id(db: AsyncSession, token_id: str, agent_id: str) -> AgentToken | None:
    result = await db.execute(
        select(AgentToken).where(AgentToken.id == token_id, AgentToken.agent_id == agent_id)
    )
    return result.scalar_one_or_none()


async def get_active_token_by_hash(db: AsyncSession, token_hash: str) -> AgentToken | None:
    result = await db.execute(
        select(AgentToken).where(
            AgentToken.token_hash == token_hash,
            AgentToken.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def revoke_token(db: AsyncSession, token_id: str, agent_id: str) -> AgentToken | None:
    token = await get_token_by_id(db, token_id, agent_id)
    if token is None or token.revoked_at is not None:
        return None
    token.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(token)
    return token


async def touch_last_used(db: AsyncSession, token_id: str) -> None:
    token = await db.get(AgentToken, token_id)
    if token:
        token.last_used_at = datetime.now(timezone.utc)
        await db.commit()


async def delete_token(db: AsyncSession, token_id: str, agent_id: str) -> AgentToken | None:
    token = await get_token_by_id(db, token_id, agent_id)
    if token is None:
        return None
    await db.delete(token)
    await db.commit()
    return token
