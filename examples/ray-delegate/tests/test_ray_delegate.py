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

"""Ray delegation adapter — the contract, not the cluster.

These run against a fake JobSubmissionClient so CI needs no Ray cluster (and no
ray install). What they pin is the behaviour the design document promises:
derived ids, attach-don't-double-run, terminate-on-cancel, and retry semantics.

A live test against a real cluster is at the bottom, skipped unless
FW_RAY_ADDRESS points at one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "handlers"))

from facetwork.runtime.cancellation import CancellationToken, HandlerCancelled  # noqa: E402
from facetwork.runtime.errors import PermanentError  # noqa: E402
from facetwork.runtime.handler_context import HandlerContext  # noqa: E402

ray_job = pytest.importorskip("ray.job_submission", reason="ray not installed")
import ray_handlers as adapter  # noqa: E402

JobStatus = ray_job.JobStatus
STEP_ID = "8b733944-f2da-5338-96c1-3edffb85a0b1"


class FakeClient:
    """Enough of JobSubmissionClient to exercise the adapter's decisions."""

    def __init__(self, existing: dict[str, object] | None = None, run_for: int = 0):
        self.jobs: dict[str, object] = dict(existing or {})
        self.submitted: list[str] = []
        self.stopped: list[str] = []
        self.run_for = run_for          # polls before a submitted job finishes
        self._polls: dict[str, int] = {}
        self.final = JobStatus.SUCCEEDED

    def submit_job(self, *, entrypoint, submission_id, runtime_env=None):
        if submission_id in self.jobs:
            raise RuntimeError(f"Job with submission_id {submission_id} already exists")
        self.jobs[submission_id] = JobStatus.RUNNING
        self.submitted.append(submission_id)
        return submission_id

    def get_job_status(self, submission_id):
        st = self.jobs[submission_id]
        if st == JobStatus.RUNNING and submission_id in self.submitted:
            n = self._polls.get(submission_id, 0) + 1
            self._polls[submission_id] = n
            if n > self.run_for:
                self.jobs[submission_id] = self.final
        return self.jobs[submission_id]

    def get_job_logs(self, submission_id):
        return f"logs for {submission_id}"

    def stop_job(self, submission_id):
        self.stopped.append(submission_id)
        self.jobs[submission_id] = JobStatus.STOPPED


def _payload(**kw):
    p = {
        "_facet_name": "ray.delegate.SubmitJob",
        "_step_id": STEP_ID,
        "_task_uuid": "task-1",
        "entrypoint": "python job.py",
        "run_timeout_minutes": 5,
    }
    p.update(kw)
    return p


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    monkeypatch.setattr(adapter, "POLL_INTERVAL_S", 0)


def _install(monkeypatch, client):
    monkeypatch.setattr(adapter, "_client", lambda address: client)
    return client


# --- identity and idempotency (§6) -------------------------------------------


def test_submission_id_is_derived_from_the_step_id():
    """Derived, never generated — a redelivered task must compute the same id.

    Under `foreach` a step NAME is a role shared by many instances, so the id is
    the only correct basis.
    """
    assert adapter._external_id(STEP_ID) == f"fw-{STEP_ID}"


def test_redelivery_attaches_instead_of_starting_a_second_run(monkeypatch):
    """The whole point of a derived id: don't pay for the work twice."""
    live = {f"fw-{STEP_ID}": JobStatus.RUNNING}
    client = _install(monkeypatch, FakeClient(existing=live))
    client.jobs[f"fw-{STEP_ID}"] = JobStatus.SUCCEEDED  # finishes while we watch

    out = adapter._submit_job_handler(_payload())

    assert client.submitted == [], "started a second Ray job for a redelivered task"
    assert out["submission_id"] == f"fw-{STEP_ID}"


def test_retry_after_a_failed_attempt_starts_a_fresh_job(monkeypatch):
    """The subtlety a purely step-derived id gets wrong.

    A retry must not attach to the previous attempt's TERMINAL failure and
    inherit it forever — that would make the retry budget meaningless.
    """
    dead = {f"fw-{STEP_ID}": JobStatus.FAILED}
    client = _install(monkeypatch, FakeClient(existing=dead))

    out = adapter._submit_job_handler(_payload(_retry_count=1))

    assert client.submitted == [f"fw-{STEP_ID}-r1"]
    assert out["submission_id"] == f"fw-{STEP_ID}-r1"


def test_completed_job_for_the_same_attempt_is_reused(monkeypatch):
    """Redelivery after success returns the result rather than re-running it."""
    done = {f"fw-{STEP_ID}": JobStatus.SUCCEEDED}
    client = _install(monkeypatch, FakeClient(existing=done))

    out = adapter._submit_job_handler(_payload())

    assert client.submitted == []
    assert out["status"] == str(JobStatus.SUCCEEDED)


# --- terminate propagation (§7.2) --------------------------------------------


def test_cancellation_stops_the_external_job(monkeypatch):
    """The prerequisite delegation was blocked on.

    Without this the workflow is terminated, the handler unwinds, and the Ray
    job keeps running — and billing — with nothing pointing at it.
    """
    client = _install(monkeypatch, FakeClient(run_for=99))  # never finishes on its own
    payload = _payload()
    payload["_cancellation_check"] = lambda: "task was canceled (terminate-workflow)"

    with pytest.raises(HandlerCancelled):
        adapter._submit_job_handler(payload)

    assert client.stopped == [f"fw-{STEP_ID}"], "external job left running after cancellation"


def test_timeout_stops_the_external_job(monkeypatch):
    """Same obligation on the timeout path — don't leak the run."""
    client = _install(monkeypatch, FakeClient(run_for=99))
    # Advance the clock past the deadline instead of waiting a minute. It must
    # not exhaust: ctx.stage() reads the same clock, so more callers than the
    # poll loop pull from it.
    calls = {"n": 0}

    def fake_monotonic():
        # Strictly increasing by a large step, so the deadline is exceeded no
        # matter which caller reads the clock first (ctx.stage() also reads it).
        calls["n"] += 1
        return calls["n"] * 1_000_000.0

    monkeypatch.setattr(adapter.time, "monotonic", fake_monotonic)

    with pytest.raises(TimeoutError):
        adapter._submit_job_handler(_payload(run_timeout_minutes=1))

    assert client.stopped == [f"fw-{STEP_ID}"]


def test_non_positive_timeout_is_rejected_not_silently_defaulted(monkeypatch):
    """`int(x or 120)` would rewrite an explicit 0 into a two-hour wait.

    A workflow that asks for a nonsensical timeout should be told, not quietly
    given a different one — and no retry can fix a bad parameter.
    """
    _install(monkeypatch, FakeClient())
    with pytest.raises(PermanentError):
        adapter._submit_job_handler(_payload(run_timeout_minutes=0))


# --- failure semantics --------------------------------------------------------


def test_failed_job_raises_a_retryable_error_not_a_permanent_one(monkeypatch):
    """A Ray job can fail transiently (a worker died, the object store filled).

    Marking that permanent would turn a blip into lost work; the retry budget
    decides, and the retry gets a fresh submission id.
    """
    client = _install(monkeypatch, FakeClient())
    client.final = JobStatus.FAILED

    with pytest.raises(RuntimeError) as exc:
        adapter._submit_job_handler(_payload())
    assert not isinstance(exc.value, PermanentError)


def test_missing_step_id_is_permanent(monkeypatch):
    """No step id means no stable identity — retrying cannot fix that."""
    _install(monkeypatch, FakeClient())
    with pytest.raises(PermanentError):
        adapter._submit_job_handler(_payload(_step_id=""))


def test_context_without_cancellation_injection_still_runs(monkeypatch):
    """An older runner injects no token; the adapter must not require one."""
    client = _install(monkeypatch, FakeClient())
    out = adapter._submit_job_handler(_payload())
    assert out["status"] == str(JobStatus.SUCCEEDED)
    assert client.stopped == []


def test_handler_context_helpers_are_used_not_bypassed():
    """Guard against the adapter drifting away from the injected context."""
    ctx = HandlerContext.from_payload(
        {"_cancellation_check": lambda: None, "_facet_name": "ray.delegate.SubmitJob"}
    )
    assert isinstance(ctx.cancellation, CancellationToken)
    assert ctx.is_cancelled is False


# --- live cluster (opt-in) ----------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("FW_RAY_LIVE"),
    reason="set FW_RAY_LIVE=1 with a cluster at FW_RAY_ADDRESS to run",
)
def test_live_cluster_round_trip():
    """End to end against a real cluster — the same moves, no fakes."""
    payload = _payload(
        entrypoint="python -c 'print(42)'",
        _step_id=f"live-{os.getpid()}",
        run_timeout_minutes=5,
    )
    out = adapter._submit_job_handler(payload)
    assert out["status"] == str(JobStatus.SUCCEEDED)
    assert "42" in out["logs_tail"]
