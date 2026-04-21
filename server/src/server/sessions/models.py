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
    last_stream_config: dict | None = None
    last_config_version: int | None = None

    # Outbound frame queue — available for future buffering / replay
    frame_queue: asyncio.Queue = field(default_factory=asyncio.Queue)


_VIEWER_QUEUE_SIZE = 64


@dataclass
class ViewerSubscription:
    """In-memory state for a connected browser viewer."""

    subscription_id: str
    user_id: str
    agent_id: str
    session_id: str  # the LiveAgentSession being watched
    send_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=_VIEWER_QUEUE_SIZE)
    )
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    subscribed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
