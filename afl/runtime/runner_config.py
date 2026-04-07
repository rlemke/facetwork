"""Unified runner configuration with shared defaults.

Provides a ``BaseRunnerConfig`` with fields common to all runner types
(RunnerService, RegistryRunner, AgentPoller). Specific runner configs
extend it with runner-type-specific fields.

All configs resolve sentinel values from the global ``AFLConfig`` at
construction time, so environment variables and ``.env`` settings are
respected without requiring explicit wiring.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

_SENTINEL = -1


@dataclass
class BaseRunnerConfig:
    """Common configuration shared by all runner types.

    Fields:
        service_name: Name for server registration and logging.
        server_group: Logical server group for fleet management.
        server_name: Hostname (auto-detected if empty).
        task_list: Task queue name to poll.
        poll_interval_ms: Milliseconds between poll cycles.
        max_concurrent: Max concurrent task executions.
        heartbeat_interval_ms: Server ping interval.
        topics: Optional topic filter for handler selection.
    """

    service_name: str = "afl-runner"
    server_group: str = "default"
    server_name: str = ""
    task_list: str = "default"
    poll_interval_ms: int = _SENTINEL
    max_concurrent: int = _SENTINEL
    heartbeat_interval_ms: int = _SENTINEL
    topics: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.server_name:
            self.server_name = socket.gethostname()
        self._resolve_sentinels()

    def _resolve_sentinels(self) -> None:
        """Replace sentinel values with global config defaults."""
        if self.poll_interval_ms == _SENTINEL:
            self.poll_interval_ms = _get_global("poll_interval_ms", 2000)
        if self.max_concurrent == _SENTINEL:
            self.max_concurrent = _get_global("max_concurrent", 6)
        if self.heartbeat_interval_ms == _SENTINEL:
            self.heartbeat_interval_ms = _get_global("heartbeat_interval_ms", 30000)


def _get_global(attr: str, default: int) -> int:
    """Read a runner config attribute from the global AFLConfig."""
    try:
        from ..config import get_config

        return getattr(get_config().runner, attr, default)
    except Exception:
        return default
