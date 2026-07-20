"""Unified runner configuration with shared defaults.

Provides a ``BaseRunnerConfig`` with fields common to all runner types
(RunnerService, RegistryRunner, AgentPoller). Specific runner configs
extend it with runner-type-specific fields.

All configs resolve sentinel values from the global ``FFLConfig`` at
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
    # Environment manifest hashes this runner can execute scripts in
    # (script-environments.md §3). Defaults from FW_PROVIDED_ENVS (comma-
    # separated) plus discovery of materialized venvs under FW_ENV_ROOT.
    provided_environments: list[str] = field(default_factory=list)
    # Continuation/orchestration role (see docs/architecture/ffl-runner-orchestration-tier.md):
    #   "inline" (default) — today's behaviour: poll the shared _fw_continue
    #                        backlog AND run the stuck-step sweep, in addition to
    #                        the inline cascade after this runner's own handler.
    #   "off"              — handler-only: keep the inline cascade after own
    #                        handler, but do NOT poll the shared backlog and do
    #                        NOT run the sweep (a dedicated ffl-runner owns those).
    #   "shared"           — ffl-runner: own the shared backlog + sweep.
    # Empty resolves to FW_CONTINUATION_MODE, else "inline".
    continuation_mode: str = ""

    _CONTINUATION_MODES = ("inline", "off", "shared")

    def __post_init__(self) -> None:
        if not self.server_name:
            self.server_name = socket.gethostname()
        if not self.continuation_mode:
            import os

            self.continuation_mode = (
                os.environ.get("FW_CONTINUATION_MODE", "inline").strip() or "inline"
            )
        if self.continuation_mode not in self._CONTINUATION_MODES:
            raise ValueError(
                f"continuation_mode must be one of {self._CONTINUATION_MODES}, "
                f"got {self.continuation_mode!r}"
            )
        self._resolve_sentinels()

    # --- continuation-mode helpers (used by the runner poll loops) -----------
    def polls_shared_continuations(self) -> bool:
        """Whether this runner drains the shared ``_fw_continue`` backlog."""
        return self.continuation_mode in ("inline", "shared")

    def runs_stuck_step_sweep(self) -> bool:
        """Whether this runner runs the stuck-step recovery sweep."""
        return self.continuation_mode in ("inline", "shared")

    def _resolve_sentinels(self) -> None:
        """Replace sentinel values with global config defaults."""
        if self.poll_interval_ms == _SENTINEL:
            self.poll_interval_ms = _get_global("poll_interval_ms", 2000)
        if self.max_concurrent == _SENTINEL:
            self.max_concurrent = _get_global("max_concurrent", 6)
        if self.heartbeat_interval_ms == _SENTINEL:
            self.heartbeat_interval_ms = _get_global("heartbeat_interval_ms", 30000)


def _get_global(attr: str, default: int) -> int:
    """Read a runner config attribute from the global FFLConfig."""
    try:
        from ..config import get_config

        return getattr(get_config().runner, attr, default)
    except Exception:
        return default
