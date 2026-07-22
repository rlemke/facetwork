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
    needs its OWN handler re-run next, but _process_step marks only a step's
    PARENTS dirty (which seed the parents' continuations). It now also marks
    the step's OWN id dirty for self-reprocess states, so the iteration
    boundary seeds a self-continuation instead of waiting for the ~5-min sweep.
    """

    def _ctx_and_step(self, state):
        from unittest.mock import MagicMock
        from facetwork.runtime.evaluator import ExecutionContext
        from facetwork.runtime.persistence import IterationChanges
        from facetwork.runtime.telemetry import Telemetry

        ctx = ExecutionContext(
            persistence=MagicMock(),
            telemetry=Telemetry(enabled=False),
            changes=IterationChanges(),
            workflow_id="wf",
            _dirty_blocks=set(),
        )
        s = StepDefinition.create(
            workflow_id="wf", object_type=ObjectType.VARIABLE_ASSIGNMENT,
            facet_name="ns.F", statement_id="s1", step_uuid="step-1",
            container_id="parent-1",
        )
        s.block_id = "block-1"
        s.state = "state.statement.blocks.Continue"  # state_before
        return ctx, s, state

    def _run(self, monkeypatch, target_state):
        import facetwork.runtime.evaluator as ev_mod
        from facetwork.runtime.changers.base import StateChangeResult

        ctx, s, _ = self._ctx_and_step(target_state)

        def fake_changer(step, context):
            class _C:
                def process(_self):
                    step.state = target_state  # transition happened
                    return StateChangeResult(step=step)
            return _C()

        monkeypatch.setattr(ev_mod, "get_state_changer", fake_changer)
        evaluator = ev_mod.Evaluator(persistence=ctx.persistence, telemetry=ctx.telemetry)
        progressed = evaluator._process_step(s, ctx)
        assert progressed
        return ctx

    def test_catch_begin_marks_self_dirty(self, monkeypatch):
        ctx = self._run(monkeypatch, "state.statement.catch.Begin")
        assert "step-1" in ctx._dirty_blocks   # self-dirty -> gets a continuation
        assert "parent-1" in ctx._dirty_blocks  # parents still dirtied
        assert "block-1" in ctx._dirty_blocks

    def test_catch_continue_marks_self_dirty(self, monkeypatch):
        ctx = self._run(monkeypatch, "state.statement.catch.Continue")
        assert "step-1" in ctx._dirty_blocks

    def test_normal_transition_does_not_self_dirty(self, monkeypatch):
        ctx = self._run(monkeypatch, "state.statement.blocks.End")
        assert "step-1" not in ctx._dirty_blocks   # only parents
        assert "parent-1" in ctx._dirty_blocks

    def test_self_reprocess_states_are_the_catch_states(self):
        from facetwork.runtime.states import SELF_REPROCESS_STATES, StepState
        assert SELF_REPROCESS_STATES == frozenset({
            StepState.CATCH_BEGIN, StepState.CATCH_CONTINUE})
