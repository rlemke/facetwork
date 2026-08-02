"""Repair check 7 — stranded block steps.

The failure mode none of checks 1-6 intersect, observed live on the national
county-atlas fan-out: 3,290 of 3,290 tasks ``completed``, 9,036 steps
``Complete``, and 314 steps left at ``blocks.Begin`` / ``block.execution.Continue``
for **49 hours**, with the runner still ``running``.

`repair-workflow` reported *"No issues found"* because every existing check looks
somewhere else: the runner was legitimately ``running`` (check 1 only fires on
completed/failed), nothing had errored (3, 4, 6), and no task was orphaned or
dead-lettered (2, 5). A direct ``resume_step`` cleared all 312 remaining steps in
one pass, so the fix is mechanical — it just had no check to trigger it.

Detection must be conservative, so the negative cases matter as much as the
positive one:

* a workflow with ANY non-terminal task is still doing handler work — checks 3/5's
  territory, not this one;
* ``EventTransmit`` means "awaiting a task", never "block failed to cascade";
* a step modified seconds ago may be a live cascade mid-flight.
"""

from __future__ import annotations

import pytest

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

needs_mongomock = pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")

WF = "wf-stranded"
RUNNER = "runner-stranded"


@pytest.fixture
def store():
    if not MONGOMOCK_AVAILABLE:
        pytest.skip("mongomock not installed")
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_repair_stranded", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _seed(store, *, step_states, task_states, step_age_ms=3_600_000):
    """Insert a runner + steps + tasks directly, mimicking the observed shape."""
    now = _now_ms()
    store._db.runners.insert_one(
        {
            "uuid": RUNNER,
            "workflow_id": WF,
            "state": "running",
            "workflow": {
                "uuid": WF,
                "name": "county.atlas.workflows.BuildAtlasFanout",
                "namespace_id": "cli",
                "facet_id": WF,
                "flow_id": "flow-stranded",
                "starting_step": "",
                "version": "1.0",
                "metadata": None,
                "documentation": None,
                "date": 0,
            },
            "start_time": now - step_age_ms,
        }
    )
    for i, st in enumerate(step_states):
        store._db.steps.insert_one(
            {
                "uuid": f"step-{i}",
                "workflow_id": WF,
                "state": st,
                "statement_name": f"s{i}",
                "object_type": "VariableAssignment",
                "last_modified": now - step_age_ms,
                "start_time": now - step_age_ms,
            }
        )
    for i, st in enumerate(task_states):
        store._db.tasks.insert_one(
            {
                "uuid": f"task-{i}",
                "workflow_id": WF,
                "step_id": f"step-{i}",
                "runner_id": RUNNER,
                "flow_id": "flow-stranded",
                "name": "county.atlas.BuildCountyAtlas",
                "state": st,
                "created": now - step_age_ms,
            }
        )


@needs_mongomock
def test_detects_stranded_block_steps_when_all_tasks_are_terminal(store):
    """The county-atlas shape: work done, blocks never cascaded."""
    _seed(
        store,
        step_states=[
            "state.statement.Complete",
            "state.statement.blocks.Begin",
            "state.block.execution.Continue",
        ],
        task_states=["completed", "completed", "completed"],
    )
    result = store.repair_workflow(RUNNER, dry_run=True)
    stranded = result["stranded_block_steps"]
    assert len(stranded) == 2, stranded
    assert {s["state"] for s in stranded} == {
        "state.statement.blocks.Begin",
        "state.block.execution.Continue",
    }
    # Checks 1-6 must still find nothing — that is the whole point of check 7.
    assert not result["runner_reset"]
    assert result["orphaned_tasks_reset"] == []
    assert result["transient_steps_retried"] == []
    assert result.get("dead_letter_tasks_reset", []) == []
    assert result.get("inconsistent_steps_reset", []) == []


@needs_mongomock
def test_does_not_fire_while_a_task_is_still_running(store):
    """A live workflow must never be touched — that is checks 3/5's territory."""
    _seed(
        store,
        step_states=["state.statement.blocks.Begin", "state.event.EventTransmit"],
        task_states=["running", "pending"],
    )
    result = store.repair_workflow(RUNNER, dry_run=True)
    assert result["stranded_block_steps"] == []


@needs_mongomock
def test_event_transmit_is_never_reported_as_stranded(store):
    """EventTransmit means 'awaiting a task', not 'block failed to cascade'."""
    _seed(
        store,
        step_states=["state.event.EventTransmit"],
        task_states=["completed"],
    )
    result = store.repair_workflow(RUNNER, dry_run=True)
    assert result["stranded_block_steps"] == []


@needs_mongomock
def test_recent_steps_are_left_alone(store):
    """A step modified seconds ago may be a cascade in flight, not a stall."""
    _seed(
        store,
        step_states=["state.statement.blocks.Begin"],
        task_states=["completed"],
        step_age_ms=5_000,
    )
    result = store.repair_workflow(RUNNER, dry_run=True)
    assert result["stranded_block_steps"] == []
    # …but the same state does report once it is genuinely old.
    result = store.repair_workflow(RUNNER, dry_run=True, stranded_min_age_ms=1_000)
    assert len(result["stranded_block_steps"]) == 1


@needs_mongomock
def test_no_tasks_at_all_is_not_reported(store):
    """A workflow that never created a task tells us nothing about cascading."""
    _seed(store, step_states=["state.statement.blocks.Begin"], task_states=[])
    result = store.repair_workflow(RUNNER, dry_run=True)
    assert result["stranded_block_steps"] == []
