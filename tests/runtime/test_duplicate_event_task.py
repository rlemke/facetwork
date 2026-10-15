"""A step must not get a second event task for work already done.

Measured 2026-10-15 in capped ``foreach`` fan-outs: 11 of 666 event tasks got a
SECOND task, created 15-162 ms after the first COMPLETED, often on another
server -- an evaluator committing a copy of the step read before the first task
finished. The handler then ran twice. The guard judges each new event task
against the store BEFORE the commit's own writes.
"""

from __future__ import annotations

import time

import pytest

from facetwork.runtime.entities import TaskDefinition, TaskState
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.persistence import IterationChanges, is_stale_event_task
from facetwork.runtime.states import StepState
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import ObjectType

ET = StepState.EVENT_TRANSMIT
NEXT = StepState.STATEMENT_BLOCKS_BEGIN


# -- the rule ------------------------------------------------------------------

@pytest.mark.parametrize("step_state,step_lm,prior,stale", [
    (None, 0, [], False),                          # step not stored yet
    (ET, 100, [], False),                          # first task for the step
    (ET, 100, [("pending", 50)], True),            # already queued
    (ET, 100, [("running", 50)], True),            # already running
    (NEXT, 100, [("completed", 150)], True),       # result applied, step moved on
    (ET, 100, [("completed", 150)], True),         # the gap before continue_step
    (ET, 200, [("completed", 150)], False),        # reset AFTER it completed: rerun
    (ET, 100, [("failed", 150)], False),           # retry by recreation
    (ET, 100, [("dead_letter", 150)], False),
    (ET, 100, [("canceled", 150)], False),
])
def test_the_rule(step_state, step_lm, prior, stale):
    assert is_stale_event_task(step_state, step_lm, prior) is stale


# -- the stores ------------------------------------------------------------------

def _mongo():
    mongomock = pytest.importorskip("mongomock")
    from facetwork.runtime.mongo_store import MongoStore

    return MongoStore(database_name="afl_test_dup_task", client=mongomock.MongoClient())


@pytest.fixture(params=["memory", "mongo"])
def store(request):
    return MemoryStore() if request.param == "memory" else _mongo()


def _step(store) -> StepDefinition:
    s = StepDefinition.create(workflow_id="wf", object_type=ObjectType.VARIABLE_ASSIGNMENT,
                              facet_name="ns.Work", statement_id="s1", block_id="b1")
    s.state = ET
    s.transition.current_state = ET
    store.save_step(s)
    return store.get_step(s.id)


def _task(step, state=TaskState.PENDING, updated=None) -> TaskDefinition:
    now = int(time.time() * 1000)
    return TaskDefinition(uuid=f"t-{time.time_ns()}", name="ns.Work", runner_id="r",
                          workflow_id="wf", flow_id="", step_id=step.id, state=state,
                          created=now, updated=updated or now, task_list_name="ns", data={})


def _commit_stale_copy(store, copy: StepDefinition) -> None:
    ch = IterationChanges()
    ch.add_updated_step(copy)
    ch.add_created_task(_task(copy))
    store.commit(ch)


def _tasks(store, step):
    return list(store.get_tasks_by_step(step.id))


def test_a_stale_copy_in_the_gap_creates_no_second_task(store):
    step = _step(store)
    stale = step.clone()
    time.sleep(0.005)
    store.save_task(_task(step, TaskState.COMPLETED))     # runner marked it done ...
    _commit_stale_copy(store, stale)                      # ... before continue_step
    assert len(_tasks(store, step)) == 1


def test_a_stale_copy_after_continue_step_neither_adds_a_task_nor_rewinds_the_step(store):
    step = _step(store)
    stale = step.clone()
    time.sleep(0.005)
    store.save_task(_task(step, TaskState.COMPLETED))
    moved = store.get_step(step.id)
    moved.state = NEXT
    moved.transition.current_state = NEXT
    store.save_step(moved)                                # continue_step
    _commit_stale_copy(store, stale)
    assert len(_tasks(store, step)) == 1
    assert store.get_step(step.id).state == NEXT


def test_a_reset_after_completion_gets_a_new_task(store):
    step = _step(store)
    store.save_task(_task(step, TaskState.COMPLETED, updated=int(time.time() * 1000) - 5000))
    time.sleep(0.005)
    reset = store.get_step(step.id)
    store.save_step(reset)                                # retry / re-run / repair
    _commit_stale_copy(store, store.get_step(step.id))
    assert len(_tasks(store, step)) == 2


def test_a_running_task_blocks_a_second(store):
    step = _step(store)
    store.save_task(_task(step, TaskState.RUNNING))
    _commit_stale_copy(store, store.get_step(step.id))
    assert len(_tasks(store, step)) == 1


def test_a_failed_task_is_recreated(store):
    step = _step(store)
    store.save_task(_task(step, TaskState.FAILED))
    _commit_stale_copy(store, store.get_step(step.id))
    assert len(_tasks(store, step)) == 2


def test_the_first_task_of_a_new_step_is_created(store):
    step = StepDefinition.create(workflow_id="wf", object_type=ObjectType.VARIABLE_ASSIGNMENT,
                                 facet_name="ns.Work", statement_id="s2", block_id="b1")
    step.state = ET
    ch = IterationChanges()
    ch.add_created_step(step)
    ch.add_created_task(_task(step))
    store.commit(ch)
    assert len(_tasks(store, step)) == 1
