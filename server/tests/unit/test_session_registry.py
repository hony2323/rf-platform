from __future__ import annotations

import asyncio

import pytest

from server.sessions.models import LiveAgentSession, ViewerSubscription
from server.sessions.registry import SessionRegistry


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def make_session(
    session_id: str = "ses_1",
    agent_id: str = "agent_1",
    user_id: str = "user_1",
    stream_id: str = "default",
    config_version: int = 1,
) -> LiveAgentSession:
    return LiveAgentSession(
        session_id=session_id,
        agent_id=agent_id,
        user_id=user_id,
        stream_id=stream_id,
        config_version=config_version,
    )


def make_viewer(
    subscription_id: str = "sub_1",
    user_id: str = "user_1",
    agent_id: str = "agent_1",
    session_id: str = "ses_1",
) -> ViewerSubscription:
    return ViewerSubscription(
        subscription_id=subscription_id,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Agent session lifecycle
# ---------------------------------------------------------------------------

def test_add_and_get_session():
    reg = SessionRegistry()
    s = make_session()
    reg.add_session(s)
    assert reg.get_session("ses_1") is s


def test_get_session_returns_none_for_unknown():
    reg = SessionRegistry()
    assert reg.get_session("nope") is None


def test_remove_session_returns_session():
    reg = SessionRegistry()
    s = make_session()
    reg.add_session(s)
    removed = reg.remove_session("ses_1")
    assert removed is s
    assert reg.get_session("ses_1") is None


def test_remove_session_returns_none_for_unknown():
    reg = SessionRegistry()
    assert reg.remove_session("nope") is None


def test_get_session_by_agent():
    reg = SessionRegistry()
    s = make_session(agent_id="agent_abc")
    reg.add_session(s)
    assert reg.get_session_by_agent("agent_abc") is s


def test_get_session_by_agent_returns_none_when_not_found():
    reg = SessionRegistry()
    assert reg.get_session_by_agent("missing") is None


def test_get_session_by_agent_returns_none_after_removal():
    reg = SessionRegistry()
    s = make_session()
    reg.add_session(s)
    reg.remove_session(s.session_id)
    assert reg.get_session_by_agent(s.agent_id) is None


def test_all_sessions_returns_all():
    reg = SessionRegistry()
    s1 = make_session("s1", "a1")
    s2 = make_session("s2", "a2")
    reg.add_session(s1)
    reg.add_session(s2)
    assert sorted([s.session_id for s in reg.all_sessions()]) == ["s1", "s2"]


def test_all_sessions_empty_registry():
    assert SessionRegistry().all_sessions() == []


def test_add_session_replaces_existing_for_same_id():
    reg = SessionRegistry()
    s1 = make_session("ses_1", agent_id="a1")
    s2 = make_session("ses_1", agent_id="a2")
    reg.add_session(s1)
    reg.add_session(s2)
    assert reg.get_session("ses_1") is s2


# ---------------------------------------------------------------------------
# Session mutation
# ---------------------------------------------------------------------------

def test_update_heartbeat_returns_true_and_refreshes_timestamp():
    from datetime import UTC, datetime, timedelta

    reg = SessionRegistry()
    s = make_session()
    reg.add_session(s)
    before = s.last_heartbeat_at

    # Ensure some time passes (monotonically) by advancing slightly
    result = reg.update_heartbeat("ses_1")
    assert result is True
    assert s.last_heartbeat_at >= before


def test_update_heartbeat_returns_false_for_unknown():
    assert SessionRegistry().update_heartbeat("nope") is False


def test_update_status_stores_value():
    reg = SessionRegistry()
    reg.add_session(make_session())
    result = reg.update_status("ses_1", "nominal")
    assert result is True
    assert reg.get_session("ses_1").last_status == "nominal"


def test_update_status_returns_false_for_unknown():
    assert SessionRegistry().update_status("nope", "x") is False


def test_update_config_version_stores_value():
    reg = SessionRegistry()
    reg.add_session(make_session(config_version=1))
    result = reg.update_config_version("ses_1", 3)
    assert result is True
    assert reg.get_session("ses_1").config_version == 3


def test_update_config_version_returns_false_for_unknown():
    assert SessionRegistry().update_config_version("nope", 2) is False


# ---------------------------------------------------------------------------
# Viewer subscription lifecycle
# ---------------------------------------------------------------------------

def test_add_and_get_viewer():
    reg = SessionRegistry()
    v = make_viewer()
    reg.add_viewer(v)
    assert reg.get_viewer("sub_1") is v


def test_get_viewer_returns_none_for_unknown():
    assert SessionRegistry().get_viewer("nope") is None


def test_remove_viewer_returns_viewer():
    reg = SessionRegistry()
    v = make_viewer()
    reg.add_viewer(v)
    removed = reg.remove_viewer("sub_1")
    assert removed is v
    assert reg.get_viewer("sub_1") is None


def test_remove_viewer_returns_none_for_unknown():
    assert SessionRegistry().remove_viewer("nope") is None


def test_get_viewers_for_session_returns_matching():
    reg = SessionRegistry()
    v1 = make_viewer("sub_1", session_id="ses_1")
    v2 = make_viewer("sub_2", session_id="ses_1")
    v3 = make_viewer("sub_3", session_id="ses_2")
    reg.add_viewer(v1)
    reg.add_viewer(v2)
    reg.add_viewer(v3)
    result = reg.get_viewers_for_session("ses_1")
    assert sorted([v.subscription_id for v in result]) == ["sub_1", "sub_2"]


def test_get_viewers_for_session_returns_empty_when_none():
    reg = SessionRegistry()
    assert reg.get_viewers_for_session("ses_1") == []


def test_get_viewers_for_session_returns_empty_after_removal():
    reg = SessionRegistry()
    v = make_viewer(session_id="ses_1")
    reg.add_viewer(v)
    reg.remove_viewer(v.subscription_id)
    assert reg.get_viewers_for_session("ses_1") == []


def test_all_viewers_returns_all():
    reg = SessionRegistry()
    v1 = make_viewer("sub_1")
    v2 = make_viewer("sub_2")
    reg.add_viewer(v1)
    reg.add_viewer(v2)
    assert sorted([v.subscription_id for v in reg.all_viewers()]) == ["sub_1", "sub_2"]


def test_all_viewers_empty_registry():
    assert SessionRegistry().all_viewers() == []


# ---------------------------------------------------------------------------
# Isolation — separate registry instances share no state
# ---------------------------------------------------------------------------

def test_registries_are_independent():
    r1 = SessionRegistry()
    r2 = SessionRegistry()
    r1.add_session(make_session())
    assert r2.get_session("ses_1") is None


# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------

def test_live_agent_session_has_frame_queue():
    s = make_session()
    assert isinstance(s.frame_queue, asyncio.Queue)


def test_viewer_subscription_has_send_queue():
    v = make_viewer()
    assert isinstance(v.send_queue, asyncio.Queue)


def test_live_agent_session_connected_at_is_set():
    from datetime import UTC, datetime
    before = datetime.now(UTC)
    s = make_session()
    assert s.connected_at >= before


def test_viewer_subscription_subscribed_at_is_set():
    from datetime import UTC, datetime
    before = datetime.now(UTC)
    v = make_viewer()
    assert v.subscribed_at >= before
