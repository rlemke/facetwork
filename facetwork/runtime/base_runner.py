# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared base for the runner classes.

``RunnerService`` and ``RegistryRunner`` historically duplicated a large amount
of identical logic (server lifecycle, heartbeat, work-item accounting). Keeping
two copies let them drift — the source of several distributed-systems bugs where
one runner had the correct behavior and the other didn't. ``BaseRunner`` holds
the logic that is (or should be) identical across both, so there is ONE
implementation.

Subclasses set the attributes this base references in their own ``__init__``:

    _server_id: str
    _running: bool
    _persistence: PersistenceAPI
    _config: BaseRunnerConfig
    _stopping: threading.Event
    _active_lock: threading.Lock
    _active_futures: list

(These are not initialized here so each subclass keeps full control of its
construction; the base only reads them.)
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import TYPE_CHECKING

from .entities import ServerState

if TYPE_CHECKING:
    from .persistence import PersistenceAPI
    from .runner_config import BaseRunnerConfig

logger = logging.getLogger(__name__)


def _current_time_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


class BaseRunner:
    """Server-lifecycle and accounting logic shared by all Facetwork runners."""

    # Attributes the base reads, set by each subclass's __init__ (declared here
    # for type-checkers / readers; not assigned so subclasses own construction).
    _server_id: str
    _running: bool
    _persistence: PersistenceAPI
    _config: BaseRunnerConfig
    _stopping: threading.Event
    _active_lock: threading.Lock
    _active_futures: list

    @property
    def server_id(self) -> str:
        """Get the server's unique ID."""
        return self._server_id

    @property
    def is_running(self) -> bool:
        """Check if the runner is currently running."""
        return self._running

    def _deregister_server(self) -> None:
        """Mark this server as shut down."""
        server = self._persistence.get_server(self._server_id)
        if server:
            server.state = ServerState.SHUTDOWN
            server.ping_time = _current_time_ms()
            self._persistence.save_server(server)

    def _get_server_ips(self) -> list[str]:
        """Get local IP addresses."""
        try:
            hostname = socket.gethostname()
            return [socket.gethostbyname(hostname)]
        except Exception:
            return []

    def _heartbeat_loop(self) -> None:
        """Periodically update the server's ping_time."""
        interval_s = self._config.heartbeat_interval_ms / 1000.0
        while not self._stopping.wait(interval_s):
            try:
                self._persistence.update_server_ping(self._server_id, _current_time_ms())
            except Exception:
                logger.exception("Heartbeat failed")

    def _active_count(self) -> int:
        """Get the number of active work items."""
        with self._active_lock:
            return len(self._active_futures)
