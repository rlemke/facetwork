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

"""Cooperative cancellation for in-flight handlers (lessons-learned §16).

``fw maint terminate-workflow`` marks a run's tasks ``canceled`` in the store,
but nothing in-flight ever read that: a long import kept consuming CPU, disk and
API quota until it finished on its own. This is the missing half — a token the
runner injects into every dispatch so a handler can notice and stop.

Cancellation here means one thing, stated from the handler's point of view:
**whatever you produce will not be accepted.** That covers more than an operator
terminate, and the extra cases matter more in practice:

* the task was cancelled (``fw maint terminate-workflow``);
* the task already reached a terminal state — the execution watchdog failed it
  while the handler kept running;
* the task is no longer *ours* — the orphan reaper reset it to ``pending`` and
  another runner re-claimed it. This execution is a zombie: it is doing
  duplicate work whose result the store will refuse (terminal writes are
  ownership-gated). Zombies were previously invisible to the handler.

Cooperative by construction: a handler that never checks is not interrupted —
Python cannot safely kill a thread blocked in C code (see the note on
``Future.cancel()`` in :mod:`facetwork.runtime.base_runner`). Non-cooperative
handlers remain bounded by ``FW_TASK_EXECUTION_TIMEOUT_MS``. What this adds is
the ability to stop *promptly and cleanly*, releasing resources at a point the
handler chooses.

Usage from a handler::

    def handle(payload: dict) -> dict:
        ctx = HandlerContext.from_payload(payload)
        for i, region in enumerate(regions):
            ctx.raise_if_cancelled()      # abort between units of work
            process(region)
            ctx.heartbeat(progress_pct=int(100 * i / len(regions)))

or, when there is cleanup to do::

        if ctx.is_cancelled:
            drop_partial_import()
            return {"status": "cancelled"}

The check is cached (see ``poll_interval_s``): calling it in a tight loop costs
one store query every few seconds, not one per iteration, so handlers can check
as often as is convenient.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# How often the token re-queries the store. Cancellation is not latency-critical
# — the point is to stop within seconds instead of hours — and a short interval
# multiplied by every concurrent handler on every runner is real database load.
DEFAULT_POLL_INTERVAL_S = 3.0


class HandlerCancelled(Exception):
    """Raised by :meth:`CancellationToken.raise_if_cancelled`.

    Runners treat this as a clean stop, NOT a failure: the task is left in its
    cancelled/terminal state and is not retried. Do not catch it to continue
    working — catch it only to clean up, then re-raise.
    """

    def __init__(self, reason: str = "cancelled"):
        super().__init__(reason)
        self.reason = reason


@dataclass
class CancellationToken:
    """Cancellation signal for one handler execution.

    Latching: once cancelled it never reports healthy again, so a handler cannot
    be fooled by a transient store read into resuming work that will be
    discarded.
    """

    # Returns a reason string when this execution should stop, else None.
    _check: Callable[[], str | None] = field(default=lambda: None, repr=False)
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S

    _reason: str | None = field(default=None, init=False, repr=False)
    _last_poll: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def is_cancelled(self) -> bool:
        """True once this execution's output would no longer be accepted."""
        return self._poll() is not None

    @property
    def reason(self) -> str:
        """Why it was cancelled, or "" while healthy."""
        return self._poll() or ""

    def raise_if_cancelled(self) -> None:
        """Abort the handler with :class:`HandlerCancelled` if cancelled."""
        reason = self._poll()
        if reason is not None:
            raise HandlerCancelled(reason)

    def check(self) -> str | None:
        """Reason string if cancelled, else None.

        Public because this is what travels in the handler payload: every value
        a runner injects is either plain data or a **callable**, so handlers can
        serialise the rest with ``{k: v for k, v in payload.items() if not
        callable(v)}``. Injecting the token object itself would break that
        contract for every handler that logs or forwards its payload.
        """
        return self._poll()

    def cancel(self, reason: str = "cancelled") -> None:
        """Trip the token locally (used by the runner on shutdown/drain)."""
        with self._lock:
            if self._reason is None:
                self._reason = reason

    def _poll(self) -> str | None:
        with self._lock:
            if self._reason is not None:
                return self._reason  # latched
            now = time.monotonic()
            if self._last_poll and (now - self._last_poll) < self.poll_interval_s:
                return None
            self._last_poll = now
            check = self._check
        # Query outside the lock: the store call can block, and holding the lock
        # would serialise every concurrent handler sharing this runner.
        try:
            reason = check()
        except Exception:
            # A store hiccup must never abort a healthy handler — that would
            # turn a blip into lost work. Stay healthy and re-check next poll.
            logger.debug("Cancellation check failed; treating as not cancelled", exc_info=True)
            return None
        if reason is None:
            return None
        with self._lock:
            if self._reason is None:
                self._reason = reason
            return self._reason


def never_cancelled() -> CancellationToken:
    """A token that is never cancelled — the default outside a real dispatch."""
    return CancellationToken()
