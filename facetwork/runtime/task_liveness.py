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

"""Best-effort liveness callbacks injected into every handler dispatch.

``_task_heartbeat`` and ``_set_stage_budget`` are *signals* to the control
plane, not part of the handler's work. They used to call the store directly, so
a store outage raised INSIDE the handler and killed it — measured 2026-10-13: a
country low-zoom build lost 2.5 hours of routing (three zoom levels, 6,000 of
the last 8,000 pairs) when the database host went dark for a few minutes,
although the routing itself never touches the database.

Every runner built its own copy of these closures, so the fix lives here once
(the recovery-path lesson: a second call site silently omits the guarantee).
Failures are logged at WARNING — rate-limited, naming the facet — and dropped.
Swallowing is safe because these calls only *extend* a lease: if the outage
outlasts it, the reaper reclaims the task and the result write, which is
ownership-gated, refuses this execution's output. That is the documented
at-least-once contract; a lost heartbeat can cost duplicate work, never a
wrong result.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# One warning per task per interval: a handler heartbeating every few seconds
# through a ten-minute outage should not write hundreds of identical lines.
_WARN_INTERVAL_S = 60.0


class _RateLimitedWarning:
    def __init__(self, what: str, facet_name: str, task_uuid: str) -> None:
        self._what = what
        self._label = f"{facet_name or 'task'} ({task_uuid[:8]})"
        self._last = 0.0
        self._suppressed = 0
        self._lock = threading.Lock()

    def __call__(self, exc: BaseException) -> None:
        now = time.monotonic()
        with self._lock:
            if self._last and now - self._last < _WARN_INTERVAL_S:
                self._suppressed += 1
                return
            suppressed, self._suppressed, self._last = self._suppressed, 0, now
        more = f" (+{suppressed} more since last warning)" if suppressed else ""
        logger.warning(
            "%s for %s failed; handler continues: %s%s",
            self._what,
            self._label,
            exc,
            more,
        )


def make_task_heartbeat(
    persistence: Any,
    task_uuid: str,
    expected_server_id: str,
    facet_name: str = "",
    now_ms: Callable[[], int] | None = None,
) -> Callable[..., None]:
    """Return the ``_task_heartbeat`` callback for one dispatch.

    The renewal is ownership-gated on ``expected_server_id`` so a reclaimed
    zombie cannot extend the new owner's lease.
    """
    clock = now_ms or (lambda: int(time.time() * 1000))
    warn = _RateLimitedWarning("Task heartbeat", facet_name, task_uuid)

    def _task_heartbeat(
        progress_pct: int | None = None,
        progress_message: str | None = None,
    ) -> None:
        try:
            persistence.update_task_heartbeat(
                task_uuid,
                clock(),
                progress_pct=progress_pct,
                progress_message=progress_message,
                expected_server_id=expected_server_id,
            )
        except Exception as exc:
            warn(exc)

    return _task_heartbeat


def make_stage_budget_setter(
    persistence: Any,
    task_uuid: str,
    facet_name: str = "",
    now_ms: Callable[[], int] | None = None,
) -> Callable[..., None]:
    """Return the ``_set_stage_budget`` callback for one dispatch."""
    clock = now_ms or (lambda: int(time.time() * 1000))
    warn = _RateLimitedWarning("Stage budget update", facet_name, task_uuid)

    def _set_stage_budget(timeout_ms: int, stage_name: str = "") -> None:
        if timeout_ms <= 0:
            return
        try:
            persistence.update_task_stage_budget(
                task_uuid,
                clock() + int(timeout_ms),
                stage_name=stage_name,
            )
        except Exception as exc:
            warn(exc)

    return _set_stage_budget
