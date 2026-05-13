from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone


_AGENT_SEND_QUEUE_SIZE = 16


@dataclass
class PendingConfigRequest:
    """In-flight viewer→agent config change tracked by the server.

    The server generates `server_request_id` (used on the wire to the agent)
    and stores `viewer_request_id` (echoed back to the originating viewer in
    a `request_config_error` if anything fails).
    """

    server_request_id: str
    viewer_request_id: str
    subscription_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LiveAgentSession:
    """In-memory state for a connected agent."""

    session_id: str
    agent_id: str
    user_id: str
    stream_id: str
    config_version: int
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_status: str | None = None

    bin_count: int = 0
    last_stream_config: dict | None = None
    last_config_version: int | None = None
    wire_encoding: str = "json_base64"

    # v0.5: outbound queue for control messages from server → agent (config_request).
    agent_send_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=_AGENT_SEND_QUEUE_SIZE)
    )
    # In-flight viewer-initiated config changes, keyed by server_request_id.
    # MVP policy: at most one entry at a time (serialized via CONFIG_BUSY).
    pending_config_requests: dict[str, PendingConfigRequest] = field(default_factory=dict)


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
    subscribed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
