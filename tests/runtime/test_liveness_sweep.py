"""Liveness-stall root-cause regression: catch-stranded steps must be swept.

The continuation-chain liveness stall was root-caused (via a read-only
forensic probe on a frozen production stall) to a step stranded at
CATCH_BEGIN: the CATCH_* states were absent from the stuck-step sweep's
query set, so such a step recovered only when a parent happened to
re-notify it — scale-fragile and paced by sweep cadence. The fix defines a
canonical STUCK_STEP_STATES (including CATCH_BEGIN/CATCH_CONTINUE) that every
sweep site queries. These tests pin it in BOTH stores (parity —
lessons-learned §23).
"""

from __future__ import annotations

import pytest

from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.states import StepState
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import ObjectType

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

    s = MongoStore(database_name="afl_test_sweep", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _step(wid, sid, state):
    s = StepDefinition.create(
        workflow_id=wid,
        object_type=ObjectType.VARIABLE_ASSIGNMENT,
        facet_name="ns.Risky",
        statement_id=f"stmt-{sid}",
        step_uuid=sid,
    )
    s.state = state
    s.transition.current_state = state
    return s


class TestCatchStatesAreSwept:
    def test_catch_begin_is_stuck_and_selects_workflow(self, store):
        store.save_step(_step("wf-catch", "cb-1", StepState.CATCH_BEGIN))
        assert [x.id for x in store.get_stuck_steps_for_workflow("wf-catch")] == ["cb-1"]
        assert "wf-catch" in store.get_pending_resume_workflow_ids()

    def test_catch_continue_is_stuck(self, store):
        store.save_step(_step("wf-cc", "cc-1", StepState.CATCH_CONTINUE))
        assert [x.id for x in store.get_stuck_steps_for_workflow("wf-cc")] == ["cc-1"]
        assert "wf-cc" in store.get_pending_resume_workflow_ids()

    def test_prior_blocking_states_still_swept(self, store):
        # No regression: the pre-existing stuck states remain covered.
        for i, st in enumerate((
            StepState.EVENT_TRANSMIT,
            StepState.STATEMENT_BLOCKS_CONTINUE,
            StepState.BLOCK_EXECUTION_CONTINUE,
        )):
            s = _step("wf-prior", f"p{i}", st)
            if st == StepState.EVENT_TRANSMIT:
                s.transition.request_transition = True  # only "requesting" ET is stuck
            store.save_step(s)
        stuck = {x.id for x in store.get_stuck_steps_for_workflow("wf-prior")}
        assert stuck == {"p0", "p1", "p2"}

    def test_terminal_step_not_swept(self, store):
        store.save_step(_step("wf-done", "done-1", StepState.STATEMENT_COMPLETE))
        assert store.get_stuck_steps_for_workflow("wf-done") == []
        assert "wf-done" not in store.get_pending_resume_workflow_ids()


class TestCatchSelfContinuation:
    """Latency follow-up: a step that transitions IN PLACE into a catch state
    needs its OWN handler re-run, but ordinary continuations only target a
    step's parents. generate_continuation_events now seeds a self-continuation
    for catch-state updates, so recovery no longer waits for the sweep cycle.
    """

    def _changes_with_updated_step(self, state):
        from facetwork.runtime.persistence import IterationChanges

        changes = IterationChanges()
        s = StepDefinition.create(
            workflow_id="wf",
            object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.F",
            statement_id="s1",
            step_uuid="step-1",
            container_id="parent-1",
        )
        s.state = state
        changes.add_updated_step(s)
        return changes, s

    def test_catch_begin_gets_self_continuation(self):
        from facetwork.runtime.continuation import generate_continuation_events
        from facetwork.runtime.states import StepState

        changes, s = self._changes_with_updated_step(StepState.CATCH_BEGIN)
        generate_continuation_events(changes)
        targets = {t.step_id: t.data["reason"] for t in changes.continuation_tasks}
        # self-continuation for the catch step itself...
        assert targets.get("step-1") == "catch_reprocess"
        # ...and still a normal parent continuation (to re-check the child)
        assert targets.get("parent-1") == "child_progress"

    def test_catch_continue_gets_self_continuation(self):
        from facetwork.runtime.continuation import generate_continuation_events
        from facetwork.runtime.states import StepState

        changes, s = self._changes_with_updated_step(StepState.CATCH_CONTINUE)
        generate_continuation_events(changes)
        assert any(t.step_id == "step-1" and t.data["reason"] == "catch_reprocess"
                   for t in changes.continuation_tasks)

    def test_non_catch_update_gets_no_self_continuation(self):
        from facetwork.runtime.continuation import generate_continuation_events
        from facetwork.runtime.states import StepState

        changes, s = self._changes_with_updated_step(StepState.STATEMENT_BLOCKS_CONTINUE)
        generate_continuation_events(changes)
        # only the parent, never a self-continuation for a normal update
        assert not any(t.step_id == "step-1" for t in changes.continuation_tasks)
        assert any(t.step_id == "parent-1" for t in changes.continuation_tasks)

    def test_self_continuation_deduped(self):
        from facetwork.runtime.continuation import generate_continuation_events
        from facetwork.runtime.states import StepState

        changes, s = self._changes_with_updated_step(StepState.CATCH_BEGIN)
        generate_continuation_events(changes)
        generate_continuation_events(changes)  # idempotent
        self_conts = [t for t in changes.continuation_tasks if t.step_id == "step-1"]
        assert len(self_conts) == 1
