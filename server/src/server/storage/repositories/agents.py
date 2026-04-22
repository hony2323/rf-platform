from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.storage.models import Agent


async def create_agent(db: AsyncSession, user_id: str, name: str, stable_node_id: str) -> Agent:
    agent = Agent(user_id=user_id, name=name, stable_node_id=stable_node_id)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


async def get_agent_by_id(db: AsyncSession, agent_id: str, user_id: str) -> Agent | None:
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id))
    return result.scalar_one_or_none()


async def get_agents_for_user(db: AsyncSession, user_id: str) -> list[Agent]:
    result = await db.execute(select(Agent).where(Agent.user_id == user_id))
    return list(result.scalars().all())


async def count_agents_for_user(db: AsyncSession, user_id: str) -> int:
    result = await db.execute(select(Agent.id).where(Agent.user_id == user_id))
    return len(result.scalars().all())


async def get_agent_by_id_unscoped(db: AsyncSession, agent_id: str) -> Agent | None:
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    return result.scalar_one_or_none()


async def get_agent_by_node_id(db: AsyncSession, stable_node_id: str) -> Agent | None:
    result = await db.execute(select(Agent).where(Agent.stable_node_id == stable_node_id))
    return result.scalar_one_or_none()


async def delete_agent(db: AsyncSession, agent_id: str, user_id: str) -> Agent | None:
    agent = await get_agent_by_id(db, agent_id, user_id)
    if agent is None:
        return None
    await db.delete(agent)
    await db.commit()
    return agent
