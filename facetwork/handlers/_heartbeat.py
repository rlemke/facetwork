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

"""Liveness for built-in handlers that block for a long time.

⚠️ NO BUILT-IN HANDLER SIGNALLED LIVENESS UNTIL THIS EXISTED, and several of
them legitimately block for minutes: ``fw.http.Fetch`` pulls multi-GB artifacts,
``fw.archive.Extract`` unpacks them, ``fw.exec.Run`` is registered with
``timeout_ms=0`` precisely because a wrapped tool may run for hours, and
``fw.compare.Tabular`` parses millions of records.

The runtime injects a ``_task_heartbeat`` callback into every handler's params
for exactly this, and the built-ins ignored it. Measured 2026-09-01: a
``fw.compare.Tabular`` comparing two 100 MB+ VCFs was RECLAIMED mid-run —
"previous server stopped responding, server silent for 120s" — and re-dispatched
onto a second host while the first was still working.

The documented batch pattern puts the heartbeat at a loop boundary, but these
phases have no loop to hook: a download, a decompression and a parse are each
one call that blocks. A ticker thread is the right shape BECAUSE the work is
opaque — it reports elapsed time rather than progress, which is honest about
what can be observed from outside a blocking call.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any, Iterator

logger = logging.getLogger(__name__)

#: Well under the stuck-task and reaper windows, and cheap: one small update.
_INTERVAL_S = 20.0


@contextlib.contextmanager
def heartbeating(params: dict[str, Any], what: str) -> Iterator[None]:
    """Keep this task's liveness signal alive across ONE blocking call.

    A no-op when the runtime did not inject a callback (direct calls, tests,
    CLI use), so wrapping a phase is always safe.
    """
    hb = params.get("_task_heartbeat")
    log = params.get("_step_log")
    if not callable(hb):
        yield
        return

    stop = threading.Event()
    started = time.monotonic()

    def _tick() -> None:
        while not stop.wait(_INTERVAL_S):
            elapsed = int(time.monotonic() - started)
            try:
                hb()
            except Exception:  # noqa: BLE001 — liveness must never fail the work
                logger.debug("heartbeat failed", exc_info=True)
            if callable(log):
                try:
                    log(f"{what} ({elapsed // 60} min elapsed)")
                except Exception:  # noqa: BLE001
                    pass

    t = threading.Thread(target=_tick, name="builtin-heartbeat", daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()


def heartbeats(what: str):
    """Decorator form: keep a whole handler's liveness alive.

    Preferred over an inline `with` for built-ins whose blocking work is the
    entire body. It cannot be misplaced — there is no call site to forget, no
    block to indent wrongly, and adding it to a new handler is one line. That
    matters more than elegance here: the inline form was applied to one of four
    call sites in a domain package, twice, on the same day.

    Reports elapsed time for the whole invocation, which is the honest signal
    for opaque work: it says "still running", not "making progress".
    """
    def _decorate(fn):
        import functools

        @functools.wraps(fn)
        def _wrapped(params, *a, **kw):
            with heartbeating(params if isinstance(params, dict) else {}, what):
                return fn(params, *a, **kw)

        # functools.wraps makes inspect.getsource return the UNDECORATED source,
        # so a text check for "heartbeating" would not see this. Mark the wrapper
        # explicitly — the enforcement test asks the object, not the text.
        _wrapped._fw_heartbeats = what
        return _wrapped
    return _decorate
