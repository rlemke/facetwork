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

"""Handlers for the built-in ``fw.exec`` facets (``facetwork/ffl/exec.ffl``).

The escape hatch for a workflow that needs a tool nothing wraps — Snakemake's
``shell:``, with the two things that make ``shell:`` dangerous removed.

**No shell.** ``args`` is an argv list and ``shell=False`` is not a parameter.
A file name containing a space, a quote or ``$(…)`` cannot change what runs.

**The host decides what may run.** ``FW_EXEC_ALLOW`` names the permitted
binaries; unset means refuse. This is not defence in depth for its own sake — it
is the same boundary the script sandbox already draws (no ``open`` builtin, a
fixed import allowlist), which an ungated exec would erase. And it follows the
model script environments use: a host advertises a capability, and work needing
it runs where it is provided.

Two smaller decisions, both load-bearing:

* **Bare command names only.** A path would let ``fw.file.WriteText`` plant a
  file that ``Run`` then executes, making the allowlist decorative. (``fw.file``
  cannot set the execute bit, so the loop is closed — but only while this holds.)
* **The environment is not inherited.** A runner holds real credentials; the
  child gets PATH/HOME/LANG/TMPDIR plus what ``env`` adds, so a secret reaches a
  subprocess only when someone put it there on purpose.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
from typing import Any

from facetwork.runtime.errors import PermanentError

logger = logging.getLogger(__name__)

NAMESPACE = "fw.exec"

ALLOW_VAR = "FW_EXEC_ALLOW"

# Passed through to the child. Everything else — API keys, S3 credentials, the
# fleet's secrets — is dropped.
_PASSTHROUGH_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ")

_CHUNK = 65536
_STDERR_TAIL = 2000


class ExecNotAllowed(PermanentError):
    """The runner does not permit this command.

    A ``PermanentError``, because a policy refusal is not a blip: retrying it
    five times with exponential backoff re-derives the same answer and delays
    the real diagnosis by minutes. Observed on the first end-to-end run before
    this was fixed — retry 1/5, 2/5, 3/5, all identical.

    It also keeps the failure off the facet's circuit breaker, which is right:
    the command being unlisted says something about this HOST's configuration,
    not about the health of ``fw.exec.Run``.
    """


def _log(step_log: Any, message: str, level: str = "info") -> None:
    if callable(step_log):
        step_log(message, level)


def _hostname() -> str:
    import socket

    return os.environ.get("FW_SERVER_NAME") or socket.gethostname()


def allowed_commands() -> set[str]:
    raw = os.environ.get(ALLOW_VAR, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _check_allowed(command: str) -> str:
    """Validate *command* against the gate, returning its resolved path."""
    if os.sep in command or (os.altsep and os.altsep in command):
        raise ExecNotAllowed(
            f"{command!r} is a path — {NAMESPACE}.Run takes a bare command name resolved "
            "on PATH. A path would let a workflow execute a file it just wrote, which "
            f"would make {ALLOW_VAR} decorative."
        )
    allowed = allowed_commands()
    if not allowed:
        raise ExecNotAllowed(
            f"{NAMESPACE}.Run is not enabled on {_hostname()}: {ALLOW_VAR} is unset. "
            f"Set it to the binaries this host may run, e.g. "
            f"{ALLOW_VAR}=osmium,tippecanoe,ogr2ogr — a host without the binary should "
            "simply not list it."
        )
    if command not in allowed:
        raise ExecNotAllowed(
            f"{command!r} is not in {ALLOW_VAR} on {_hostname()} "
            f"(allowed here: {', '.join(sorted(allowed))})"
        )
    resolved = shutil.which(command)
    if not resolved:
        raise FileNotFoundError(
            f"{command!r} is allowed on {_hostname()} but is not on PATH there — "
            "the host advertises a capability it does not have"
        )
    return resolved


def _child_env(extra: Any) -> dict[str, str]:
    env = {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ}
    if isinstance(extra, str) and extra.strip():
        extra = json.loads(extra)
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


def _as_list(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return list(value)


def _drain(stream: Any, cap: int, out: dict, key: str) -> None:
    """Keep the first *cap* bytes, count and discard the rest.

    Reading everything into memory is how a chatty program takes a runner down;
    stopping the read is how it deadlocks on a full pipe. So: keep a bounded
    prefix and keep draining.
    """
    kept = bytearray()
    total = 0
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if cap <= 0 or len(kept) < cap:
            room = len(chunk) if cap <= 0 else cap - len(kept)
            kept.extend(chunk[:room])
    out[key] = bytes(kept)
    out[key + "_total"] = total


def handle_run(params: dict[str, Any]) -> dict[str, Any]:
    command = params["command"]
    args = [str(a) for a in _as_list(params.get("args"))]
    cwd = params.get("cwd") or None
    stdin = params.get("stdin") or ""
    timeout = int(params.get("timeout_seconds") or 0)
    cap = int(params.get("max_output_bytes") or 0)
    ok_codes = {int(c) for c in _as_list(params.get("allow_exit_codes"))} | {0}
    step_log = params.get("_step_log")

    resolved = _check_allowed(command)
    argv = [resolved, *args]
    # For the caller and the log. Quoted for READING; it is never parsed back,
    # and nothing here ever goes through a shell.
    command_line = shlex.join([command, *args])
    _log(step_log, f"exec: {command_line}")

    started = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603 — argv list, no shell, gated above
        argv,
        cwd=cwd,
        env=_child_env(params.get("env")),
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Its own process group, so a timeout kills the children too — a
        # program that spawned helpers must not leave them holding scratch disk.
        start_new_session=True,
    )

    captured: dict[str, Any] = {}
    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, cap, captured, "out"), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, cap, captured, "err"), daemon=True),
    ]
    for t in readers:
        t.start()
    if stdin:
        try:
            proc.stdin.write(stdin.encode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            pass  # the child exited without reading; its status is the real news

    timed_out = False
    try:
        proc.wait(timeout=timeout or None)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        proc.wait()
    for t in readers:
        t.join(timeout=10)

    duration_ms = int((time.monotonic() - started) * 1000)
    stdout = captured.get("out", b"").decode("utf-8", errors="replace")
    stderr = captured.get("err", b"").decode("utf-8", errors="replace")
    truncated = bool(
        cap and (captured.get("out_total", 0) > cap or captured.get("err_total", 0) > cap)
    )

    if timed_out:
        raise TimeoutError(
            f"{command_line} exceeded timeout_seconds={timeout} and its process group was "
            f"killed{_stderr_hint(stderr)}"
        )
    if proc.returncode not in ok_codes:
        # The tail of stderr is what turns "exit status 1" into a diagnosis.
        raise RuntimeError(
            f"{command_line} exited {proc.returncode}{_stderr_hint(stderr)}"
        )

    _log(step_log, f"exit {proc.returncode} in {duration_ms}ms", "success")
    return {
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
        "duration_ms": duration_ms,
        "command_line": command_line,
    }


def _stderr_hint(stderr: str) -> str:
    tail = stderr.strip()[-_STDERR_TAIL:]
    return f" — stderr: {tail}" if tail else " (no stderr)"


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def handle_which(params: dict[str, Any]) -> dict[str, Any]:
    command = params["command"]
    path = shutil.which(command) or "" if os.sep not in command else ""
    return {
        "found": bool(path),
        "path": path,
        # Reported, not raised: the point is to let a workflow check BEFORE
        # fanning out 3,000 iterations that would each fail identically on a
        # host nobody provisioned.
        "allowed": command in allowed_commands() and os.sep not in command,
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

# A wrapped tool legitimately runs for hours (osmium on a continental PBF), so
# the per-handler execution timeout is disabled here and `timeout_seconds` is
# the real bound. See CLAUDE.md, "Runner resilience tuning".
HANDLER_TIMEOUT_MS = 0

_DISPATCH: dict[str, Any] = {
    f"{NAMESPACE}.Run": handle_run,
    f"{NAMESPACE}.Which": handle_which,
}


def handle(payload: dict) -> dict:
    """RegistryRunner entrypoint — one entrypoint, dispatching on ``_facet_name``."""
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise KeyError(f"no built-in exec handler for {facet!r}")
    return handler(payload)


def facet_names() -> list[str]:
    return sorted(_DISPATCH)


__all__ = ["ExecNotAllowed", "allowed_commands", "facet_names", "handle"]
