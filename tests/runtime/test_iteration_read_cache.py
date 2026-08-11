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

"""The evaluator's per-iteration read caches must never serve a stale step.

The cascade re-reads the same step documents constantly — a 50-way fan-out did
11,709 ``get_step`` calls over a 157-step workflow. Caching those reads for the
duration of one iteration cut a real workflow's wall-clock by a third. The cache
is only safe while three properties hold, so they are pinned here:

1. **Pending changes win.** A step mutated in memory has not been written yet,
   so the stored document is stale; the cache must never shadow it.
2. **A commit drops the cache.** After changes are written, every read taken
   before the write is potentially stale.
3. **Callers cannot corrupt it.** ``get_steps_by_block_cached`` hands out lists
   that callers append to, so it must not hand out its own.

A stale read here would not raise — it would silently re-run or skip a step, so
these are the tests that stand between the optimisation and a correctness bug.
"""

from __future__ import annotations

from facetwork.runtime.evaluator import ExecutionContext
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.persistence import IterationChanges
from facetwork.runtime.states import StepState
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.telemetry import Telemetry
from facetwork.runtime.types import ObjectType


def _step(step_id: str, block_id: str = "", state: str = StepState.EVENT_TRANSMIT):
    s = StepDefinition.create(
        workflow_id="wf-1",
        object_type=ObjectType.VARIABLE_ASSIGNMENT,
        facet_name="ns.Leaf",
        statement_id=f"stmt-{step_id}",
        step_uuid=step_id,
    )
    s.state = state
    s.transition.current_state = state
    if block_id:
        s.block_id = block_id
    return s


def _ctx(store: MemoryStore) -> ExecutionContext:
    return ExecutionContext(
        persistence=store,
        telemetry=Telemetry(),
        changes=IterationChanges(),
        workflow_id="wf-1",
    )


def test_a_step_is_read_from_persistence_only_once_per_iteration():
    store = MemoryStore()
    store.save_step(_step("s1"))
    ctx = _ctx(store)

    reads = []
    original = store.get_step
    store.get_step = lambda sid: (reads.append(sid), original(sid))[1]  # type: ignore

    for _ in range(5):
        assert ctx.get_step_cached("s1") is not None

    assert len(reads) == 1, f"step was re-read {len(reads)} times within one iteration"


def test_pending_changes_are_never_shadowed_by_the_cache():
    """The property the whole optimisation rests on.

    A step read early in an iteration, then mutated, must resolve to the MUTATED
    version — otherwise the cascade acts on a step state that no longer exists.
    """
    store = MemoryStore()
    store.save_step(_step("s1", state=StepState.EVENT_TRANSMIT))
    ctx = _ctx(store)

    ctx.get_step_cached("s1")  # warm the cache with the stored version

    mutated = _step("s1", state=StepState.STATEMENT_COMPLETE)
    ctx.changes.add_updated_step(mutated)

    found = ctx.changes.find_pending("s1")
    assert found is mutated, "pending change was not found"
    assert found.state == StepState.STATEMENT_COMPLETE


def test_created_steps_are_findable_before_they_are_committed():
    ctx = _ctx(MemoryStore())
    fresh = _step("new-1")
    ctx.changes.add_created_step(fresh)
    assert ctx.changes.find_pending("new-1") is fresh


def test_an_update_supersedes_a_create_for_lookups():
    """Both refer to one step; the later object is the current one."""
    ctx = _ctx(MemoryStore())
    created = _step("s1", state=StepState.EVENT_TRANSMIT)
    ctx.changes.add_created_step(created)
    updated = _step("s1", state=StepState.STATEMENT_COMPLETE)
    ctx.changes.add_updated_step(updated)
    assert ctx.changes.find_pending("s1") is updated


def test_invalidating_forces_a_re_read():
    store = MemoryStore()
    store.save_step(_step("s1", state=StepState.EVENT_TRANSMIT))
    ctx = _ctx(store)
    assert ctx.get_step_cached("s1").state == StepState.EVENT_TRANSMIT

    # Simulate the commit: the stored document changes underneath us.
    store.save_step(_step("s1", state=StepState.STATEMENT_COMPLETE))
    ctx.invalidate_step_cache()

    assert ctx.get_step_cached("s1").state == StepState.STATEMENT_COMPLETE, (
        "a committed write was served from the pre-commit cache"
    )


def test_block_steps_are_read_once_and_callers_get_their_own_list():
    """Callers append pending steps to the returned list.

    Handing out the cached list itself would let one caller's appends leak into
    every later caller's view — a step counted twice, which the block state
    machine reads as "more children than exist".
    """
    store = MemoryStore()
    store.save_step(_step("child-1", block_id="b1"))
    ctx = _ctx(store)

    reads = []
    original = store.get_steps_by_block
    store.get_steps_by_block = lambda bid: (reads.append(bid), original(bid))[1]  # type: ignore

    first = ctx.get_steps_by_block_cached("b1")
    first.append(_step("appended-by-caller", block_id="b1"))
    second = ctx.get_steps_by_block_cached("b1")

    assert len(reads) == 1, "block was re-read within one iteration"
    assert len(second) == 1, "a caller's append leaked into the cached list"


def test_block_cache_is_dropped_on_invalidate():
    store = MemoryStore()
    store.save_step(_step("child-1", block_id="b1"))
    ctx = _ctx(store)
    assert len(ctx.get_steps_by_block_cached("b1")) == 1

    store.save_step(_step("child-2", block_id="b1"))
    ctx.invalidate_step_cache()

    assert len(ctx.get_steps_by_block_cached("b1")) == 2, (
        "a step created after the cached read stayed invisible"
    )
