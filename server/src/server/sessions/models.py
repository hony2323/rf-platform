from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class LiveAgentSession:
    """In-memory state for a connected agent."""

    session_id: str
    agent_id: str
    user_id: str
    stream_id: str
    config_version: int
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_status: str | None = None

    bin_count: int = 0

    # Outbound frame queue — viewers drain this via fanout
    frame_queue: asyncio.Queue = field(default_factory=asyncio.Queue)


@dataclass
class ViewerSubscription:
    """In-memory state for a connected browser viewer."""

    subscription_id: str
    user_id: str
    agent_id: str
    session_id: str  # the LiveAgentSession being watched
    send_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    subscribed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
