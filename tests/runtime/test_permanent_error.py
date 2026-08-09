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

"""Non-retryable handler failures (lessons-learned §24 corollary).

The retry contract is built for *transient* faults and cannot tell them from a
deterministic one, so a handler failing on bad input rode the full budget: five
attempts with exponential backoff, each re-running the same doomed work, before
dead-lettering with the answer it had the first time. On a wide fan-out that
multiplies — the emergency-atlas run burned 150 route tasks x 5 attempts on
regions whose road network genuinely had no motorway tier.

``PermanentError`` is the escape hatch. The contract, pinned here:

* dead-letter immediately, without consuming the retry budget;
* still fail the step, so ``catch`` blocks and error propagation run exactly as
  they would at the end of the budget;
* do NOT count it against the facet's circuit breaker — a permanent error is a
  fact about the input, not the handler's health, and a few unsupported items in
  a fan-out must not stop a healthy facet from being claimed.
"""

from __future__ import annotations

import pytest

from facetwork.runtime.agent import ToolRegistry
from facetwork.runtime.entities import TaskDefinition, TaskState
from facetwork.runtime.errors import PermanentError
from facetwork.runtime.evaluator import Evaluator
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.runner import RunnerConfig, RunnerService


def _task(store, name="ns.PickyHandler", server_id=""):
    t = TaskDefinition(
        uuid="task-1",
        name=name,
        runner_id="run-1",
        workflow_id="wf-1",
        flow_id="f-1",
        step_id="step-1",
        state=TaskState.RUNNING,
        server_id=server_id,
    )
    store.save_task(t)
    return t


def _service(store, exc):
    registry = ToolRegistry()

    def _handler(payload):
        raise exc

    registry.register("ns.PickyHandler", _handler)
    return RunnerService(store, Evaluator(persistence=store), RunnerConfig(), registry)


def test_permanent_error_dead_letters_without_retrying():
    store = MemoryStore()
    svc = _service(store, PermanentError("region 'Atlantis' is not supported"))
    task = _task(store, server_id=svc._server_id)

    svc._process_event_task(task)

    updated = store.get_task("task-1")
    assert updated.state == TaskState.DEAD_LETTER
    assert updated.retry_count == 0, "a permanent failure must not consume the retry budget"
    assert "Atlantis" in updated.error["message"]
    assert updated.error.get("permanent") is True


def test_transient_error_still_retries():
    """The escape hatch must not change the default: ordinary errors retry."""
    store = MemoryStore()
    svc = _service(store, ConnectionResetError("peer went away"))
    task = _task(store, server_id=svc._server_id)

    svc._process_event_task(task)

    updated = store.get_task("task-1")
    assert updated.state == TaskState.PENDING
    assert updated.retry_count == 1


def test_permanent_error_fails_the_step():
    """catch blocks and error propagation must still run."""
    store = MemoryStore()
    svc = _service(store, PermanentError("bad input"))
    task = _task(store, server_id=svc._server_id)

    failed: list[tuple[str, str]] = []
    svc._evaluator.fail_step = lambda step_id, msg="", **kw: failed.append((step_id, msg))

    svc._process_event_task(task)

    assert [s for s, _ in failed] == ["step-1"]


def test_permanent_error_does_not_open_the_circuit_breaker():
    """A few unsupported items must not stop the facet being claimed.

    The breaker exists for a BROKEN HANDLER. A permanent error says the input is
    wrong, so counting it would let bad data in a fan-out take a healthy facet
    out of service for every other item.
    """
    store = MemoryStore()
    svc = _service(store, PermanentError("unsupported region"))

    for i in range(5):  # more than enough consecutive failures to trip it
        t = _task(store, server_id=svc._server_id)
        t.uuid = f"task-{i}"
        store.save_task(t)
        svc._process_event_task(t)

    assert svc._circuit_breakers.is_allowed("ns.PickyHandler") is True, (
        "permanent errors tripped the circuit breaker"
    )


def test_transient_errors_do_open_the_circuit_breaker():
    """Contrast: the breaker must still protect against an actually broken handler."""
    store = MemoryStore()
    svc = _service(store, RuntimeError("handler is broken"))

    for i in range(6):
        t = _task(store, server_id=svc._server_id)
        t.uuid = f"task-{i}"
        store.save_task(t)
        svc._process_event_task(t)

    assert svc._circuit_breakers.is_allowed("ns.PickyHandler") is False, (
        "a repeatedly failing handler should trip it"
    )


def test_importable_from_the_handler_context_module():
    """Handlers get one import site for handler-facing control flow."""
    from facetwork.runtime.handler_context import HandlerCancelled
    from facetwork.runtime.handler_context import PermanentError as PE

    assert PE is PermanentError
    assert issubclass(HandlerCancelled, Exception)


@pytest.mark.parametrize("runner_module", ["registry_runner", "agent_poller", "runner.service"])
def test_every_runner_handles_permanent_error(runner_module):
    """Parity: all three dispatch sites must implement the same contract.

    They have drifted before — a fix applied to one runner and not the others is
    a recurring source of "works on my runner" behaviour.
    """
    import importlib

    mod = importlib.import_module(f"facetwork.runtime.{runner_module}")
    src = open(mod.__file__).read()
    assert "except PermanentError" in src, f"{runner_module} does not handle PermanentError"
