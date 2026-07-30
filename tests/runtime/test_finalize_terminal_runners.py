"""Runners whose work is finished must not sit at `running` forever.

A workflow can finish without anything observing it. The clearest case is the
declaration-level ``catch``: the catch runs, its yield completes, the Workflow
step reaches Complete — and nothing calls ``resume_step`` again, so
``_update_runner_terminal_state`` never fires. The stuck-step sweep cannot save
it either: once every step is Complete/Error the workflow no longer matches
``STUCK_STEP_STATES``, so the sweep stops selecting it.

Observed live: a catch-handled workflow sat `running` for 105 minutes with every
step and task terminal and nothing left to do.

The negative cases matter as much as the positive one — this must never
terminalise a workflow that is genuinely still working.
"""

from __future__ import annotations

import time

import pytest

try:
    import mongomock

    MONGOMOCK_AVAILABLE = True
except ImportError:
    MONGOMOCK_AVAILABLE = False

needs_mongomock = pytest.mark.skipif(not MONGOMOCK_AVAILABLE, reason="mongomock not installed")

WF = "wf-fin"
RUNNER = "runner-fin"


@pytest.fixture
def store():
    if not MONGOMOCK_AVAILABLE:
        pytest.skip("mongomock not installed")
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_finalize", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _seed(store, *, step_states, task_states, age_ms=3_600_000, root_state=None,
          runner_state="running"):
    now = int(time.time() * 1000)
    store._db.runners.insert_one({
        "uuid": RUNNER,
        "workflow_id": WF,
        "state": runner_state,
        "start_time": now - age_ms,
        "workflow": {"name": "test.Workflow"},
    })
    if root_state:
        store._db.steps.insert_one({
            "uuid": "root", "workflow_id": WF, "state": root_state,
            "object_type": "Workflow", "statement_name": "Workflow",
        })
    for i, st in enumerate(step_states):
        store._db.steps.insert_one({
            "uuid": f"s{i}", "workflow_id": WF, "state": st,
            "object_type": "VariableAssignment", "statement_name": f"s{i}",
        })
    for i, st in enumerate(task_states):
        store._db.tasks.insert_one({
            "uuid": f"t{i}", "workflow_id": WF, "step_id": f"s{i}",
            "runner_id": RUNNER, "flow_id": "f", "name": "T", "state": st,
        })


@needs_mongomock
def test_finalizes_a_runner_whose_work_is_done(store):
    _seed(store, step_states=["state.statement.Complete"], task_states=["completed"],
          root_state="state.statement.Complete")
    out = store.finalize_terminal_runners()
    assert len(out) == 1
    assert out[0]["state"] == "completed"
    assert store._db.runners.find_one({"uuid": RUNNER})["state"] == "completed"


@needs_mongomock
def test_dead_lettered_tasks_count_as_terminal(store):
    """The case that motivated this: a catch-handled failure leaves a dead-letter.

    Omitting dead_letter from the terminal set made the whole pass a no-op for
    exactly the workflows it exists to finalise — caught only by running it
    against the real stuck workflow.
    """
    _seed(store, step_states=["state.statement.Complete", "state.statement.Error"],
          task_states=["completed", "dead_letter"],
          root_state="state.statement.Complete")
    out = store.finalize_terminal_runners()
    assert len(out) == 1, "a dead-lettered task must not block finalization"


@needs_mongomock
def test_root_error_finalizes_as_failed(store):
    _seed(store, step_states=["state.statement.Error"], task_states=["failed"],
          root_state="state.statement.Error")
    out = store.finalize_terminal_runners()
    assert out and out[0]["state"] == "failed"
    assert store._db.runners.find_one({"uuid": RUNNER})["state"] == "failed"


@needs_mongomock
def test_never_touches_a_workflow_with_a_running_task(store):
    _seed(store, step_states=["state.statement.Complete"], task_states=["running"],
          root_state="state.statement.Complete")
    assert store.finalize_terminal_runners() == []
    assert store._db.runners.find_one({"uuid": RUNNER})["state"] == "running"


@needs_mongomock
def test_never_touches_a_workflow_with_a_non_terminal_step(store):
    _seed(store, step_states=["state.statement.blocks.Begin"], task_states=["completed"],
          root_state="state.statement.Complete")
    assert store.finalize_terminal_runners() == []


@needs_mongomock
def test_respects_the_age_window(store):
    """Never race a workflow that just finished a moment ago."""
    _seed(store, step_states=["state.statement.Complete"], task_states=["completed"],
          root_state="state.statement.Complete", age_ms=5_000)
    assert store.finalize_terminal_runners() == []
    assert len(store.finalize_terminal_runners(min_age_ms=1_000)) == 1


@needs_mongomock
def test_ignores_a_runner_with_no_steps(store):
    """A runner that never created a step tells us nothing about completion."""
    _seed(store, step_states=[], task_states=[], root_state=None)
    assert store.finalize_terminal_runners() == []


@needs_mongomock
def test_already_terminal_runners_are_left_alone(store):
    _seed(store, step_states=["state.statement.Complete"], task_states=["completed"],
          root_state="state.statement.Complete", runner_state="completed")
    assert store.finalize_terminal_runners() == []
