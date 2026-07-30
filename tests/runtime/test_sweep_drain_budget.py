"""The sweep drains a workflow that has no live tasks left.

The per-invocation budget (25 steps / 1500 ms) exists to prevent the runtime.md
§10.4 livelock: the sweep runs synchronously on the poll thread, so an unbounded
pass over a wide fan-out starves event-task claiming.

That reasoning does not apply when a workflow's tasks are ALL terminal — there is
nothing left for it to claim, so sweeping it cannot starve its own work. Under the
small budget such a backlog crawls: measured 0.45 s per resume on a 9,505-step
workflow, i.e. ~3 steps per 5-minute sweep, ~9 hours for a 314-step backlog.
Measured after the change: 60 stranded steps drained in ~45 s.
"""

from __future__ import annotations

import facetwork.runtime.runner.service as svc


def test_drain_budget_is_much_larger_than_the_shared_one():
    assert svc._SWEEP_DRAIN_MAX_STEPS > svc._SWEEP_MAX_STEPS
    assert svc._SWEEP_DRAIN_MAX_MS > svc._SWEEP_MAX_MS


def test_drain_budget_is_still_bounded():
    """It must not become 'unbounded' — one workflow cannot own the poll thread."""
    assert svc._SWEEP_DRAIN_MAX_STEPS <= 1000
    assert svc._SWEEP_DRAIN_MAX_MS <= 60_000


def test_budget_exhausts_on_step_count():
    b = svc._SweepBudget(2, svc._current_time_ms() + 60_000)
    assert not b.exhausted()
    b.consume()
    b.consume()
    assert b.exhausted()


def test_budget_exhausts_on_deadline():
    b = svc._SweepBudget(1000, svc._current_time_ms() - 1)
    assert b.exhausted(), "a passed deadline must exhaust the budget regardless of steps"
