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

"""Shutdown-signal handling that a forked child cannot impersonate.

A handler that calls `os.fork` — directly, or via a library that does, which is
most multiprocessing pools — hands the child a copy of every signal handler the
runner installed. When the pool later terminates its workers with SIGTERM, each
child runs the runner's *shutdown* handler and logs, with the parent's
``server_id``, that the runner is stopping.

It is not. The parent is fine, and the log says otherwise, in as many copies as
there were workers. Chasing that during a fleet deploy cost about an hour: the
lines are indistinguishable from a real shutdown, they carry an id that belongs
to a process that is still running, and they appear at exactly the moment the
work finishes — which is also when a graceful stop would appear.

Two defences, because either alone leaves a gap:

* **at-fork** — children get SIG_DFL back, so SIGTERM does what a worker's
  SIGTERM is supposed to do (terminate it) instead of running our shutdown path;
* **an owner-pid guard** — a child forked before the hook was registered, or by
  a library that installs its own handlers afterwards, still cannot run the
  parent's shutdown. It restores the default and re-raises, so the child dies as
  the pool expects.
"""

from __future__ import annotations

import logging
import os
import signal
from collections.abc import Callable

logger = logging.getLogger(__name__)

_SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)

#: Registered once per process — `os.register_at_fork` has no deregister, so a
#: second registration would just run the same reset twice.
_fork_hook_registered = False


def _reset_in_child() -> None:
    """Restore default shutdown-signal disposition. Runs in a freshly forked child."""
    for sig in _SHUTDOWN_SIGNALS:
        try:
            signal.signal(sig, signal.SIG_DFL)
        except (ValueError, OSError):  # pragma: no cover - non-main thread/platform
            pass


def install_shutdown_handlers(stop: Callable[[], None]) -> None:
    """Install SIGTERM/SIGINT handlers that only *this* process will honour.

    Args:
        stop: called on a shutdown signal received by the installing process.
    """
    global _fork_hook_registered

    owner_pid = os.getpid()

    def handle_signal(signum: int, frame: object) -> None:
        if os.getpid() != owner_pid:
            # A forked child. It does not own this runner, so it must not run
            # the runner's shutdown — restore the default and let the signal do
            # what the forking library expects it to do.
            _reset_in_child()
            os.kill(os.getpid(), signum)
            return
        stop()

    for sig in _SHUTDOWN_SIGNALS:
        signal.signal(sig, handle_signal)

    if not _fork_hook_registered and hasattr(os, "register_at_fork"):
        os.register_at_fork(after_in_child=_reset_in_child)
        _fork_hook_registered = True
