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

"""Shutdown signals: a forked child must not be able to impersonate the runner.

The bug this pins: a handler's multiprocessing pool forks, the children inherit
the runner's SIGTERM handler, and when the pool tears them down each one logs
"Runner stopping" under the PARENT's server_id — while the runner is healthy.
Indistinguishable from a real shutdown, one line per worker, emitted exactly
when the work finishes. It cost about an hour during a fleet deploy.

The fork cases run in a SUBPROCESS rather than forking pytest. Forking a
multi-threaded process that holds locks is precisely the hazard under test, and
pytest (capture threads) is such a process — the first version of this file
hung on its own subject matter.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.skipif(
    not hasattr(os, "fork"), reason="fork-inheritance semantics are POSIX-only"
)

_PREAMBLE = """
import os, signal, sys, time
sys.path.insert(0, {repo!r})
from facetwork.runtime.signals import install_shutdown_handlers
"""


def _run(body: str) -> str:
    """Run a snippet in a clean interpreter; return its stdout."""
    repo = str(__import__("pathlib").Path(__file__).resolve().parents[2])
    script = _PREAMBLE.format(repo=repo) + textwrap.dedent(body)
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"snippet failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout.strip()


def test_the_installing_process_still_shuts_down():
    """The point of having a handler at all."""
    assert _run("""
        called = []
        install_shutdown_handlers(lambda: called.append(os.getpid()))
        os.kill(os.getpid(), signal.SIGTERM)
        print("stopped" if called == [os.getpid()] else f"WRONG {called}")
    """) == "stopped"


def test_a_forked_child_gets_the_default_disposition_back():
    """After fork the child holds SIG_DFL, so a pool's SIGTERM terminates it
    instead of running the parent's graceful-shutdown path."""
    assert _run("""
        install_shutdown_handlers(lambda: None)
        r, w = os.pipe()
        if os.fork() == 0:
            os.close(r)
            os.write(w, b"1" if signal.getsignal(signal.SIGTERM) is signal.SIG_DFL else b"0")
            os._exit(0)
        os.close(w)
        answer = os.read(r, 1)
        os.wait()
        print("default" if answer == b"1" else "INHERITED THE HANDLER")
    """) == "default"


def test_a_forked_child_terminates_on_sigterm_and_does_not_run_stop():
    """End to end, and the symptom directly: the child must die, and `stop`
    must not run — a child that reaches `stop` is what logged the parent's
    server_id."""
    assert _run("""
        marker = "/tmp/fw_signals_child_ran_stop"
        try:
            os.unlink(marker)
        except FileNotFoundError:
            pass

        def stop():
            open(marker, "w").write("ran")

        install_shutdown_handlers(stop)
        pid = os.fork()
        if pid == 0:
            time.sleep(10)
            os._exit(99)          # only reached if SIGTERM did not terminate us
        time.sleep(0.3)           # let the child reach the sleep
        os.kill(pid, signal.SIGTERM)
        _, status = os.waitpid(pid, 0)
        killed = os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGTERM
        print("terminated" if killed and not os.path.exists(marker) else f"BAD status={status}")
    """) == "terminated"


def test_the_owner_pid_guard_holds_when_the_handler_survives_the_fork():
    """Belt and braces for the at-fork hook.

    A child forked before the hook existed, or by a library that re-installs
    handlers afterwards, can still hold the parent's handler. The owner-pid
    guard makes it terminate rather than run the runner's shutdown.
    """
    assert _run("""
        install_shutdown_handlers(lambda: None)
        handler = signal.getsignal(signal.SIGTERM)
        pid = os.fork()
        if pid == 0:
            signal.signal(signal.SIGTERM, handler)   # undo the at-fork reset
            time.sleep(10)
            os._exit(99)
        time.sleep(0.3)
        os.kill(pid, signal.SIGTERM)
        _, status = os.waitpid(pid, 0)
        print("terminated" if os.WIFSIGNALED(status) else f"SURVIVED status={status}")
    """) == "terminated"


def test_stop_logs_the_pid(caplog):
    """So a recurrence is readable in the log instead of needing a process tree."""
    import logging
    from unittest.mock import MagicMock

    from facetwork.runtime.runner.service import RunnerService

    svc = RunnerService.__new__(RunnerService)
    svc._server_id = "abc123"
    svc._stopping = MagicMock()
    svc._work_ready = MagicMock()
    with caplog.at_level(logging.INFO, logger="facetwork.runtime.runner.service"):
        svc.stop()
    assert f"pid={os.getpid()}" in caplog.text
    assert "abc123" in caplog.text
