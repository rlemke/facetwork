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

"""Ray delegation adapter (external-engine-delegation.md §8.4).

Submits a Ray job, waits for it, and returns its outcome — the **D2 (blocking
with heartbeat)** depth of §4, not D3. D2 needs no new runtime concepts and is
testable today; its cost is real and stated here rather than hidden: this
handler occupies a runner worker slot for the whole external run, so with
``FW_MAX_CONCURRENT=2`` three concurrent four-hour jobs saturate two runners.
D3 (park and resume) removes that at the price of a watcher process; the pieces
it would need — derived ids, the stage budget, cancellation — are all below.

What this exercises end to end:

* **Idempotent submission** (§6) — the Ray ``submission_id`` is *derived* from
  the step id, so a redelivered task attaches to the running job rather than
  starting a second one.
* **The stage budget** (§5.1) — the run is declared long, so no watchdog,
  reaper or lease reclaims the task mid-job.
* **Terminate propagation** (§7.2) — the prerequisite delegation was blocked on.
  When the run is cancelled, the adapter calls ``stop_job`` before unwinding, so
  the external job dies with the workflow instead of billing on unattended.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from facetwork.runtime.errors import PermanentError
from facetwork.runtime.handler_context import HandlerCancelled, HandlerContext

log = logging.getLogger(__name__)

NAMESPACE = "ray.delegate"

# Ray's Job Submission API — the dashboard port, not the client port.
DEFAULT_ADDRESS = os.environ.get("FW_RAY_ADDRESS", "http://localhost:8265")
POLL_INTERVAL_S = float(os.environ.get("FW_RAY_POLL_INTERVAL_S", "2"))
LOGS_TAIL_CHARS = 2000


def _client(address: str):
    """Import lazily so the module is importable without ray installed.

    A RegistryRunner verifies a handler by importing it; failing at import time
    would make this runner advertise nothing at all rather than failing the one
    facet that actually needs ray.
    """
    try:
        from ray.job_submission import JobSubmissionClient
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise PermanentError(
            "ray is not installed on this runner — `pip install 'ray[default]'`. "
            "This is a deployment fact, not a transient fault, so the task is "
            "dead-lettered rather than retried five times."
        ) from exc
    return JobSubmissionClient(address)


def _external_id(step_id: str) -> str:
    """Derive the Ray submission id from the step id (§6).

    Derived, never generated: a redelivered task must compute the *same* id so
    it attaches to the run already in flight. Under `foreach` a step *name* is a
    role shared by many instances, so the step **id** is the only correct basis
    (paper-parity-gaps G4).
    """
    return f"fw-{step_id}"


def _submit_or_attach(client: Any, submission_id: str, payload: dict, log_fn) -> str:
    """Submit the job, or attach to the one already running under this id.

    The subtlety §8.4 does not cover: a *retry* of a step whose previous attempt
    already FAILED must not attach to that terminal job and inherit its failure
    forever. So the duplicate branch inspects the existing job — live means
    attach (the redelivery case the derived id exists for), terminal means the
    previous attempt is over and this attempt gets its own id.
    """
    from ray.job_submission import JobStatus

    runtime_env: dict[str, Any] = json.loads(payload.get("runtime_env_json") or "{}")
    working_dir = payload.get("working_dir") or ""
    if working_dir:
        runtime_env["working_dir"] = working_dir

    try:
        client.submit_job(
            entrypoint=payload["entrypoint"],
            submission_id=submission_id,
            runtime_env=runtime_env or None,
        )
        log_fn(f"Ray job submitted: {submission_id}")
        return submission_id
    except RuntimeError:
        # The id already exists. Whether that is "attach" or "the last attempt
        # is over" depends on the existing job's state.
        status = client.get_job_status(submission_id)
        if not status.is_terminal():
            log_fn(f"Ray job {submission_id} already running — attaching")
            return submission_id
        attempt = int(payload.get("_retry_count", 0) or 0)
        fresh = f"{submission_id}-r{attempt}"
        if status == JobStatus.SUCCEEDED and attempt == 0:
            # Nothing to redo: a completed job for this exact step.
            log_fn(f"Ray job {submission_id} already succeeded — reusing result")
            return submission_id
        client.submit_job(
            entrypoint=payload["entrypoint"],
            submission_id=fresh,
            runtime_env=runtime_env or None,
        )
        log_fn(f"Previous attempt {submission_id} ended {status}; submitted {fresh}")
        return fresh


def _submit_job_handler(payload: dict) -> dict:
    ctx = HandlerContext.from_payload(payload)
    step_id = payload.get("_step_id", "")
    if not step_id:
        raise PermanentError("no _step_id in payload — cannot derive a stable Ray id")

    from ray.job_submission import JobStatus

    address = payload.get("address") or DEFAULT_ADDRESS
    # Explicit, not `x or 120`: that idiom silently rewrites a caller's 0 into
    # the default, so a workflow asking for no wait would quietly wait two hours.
    raw_timeout = payload.get("run_timeout_minutes", 120)
    timeout_min = 120 if raw_timeout is None else int(raw_timeout)
    if timeout_min <= 0:
        raise PermanentError(
            f"run_timeout_minutes must be positive, got {timeout_min} — "
            "a retry cannot fix a bad parameter"
        )
    client = _client(address)
    submission_id = _submit_or_attach(client, _external_id(step_id), payload, ctx.step_log)

    # Declare the wait, so no clock reclaims this task mid-run (§5.1). The
    # budget covers the job's own timeout; the lease is renewed to cover it.
    with ctx.stage("ray-job", timeout_ms=timeout_min * 60_000) as stage:
        deadline = time.monotonic() + timeout_min * 60
        while True:
            try:
                ctx.raise_if_cancelled()
            except HandlerCancelled:
                # §7.2 — the whole point. Kill the external run before unwinding,
                # or it keeps running (and billing) with nothing pointing at it.
                _stop_quietly(client, submission_id, ctx.step_log)
                raise

            status = client.get_job_status(submission_id)
            if status.is_terminal():
                break
            if time.monotonic() > deadline:
                _stop_quietly(client, submission_id, ctx.step_log)
                raise TimeoutError(
                    f"Ray job {submission_id} exceeded run_timeout_minutes={timeout_min}"
                )
            stage.heartbeat(progress_message=f"ray job {submission_id}: {status}")
            time.sleep(POLL_INTERVAL_S)

    logs = _logs_tail(client, submission_id)
    if status != JobStatus.SUCCEEDED:
        ctx.step_log(f"Ray job {submission_id} ended {status}", level="error")
        # NOT PermanentError: a job can fail for transient reasons (a worker
        # died, the object store filled). Let the normal retry budget decide,
        # and note that the retry gets a fresh submission id via _submit_or_attach.
        raise RuntimeError(f"Ray job {submission_id} ended {status}: {logs[-500:]}")

    ctx.step_log(f"Ray job {submission_id} succeeded", level="success")
    return {
        "submission_id": submission_id,
        "status": str(status),
        "logs_tail": logs,
    }


def _stop_quietly(client: Any, submission_id: str, log_fn) -> None:
    """Best-effort terminate — never mask the reason we are unwinding."""
    try:
        client.stop_job(submission_id)
        log_fn(f"Stopped Ray job {submission_id}", level="warning")
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not stop Ray job %s: %s", submission_id, exc)


def _logs_tail(client: Any, submission_id: str) -> str:
    try:
        return client.get_job_logs(submission_id)[-LOGS_TAIL_CHARS:]
    except Exception:  # noqa: BLE001 - logs are diagnostics, never fatal
        return ""


_DISPATCH = {
    f"{NAMESPACE}.SubmitJob": _submit_job_handler,
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


def register_ray_handlers(poller) -> None:
    """Register with an AgentPoller."""
    for fqn, func in _DISPATCH.items():
        poller.register(fqn, func)
