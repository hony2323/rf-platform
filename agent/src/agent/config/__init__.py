"""Agent configuration.

Loads and validates agent config from file / env / CLI.
Produces typed config objects consumed by other modules.

Public API
----------
load_config_dict   -- parse raw dict → AgentConfig (raises ConfigValidationError)
ConfigValidationError -- raised on any validation failure
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.domain import IQDescriptor, RFConfig, WireEncoding


@dataclass(frozen=True)
class ServerConfig:
    url: str  # wss://...
    token: str  # bearer token


@dataclass(frozen=True)
class AgentIdentity:
    node_id: str
    agent_version: str = "0.3.0"


@dataclass(frozen=True)
class QueueConfig:
    iq_queue_size: int = 4
    frame_queue_size: int = 8


@dataclass(frozen=True)
class TelemetryConfig:
    heartbeat_interval_s: float = 5.0
    status_interval_s: float = 10.0


@dataclass(frozen=True)
class ReconnectConfig:
    initial_delay_s: float = 1.0
    max_delay_s: float = 30.0
    backoff_factor: float = 2.0
    jitter: bool = True


@dataclass(frozen=True)
class AgentConfig:
    """Root config object. Composed from all sub-configs."""

    identity: AgentIdentity
    server: ServerConfig
    rf: RFConfig
    iq: IQDescriptor
    # stream_id: identifies the RF stream within a session. The server requires
    # this in stream_config and spectrum_frame messages. MVP default is "default".
    stream_id: str = "default"
    wire_encoding: WireEncoding = WireEncoding.JSON_BASE64
    queues: QueueConfig = field(default_factory=QueueConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)


# Re-export public loading API so callers can do:
#   from agent.config import load_config_dict, ConfigValidationError
from agent.config.errors import ConfigValidationError  # noqa: E402
from agent.config.loader import load_config_dict  # noqa: E402

__all__ = [
    "AgentConfig",
    "AgentIdentity",
    "ConfigValidationError",
    "QueueConfig",
    "ReconnectConfig",
    "ServerConfig",
    "TelemetryConfig",
    "load_config_dict",
]
