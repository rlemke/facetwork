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

``fw maint terminate-workflow`` already marked a run's tasks ``canceled``, but
nothing in flight read that — a long import kept burning CPU, disk and API quota
until it finished on its own. These pin the missing half.

The store predicate is tested in BOTH stores (parity — lessons-learned §23): a
handler must behave the same on either, and the interesting cases (reclaim,
already-terminal) are exactly the ones a single-process test double hides.
"""

from __future__ import annotations

import copy

import pytest

from facetwork.runtime.cancellation import (
    CancellationToken,
    HandlerCancelled,
    never_cancelled,
)
from facetwork.runtime.entities import TaskDefinition, TaskState
from facetwork.runtime.handler_context import HandlerContext
from facetwork.runtime.memory_store import MemoryStore

try:
    import mongomock

    MONGOMOCK = True
except ImportError:
    MONGOMOCK = False


@pytest.fixture(params=["memory", "mongo"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    if not MONGOMOCK:
        pytest.skip("mongomock not installed")
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_cancel", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _task(store, state=TaskState.RUNNING, server_id="server-A"):
    t = TaskDefinition(
        uuid="task-1",
        name="ns.SlowImport",
        runner_id="run-1",
        workflow_id="wf-1",
        flow_id="f-1",
        step_id="step-1",
        state=state,
        server_id=server_id,
    )
    store.save_task(t)
    return t


# --- the store predicate ----------------------------------------------------


def test_running_task_owned_by_us_is_not_cancelled(store):
    _task(store)
    assert store.task_cancellation_reason("task-1", "server-A") is None


def test_canceled_task_reports_a_reason(store):
    """What `fw maint terminate-workflow` writes must be visible in flight."""
    _task(store, state=TaskState.CANCELED)
    reason = store.task_cancellation_reason("task-1", "server-A")
    assert reason and "cancel" in reason.lower()


def test_reclaimed_task_cancels_the_zombie(store):
    """The reaper moved this task to another runner — our result can't land."""
    _task(store, server_id="server-B")
    reason = store.task_cancellation_reason("task-1", "server-A")
    assert reason and "reclaim" in reason.lower()


def test_already_terminal_task_cancels(store):
    """The execution watchdog failed it while the handler kept running."""
    _task(store, state=TaskState.FAILED)
    reason = store.task_cancellation_reason("task-1", "server-A")
    assert reason and "terminal" in reason.lower()


def test_missing_task_cancels(store):
    assert store.task_cancellation_reason("no-such-task", "server-A") is not None


def test_no_server_id_skips_the_ownership_check(store):
    """Callers without a server identity still get operator-cancel detection."""
    _task(store, server_id="server-B")
    assert store.task_cancellation_reason("task-1") is None


# --- the token --------------------------------------------------------------


def test_token_latches_once_cancelled():
    """A transient store read must not let a doomed handler resume work."""
    reasons = iter(["terminated by operator", None, None])
    token = CancellationToken(_check=lambda: next(reasons), poll_interval_s=0)

    assert token.is_cancelled is True
    assert token.is_cancelled is True  # still cancelled, not re-polled to healthy
    assert token.reason == "terminated by operator"


def test_token_caches_between_polls():
    """Handlers check in loops — that must not mean a query per iteration."""
    calls = []

    def _check():
        calls.append(1)
        return None

    token = CancellationToken(_check=_check, poll_interval_s=60)
    for _ in range(50):
        assert token.is_cancelled is False
    assert len(calls) == 1


def test_store_failure_does_not_cancel_a_healthy_handler():
    """A DB blip must not turn into lost work."""

    def _boom():
        raise RuntimeError("mongo unavailable")

    token = CancellationToken(_check=_boom, poll_interval_s=0)
    assert token.is_cancelled is False


def test_raise_if_cancelled():
    token = CancellationToken(_check=lambda: "terminated by operator", poll_interval_s=0)
    with pytest.raises(HandlerCancelled) as exc:
        token.raise_if_cancelled()
    assert exc.value.reason == "terminated by operator"


def test_never_cancelled_is_the_default():
    assert never_cancelled().is_cancelled is False
    never_cancelled().raise_if_cancelled()  # must not raise


# --- handler-facing surface -------------------------------------------------


def test_handler_context_exposes_cancellation():
    ctx = HandlerContext(
        facet_name="ns.SlowImport",
        cancellation=CancellationToken(_check=lambda: "task was canceled", poll_interval_s=0),
    )
    assert ctx.is_cancelled is True
    assert ctx.cancellation_reason == "task was canceled"
    with pytest.raises(HandlerCancelled):
        ctx.raise_if_cancelled()


def test_context_round_trips_through_payload_keys():
    """Handlers using the flat payload keys get the same token as _ctx users."""
    token = CancellationToken(_check=lambda: "task was canceled", poll_interval_s=0)
    original = HandlerContext(facet_name="ns.SlowImport", cancellation=token)

    payload = original.to_payload_keys()
    payload.pop("_ctx")  # force reconstruction from the flat keys
    rebuilt = HandlerContext.from_payload(payload)

    assert rebuilt.is_cancelled is True


def test_injected_payload_stays_json_serializable_after_dropping_callables():
    """The payload contract: every injected value is plain data or a CALLABLE.

    Handlers serialise their payload with
    ``{k: v for k, v in payload.items() if not callable(v)}`` (for logging, or to
    forward it to a subprocess). Injecting the token *object* passed its own
    tests while breaking every such handler, because a dataclass is not callable
    and not JSON-serialisable — so it survived the filter and blew up in
    json.dump. Cancellation therefore travels as ``_cancellation_check``.
    """
    import json

    store = MemoryStore()
    svc = _runner_service(store)
    task = _task(store, server_id=svc._server_id)
    payload = svc._build_handler_payload(task)

    serializable = {k: v for k, v in payload.items() if not callable(v)}
    json.dumps(serializable)  # must not raise

    assert callable(payload["_cancellation_check"])
    assert HandlerContext.from_payload(payload).is_cancelled is False


def test_context_without_injection_is_never_cancelled():
    """A handler run outside a real dispatch must not think it was cancelled."""
    assert HandlerContext.from_payload({}).is_cancelled is False


def test_handler_can_stop_between_units_of_work(store):
    """End to end: the operator cancels mid-run and the handler stops early."""
    _task(store)
    token = CancellationToken(
        _check=lambda: store.task_cancellation_reason("task-1", "server-A"),
        poll_interval_s=0,
    )
    ctx = HandlerContext(facet_name="ns.SlowImport", cancellation=token)

    done = []
    with pytest.raises(HandlerCancelled):
        for i in range(10):
            ctx.raise_if_cancelled()
            done.append(i)
            if i == 2:  # operator runs `fw maint terminate-workflow`
                t = store.get_task("task-1")
                t.state = TaskState.CANCELED
                store.save_task(t)

    assert done == [0, 1, 2], "handler kept working after cancellation"


# --- runner contract --------------------------------------------------------
#
# The runner must treat a cancelled handler as a CLEAN STOP: no retry (the work
# was deliberately abandoned) and no failed step. Getting this wrong is worse
# than not cancelling at all — a cancelled run that retries burns the same
# resources three more times.


def _runner_service(store):
    from facetwork.runtime.agent import ToolRegistry
    from facetwork.runtime.evaluator import Evaluator
    from facetwork.runtime.runner import RunnerConfig, RunnerService

    registry = ToolRegistry()

    def _cancelling_handler(payload):
        HandlerContext.from_payload(payload).raise_if_cancelled()
        return {"output": "should not get here"}

    registry.register("ns.SlowImport", _cancelling_handler)
    svc = RunnerService(store, Evaluator(persistence=store), RunnerConfig(), registry)
    return svc


def test_runner_marks_a_cancelled_handler_canceled_and_does_not_retry():
    store = MemoryStore()
    svc = _runner_service(store)
    task = _task(store, state=TaskState.CANCELED, server_id=svc._server_id)

    svc._process_event_task(task)

    updated = store.get_task("task-1")
    assert updated.state == TaskState.CANCELED
    assert updated.retry_count == 0, "cancellation must not consume a retry"
    assert "cancelled" in (updated.error or {}).get("message", "")


def test_runner_does_not_fail_the_step_on_cancellation():
    """The step belongs to terminate-workflow (or to the new owner) — not to us."""
    store = MemoryStore()
    svc = _runner_service(store)
    task = _task(store, state=TaskState.CANCELED, server_id=svc._server_id)

    failed: list[str] = []
    svc._evaluator.fail_step = lambda step_id, msg="": failed.append(step_id)

    svc._process_event_task(task)

    assert failed == [], "cancellation must not fail the step"


def test_zombie_cancellation_write_is_dropped_by_the_ownership_gate():
    """A reclaimed execution must not cancel the task its successor now owns.

    Models the real reclaim: we claimed the task (so our in-memory copy carries
    OUR server_id), the orphan reaper then reset it and another runner claimed
    it, so the stored row now carries theirs. We notice via the token and stop —
    but our terminal write must not land on their task.
    """
    store = MemoryStore()
    svc = _runner_service(store)

    _task(store, state=TaskState.RUNNING, server_id="server-B")  # the new owner
    # deepcopy: MemoryStore hands back the stored object itself, so mutating a
    # "stale view" in place would rewrite the row we are trying to protect.
    stale = copy.deepcopy(store.get_task("task-1"))
    stale.server_id = svc._server_id  # our view, from when we claimed it

    svc._process_event_task(stale)

    updated = store.get_task("task-1")
    assert updated.state == TaskState.RUNNING, "zombie clobbered the new owner's task"
    assert updated.server_id == "server-B"
