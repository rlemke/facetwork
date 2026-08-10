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

"""No external reclaim path may fire before the owner's own bound.

Three mechanisms can take a running task away from the runner executing it:
lease expiry, the per-task stuck reap, and the default stuck reap. Each must
sit a grace period ABOVE the owning runner's own deadline, so the owner always
resets its own wedged task first. Otherwise the task returns to the pool while
its handler is still running and a second runner executes the same work —
duplicated hours for a PBF import, or a corrupt one.

The lease side is covered in test_lease_clamp.py. This file covers the two
stuck-reap paths, whose ordering was previously unarticulated:

* **Pass 1** compared against the *same* ``timeout_ms`` the owning runner
  dispatches on, so the reap and the owner's own timeout fired together.
* **Pass 2** used ``FW_STUCK_TIMEOUT_MS`` with no relationship to
  ``FW_TASK_EXECUTION_TIMEOUT_MS``. The stock defaults (30min vs 15min) happen
  to be ordered correctly; a deployment raising the execution timeout without
  raising the stuck timeout is not.

See docs/thesis/paper-timeout-interactions.md §3.
"""

from __future__ import annotations

import pytest

from facetwork.runtime.mongo_store.base import BaseMixin

try:
    import mongomock

    MONGOMOCK = True
except ImportError:
    MONGOMOCK = False

pytestmark = pytest.mark.skipif(not MONGOMOCK, reason="mongomock not installed")

MIN = 60_000
GRACE = BaseMixin.RECLAIM_GRACE_MS


@pytest.fixture
def store(monkeypatch):
    monkeypatch.delenv("FW_TASK_EXECUTION_TIMEOUT_MS", raising=False)
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_grace", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _running_task(store, uuid, *, timeout_ms, age_ms):
    """A running task whose last activity was ``age_ms`` ago."""
    from facetwork.runtime.mongo_store.base import _current_time_ms

    now = _current_time_ms()
    store._db.tasks.insert_one(
        {
            "uuid": uuid,
            "name": "ns.Slow",
            "state": "running",
            "server_id": "server-A",
            "step_id": f"step-{uuid}",
            "workflow_id": "wf-1",
            "timeout_ms": timeout_ms,
            "updated": now - age_ms,
            "task_heartbeat": now - age_ms,
        }
    )


def _reaped(store, **kw) -> set[str]:
    return {r["step_id"].removeprefix("step-") for r in store.reap_stuck_tasks(**kw)}


# --- Pass 1: explicit per-task timeout ---------------------------------------


def test_task_at_its_own_timeout_is_not_reaped(store):
    """The owner is timing out right now — the reaper must not race it."""
    _running_task(store, "t1", timeout_ms=10 * MIN, age_ms=10 * MIN + 1000)
    assert _reaped(store) == set(), "reaper fired at the owner's own deadline"


def test_task_past_timeout_plus_grace_is_reaped(store):
    """Grace is a delay, not an exemption: the backstop still fires."""
    _running_task(store, "t1", timeout_ms=10 * MIN, age_ms=10 * MIN + GRACE + 1000)
    assert _reaped(store) == {"t1"}


def test_task_well_inside_its_timeout_is_untouched(store):
    _running_task(store, "t1", timeout_ms=10 * MIN, age_ms=1 * MIN)
    assert _reaped(store) == set()


# --- Pass 2: default stuck timeout -------------------------------------------


def test_default_stuck_timeout_is_clamped_above_the_execution_timeout(store, monkeypatch):
    """The unarticulated ordering: stuck timeout vs execution timeout.

    A deployment raising the execution timeout to 4h while leaving the stuck
    timeout at 30min would otherwise reap tasks its owners are still running.
    """
    monkeypatch.setenv("FW_TASK_EXECUTION_TIMEOUT_MS", str(4 * 60 * MIN))
    # Idle for 45min: past the requested 30min default, but far inside the
    # 4h execution timeout the owner is working to.
    _running_task(store, "t1", timeout_ms=0, age_ms=45 * MIN)
    assert _reaped(store, default_stuck_ms=30 * MIN) == set(), (
        "reaped a task its owner was still legitimately executing"
    )


def test_default_stuck_timeout_still_fires_past_the_clamped_floor(store, monkeypatch):
    monkeypatch.setenv("FW_TASK_EXECUTION_TIMEOUT_MS", str(4 * 60 * MIN))
    _running_task(store, "t1", timeout_ms=0, age_ms=4 * 60 * MIN + GRACE + MIN)
    assert _reaped(store, default_stuck_ms=30 * MIN) == {"t1"}


def test_generous_explicit_default_is_respected(store):
    """max(), not override: a stuck timeout above the floor is kept."""
    _running_task(store, "t1", timeout_ms=0, age_ms=45 * MIN)
    assert _reaped(store, default_stuck_ms=8 * 60 * MIN) == set()


def test_stock_defaults_remain_ordered(store):
    """30min stuck vs 15min execution: correct already, and must stay so."""
    _running_task(store, "t1", timeout_ms=0, age_ms=20 * MIN)
    assert _reaped(store, default_stuck_ms=30 * MIN) == set()
    store._db.tasks.delete_many({})
    _running_task(store, "t2", timeout_ms=0, age_ms=35 * MIN)
    assert _reaped(store, default_stuck_ms=30 * MIN) == {"t2"}


def test_reported_timeout_is_the_threshold_actually_applied(store, monkeypatch):
    """An operator reading the reap log must see the effective value."""
    monkeypatch.setenv("FW_TASK_EXECUTION_TIMEOUT_MS", str(4 * 60 * MIN))
    _running_task(store, "t1", timeout_ms=0, age_ms=5 * 60 * MIN)
    [row] = store.reap_stuck_tasks(default_stuck_ms=30 * MIN)
    assert int(row["timeout_ms"]) == 4 * 60 * MIN + GRACE
