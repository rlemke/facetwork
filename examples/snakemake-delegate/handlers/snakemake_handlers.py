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

"""Snakemake delegation adapter (external-engine-delegation.md §4, depth D2).

Runs an existing Snakefile as one Facetwork step and reports what it did — the
**blocking with heartbeat** depth, not D3. The cost is stated rather than
hidden: this handler occupies a runner worker slot for the whole external run.

**How this differs from the Ray adapter, and why it matters.** Ray has a job
submission API with server-side ids, so that adapter derives a `submission_id`
from the step id and a redelivered task *attaches* to the job already in flight.
Snakemake has no such registry: it is a local process that records completion in
the **filesystem**. So idempotency here is not an id at all — it is Snakemake's
own incremental model. A redelivered task re-invokes it, and it answers "nothing
to be done" from the outputs on disk.

That is a deliberate reliance on the property thesis §13.3 criticises, and it is
sound for exactly the reason the criticism is: the answer comes from timestamps,
so it is cheap and it is only as good as the target engine's own staleness
check. The adapter therefore *reports* which answer it got (`nothing_to_be_done`)
instead of hiding it, so a workflow can tell a real run from a no-op.

What this exercises end to end:

* **The stage budget** (§5.1) — the run is declared long, so no watchdog, reaper
  or lease reclaims the task mid-run.
* **Terminate propagation** (§7.2) — on cancellation or timeout the whole
  process *group* is killed, because Snakemake spawns the jobs and dying alone
  would orphan them.
* **A stale lock is a crash, and crashes are ambiguous** — see `_unlock`.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from facetwork.runtime.errors import PermanentError
from facetwork.runtime.handler_context import HandlerCancelled, HandlerContext

log = logging.getLogger(__name__)

NAMESPACE = "snakemake.delegate"

#: Where to find the binary. A venv of its own is the usual answer — Snakemake
#: pulls in ~20 MiB including an LP solver, and a runner has no use for it in
#: process.
SNAKEMAKE_BIN = os.environ.get("FW_SNAKEMAKE_BIN", "snakemake")
#: How often to heartbeat while the external run is in flight.
POLL_INTERVAL_S = float(os.environ.get("FW_SNAKEMAKE_POLL_INTERVAL_S", "2"))
#: How often to check whether it has EXITED. Much shorter than the heartbeat:
#: this is what a short delegated call actually costs.
WAIT_TICK_S = float(os.environ.get("FW_SNAKEMAKE_WAIT_TICK_S", "0.05"))
LOG_TAIL_CHARS = 4000
#: Grace between SIGTERM and SIGKILL when unwinding, so Snakemake can remove its
#: own lock and mark incomplete outputs before it dies.
TERM_GRACE_S = float(os.environ.get("FW_SNAKEMAKE_TERM_GRACE_S", "10"))

_NOTHING_TO_DO = re.compile(r"Nothing to be done", re.I)
_JOB_COUNT = re.compile(r"^\s*total\s+(\d+)\s*$", re.M | re.I)
_LOCKED = re.compile(r"Directory cannot be locked|already locked", re.I)


def _require(payload: dict, key: str) -> str:
    value = (payload.get(key) or "").strip()
    if not value:
        raise PermanentError(
            f"{key} is required — a retry cannot supply a missing parameter"
        )
    return value


def _resolve_binary() -> str:
    """Locate the snakemake executable, or refuse permanently.

    Not installed is a deployment fact, not a transient fault, so this
    dead-letters instead of burning five retries on a host that will never have
    it — the same reasoning as the Ray adapter's missing-import branch.
    """
    from shutil import which

    found = which(SNAKEMAKE_BIN) if os.path.basename(SNAKEMAKE_BIN) == SNAKEMAKE_BIN \
        else (SNAKEMAKE_BIN if os.path.isfile(SNAKEMAKE_BIN) else None)
    if not found:
        raise PermanentError(
            f"snakemake not found (looked for {SNAKEMAKE_BIN!r}). Install it on this "
            "runner and/or set FW_SNAKEMAKE_BIN to its absolute path."
        )
    return found


def _command(binary: str, payload: dict, workdir: str, snakefile: str) -> list[str]:
    cores = payload.get("cores", 1)
    cores = 1 if cores is None else int(cores)
    if cores <= 0:
        raise PermanentError(f"cores must be positive, got {cores}")
    cmd = [
        binary,
        "--snakefile", snakefile,
        "--directory", workdir,
        "--cores", str(cores),
        # Snakemake ≥8 requires an explicit deployment method; "conda" would
        # silently try to build environments on a runner that has no conda.
        "--software-deployment-method", "env-modules",
        "--nocolor",
    ]
    # argv list, never a shell string — the same rule fw.exec.Run enforces, and
    # for the same reason: a path containing a space or `$( )` must not be able
    # to change what runs.
    extra = payload.get("extra_args") or ""
    if extra:
        cmd += shlex.split(extra)
    targets = payload.get("targets") or ""
    if targets:
        cmd += shlex.split(targets)
    return cmd


def _unlock(binary: str, workdir: str, snakefile: str, log_fn) -> None:
    """Clear a lock left behind by a killed run.

    ⚠️ This is the same ambiguity the orphan reaper faces: a lock means *some
    process may be running here*, and from outside there is no way to tell a
    crashed owner from a live one. Snakemake cannot tell either, which is why it
    refuses rather than guessing.

    So it is opt-in (`unlock_stale`). Set it when the working directory belongs
    to this step alone — then the only process that could hold the lock is a
    previous attempt of this same task, and the claim model already guarantees
    that attempt is over. Leave it off for a shared directory, where unlocking
    could stomp a concurrent run that is perfectly healthy.
    """
    log_fn("stale Snakemake lock found — unlocking (unlock_stale=true)", level="warning")
    subprocess.run(
        [binary, "--snakefile", snakefile, "--directory", workdir, "--unlock", "--nocolor"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _terminate(proc: subprocess.Popen, log_fn) -> None:
    """Kill the process GROUP, not just Snakemake.

    Snakemake is a supervisor: it spawns the rule jobs. Killing only the parent
    leaves those children running with nothing pointing at them — the exact
    "billing on unattended" failure §7.2 exists to prevent. SIGTERM first so it
    can release its lock, SIGKILL if it will not go.
    """
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:  # pragma: no cover - process already reaped
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
        log_fn(f"sent SIGTERM to snakemake process group {pgid}", level="warning")
        try:
            proc.wait(timeout=TERM_GRACE_S)
            return
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            log_fn(f"process group {pgid} did not exit — SIGKILL", level="warning")
    except OSError as exc:  # pragma: no cover - races with natural exit
        log.warning("could not terminate snakemake group: %s", exc)


def _run_workflow_handler(payload: dict) -> dict:
    ctx = HandlerContext.from_payload(payload)
    snakefile = _require(payload, "snakefile")
    workdir = _require(payload, "workdir")
    if not Path(snakefile).is_file():
        raise PermanentError(f"snakefile does not exist: {snakefile}")
    if not Path(workdir).is_dir():
        raise PermanentError(f"workdir does not exist: {workdir}")

    # Explicit, not `x or 120`: that idiom silently rewrites a caller's 0 into
    # the default, so a workflow asking for no wait would quietly wait two hours.
    raw_timeout = payload.get("run_timeout_minutes", 120)
    timeout_min = 120 if raw_timeout is None else int(raw_timeout)
    if timeout_min <= 0:
        raise PermanentError(
            f"run_timeout_minutes must be positive, got {timeout_min} — "
            "a retry cannot fix a bad parameter"
        )

    binary = _resolve_binary()
    cmd = _command(binary, payload, workdir, snakefile)
    ctx.step_log(f"delegating to snakemake: {' '.join(shlex.quote(c) for c in cmd)}")

    started = time.monotonic()
    out, code = _spawn_and_wait(cmd, ctx, timeout_min)

    if code != 0 and _LOCKED.search(out):
        if payload.get("unlock_stale"):
            _unlock(binary, workdir, snakefile, ctx.step_log)
            out, code = _spawn_and_wait(cmd, ctx, timeout_min)
        else:
            raise PermanentError(
                f"{workdir} is locked by Snakemake. A previous run was killed, or "
                "another run is live. Pass unlock_stale=true ONLY if this working "
                "directory belongs to this step alone; otherwise clear it with "
                f"`{binary} --directory {workdir} --unlock`."
            )

    duration = time.monotonic() - started
    tail = out[-LOG_TAIL_CHARS:]
    nothing = bool(_NOTHING_TO_DO.search(out))
    jobs = 0 if nothing else _parse_jobs(out)

    if code != 0:
        ctx.step_log(f"snakemake exited {code}", level="error")
        # NOT PermanentError: a rule can fail transiently (a full disk, a flaky
        # download inside the Snakefile). Let the retry budget decide — and note
        # the retry re-invokes Snakemake, which resumes from what is on disk
        # rather than starting over.
        raise RuntimeError(f"snakemake exited {code}: {tail[-800:]}")

    ctx.step_log(
        f"snakemake finished in {duration:.1f}s — "
        + ("nothing to be done" if nothing else f"{jobs} job(s)"),
        level="success",
    )
    return {
        "status": "success",
        "jobs_run": jobs,
        "nothing_to_be_done": nothing,
        "duration_seconds": round(duration, 3),
        "log_tail": tail,
    }


def _spawn_and_wait(cmd: list[str], ctx: HandlerContext, timeout_min: int) -> tuple[str, int]:
    """Run the command, heartbeating, until it exits / is cancelled / times out."""
    proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # Its own process group, so cancellation can take the children with it.
        start_new_session=True,
    )
    chunks: list[str] = []
    # Declare the wait, so no clock reclaims this task mid-run (§5.1).
    with ctx.stage("snakemake-run", timeout_ms=timeout_min * 60_000) as stage:
        deadline = time.monotonic() + timeout_min * 60
        next_beat = 0.0
        try:
            while proc.poll() is None:
                try:
                    ctx.raise_if_cancelled()
                except HandlerCancelled:
                    _terminate(proc, ctx.step_log)
                    raise
                now = time.monotonic()
                if now > deadline:
                    _terminate(proc, ctx.step_log)
                    raise TimeoutError(
                        f"snakemake exceeded run_timeout_minutes={timeout_min}"
                    )
                # Two different clocks, deliberately. Exit is detected at
                # WAIT_TICK_S; the heartbeat still goes out at POLL_INTERVAL_S.
                # Sleeping the heartbeat interval instead would put a floor
                # under every delegated call equal to that interval — measured:
                # a "nothing to be done" no-op cost 2.015s against a real
                # 14-job run's 2.009s, i.e. the adapter's own polling hid the
                # very cheapness that makes a redelivered task cheap.
                if now >= next_beat:
                    stage.heartbeat(progress_message="snakemake running")
                    next_beat = now + POLL_INTERVAL_S
                time.sleep(WAIT_TICK_S)
        finally:
            # Drain whatever was produced, even when unwinding — the output is
            # how anyone finds out why.
            try:
                chunks.append(proc.communicate(timeout=30)[0] or "")
            except Exception:  # noqa: BLE001
                _terminate(proc, ctx.step_log)
    return "".join(chunks), proc.returncode


def _parse_jobs(out: str) -> int:
    """Job count from Snakemake's own summary table ("total  N")."""
    found = _JOB_COUNT.findall(out)
    return int(found[-1]) if found else 0


_DISPATCH = {
    f"{NAMESPACE}.RunWorkflow": _run_workflow_handler,
}


def handle(payload: dict) -> dict:
    """RegistryRunner dispatch entrypoint."""
    facet_name = payload["_facet_name"]
    handler = _DISPATCH.get(facet_name)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet_name}")
    return handler(payload)


def register_handlers(runner) -> None:
    """Register all facets with a RegistryRunner."""
    for facet_name in _DISPATCH:
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
            # 0 = no per-handler timeout; the stage budget and
            # run_timeout_minutes bound the wait instead.
            timeout_ms=0,
        )


def register_snakemake_handlers(poller) -> None:
    """Register with an AgentPoller."""
    for fqn, func in _DISPATCH.items():
        poller.register(fqn, func)
