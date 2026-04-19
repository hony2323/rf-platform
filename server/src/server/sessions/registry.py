from __future__ import annotations

from datetime import UTC, datetime

from server.sessions.models import LiveAgentSession, ViewerSubscription


class SessionRegistry:
    """In-memory registry for live agent sessions and viewer subscriptions.

    No persistence. All state is lost on process restart.
    Thread-safety: asyncio single-threaded — no locking needed.

    Invariants:
    - At most one live session per agent_id.
    - Removing a session also removes all viewers attached to it.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, LiveAgentSession] = {}
        # Secondary index: agent_id -> session_id for O(1) lookup.
        self._agent_index: dict[str, str] = {}
        self._viewers: dict[str, ViewerSubscription] = {}

    # ------------------------------------------------------------------
    # Agent sessions
    # ------------------------------------------------------------------

    def add_session(self, session: LiveAgentSession) -> None:
        # If this agent already has a live session, evict it first so
        # there is never more than one session per agent_id.
        existing_sid = self._agent_index.get(session.agent_id)
        if existing_sid is not None and existing_sid != session.session_id:
            self._sessions.pop(existing_sid, None)
            self._evict_viewers(existing_sid)
        self._sessions[session.session_id] = session
        self._agent_index[session.agent_id] = session.session_id

    def remove_session(self, session_id: str) -> LiveAgentSession | None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return None
        self._agent_index.pop(session.agent_id, None)
        self._evict_viewers(session_id)
        return session

    def get_session(self, session_id: str) -> LiveAgentSession | None:
        return self._sessions.get(session_id)

    def get_session_by_agent(self, agent_id: str) -> LiveAgentSession | None:
        sid = self._agent_index.get(agent_id)
        if sid is None:
            return None
        return self._sessions.get(sid)

    def update_heartbeat(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.last_heartbeat_at = datetime.now(UTC)
        return True

    def update_status(self, session_id: str, status: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.last_status = status
        return True

    def update_config_version(self, session_id: str, config_version: int) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.config_version = config_version
        return True

    def update_stream_config(
        self, session_id: str, stream_id: str, bin_count: int, config_version: int
    ) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.stream_id = stream_id
        session.bin_count = bin_count
        session.config_version = config_version
        return True

    def all_sessions(self) -> list[LiveAgentSession]:
        return list(self._sessions.values())

    # ------------------------------------------------------------------
    # Viewer subscriptions
    # ------------------------------------------------------------------

    def add_viewer(self, viewer: ViewerSubscription) -> None:
        self._viewers[viewer.subscription_id] = viewer

    def remove_viewer(self, subscription_id: str) -> ViewerSubscription | None:
        return self._viewers.pop(subscription_id, None)

    def get_viewer(self, subscription_id: str) -> ViewerSubscription | None:
        return self._viewers.get(subscription_id)

    def get_viewers_for_session(self, session_id: str) -> list[ViewerSubscription]:
        return [v for v in self._viewers.values() if v.session_id == session_id]

    def all_viewers(self) -> list[ViewerSubscription]:
        return list(self._viewers.values())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_viewers(self, session_id: str) -> None:
        to_remove = [
            sid for sid, v in self._viewers.items() if v.session_id == session_id
        ]
        for sid in to_remove:
            del self._viewers[sid]
