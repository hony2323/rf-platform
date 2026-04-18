from __future__ import annotations

from datetime import UTC, datetime

from server.sessions.models import LiveAgentSession, ViewerSubscription


class SessionRegistry:
    """In-memory registry for live agent sessions and viewer subscriptions.

    No persistence. All state is lost on process restart.
    Thread-safety: asyncio single-threaded — no locking needed.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, LiveAgentSession] = {}
        self._viewers: dict[str, ViewerSubscription] = {}

    # ------------------------------------------------------------------
    # Agent sessions
    # ------------------------------------------------------------------

    def add_session(self, session: LiveAgentSession) -> None:
        self._sessions[session.session_id] = session

    def remove_session(self, session_id: str) -> LiveAgentSession | None:
        return self._sessions.pop(session_id, None)

    def get_session(self, session_id: str) -> LiveAgentSession | None:
        return self._sessions.get(session_id)

    def get_session_by_agent(self, agent_id: str) -> LiveAgentSession | None:
        for s in self._sessions.values():
            if s.agent_id == agent_id:
                return s
        return None

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
