"""Leaf-stranding regression: a step that defers itself must stay wake-able.

A handler that cannot proceed yet parks the step with ``stay(push=True)`` —
FACET_INIT_BEGIN waiting on a cross-block reference, blocks.Continue waiting on
children. Such a step deliberately does NOT mark a parent dirty (the parent is
waiting on IT, so notifying it only yields a paused re-evaluation). The single
mechanism that re-queues it when its dependency lands is the stuck-sibling scan
in ``Evaluator.resume_step``, which tests ``transition.push_me`` **read back off
the store**.

Two independent defects broke that, and both had to hold for the rescue to work:

1. ``_process_step`` dropped the update for a deferred step (the branch guarded
   on ``not result.continue_processing``), so push_me never reached persistence.
2. ``push_me`` was not part of the persisted step document at all, so it read
   back as False regardless.

The result was a step parked with no wake-up path: nothing re-queued it, and
even a stuck-step sweep over it committed nothing. Survivable on an unbounded
fan-out (a slow tail — the 3,167-county run stranded ~13% and still finished),
but fatal under ``foreach … limit N``: stranded iterations hold every window
slot, admission stops, and the run freezes. See
docs/architecture/ffl-foreach-limit.md.

Pinned in BOTH stores (parity — lessons-learned §23).
"""

from __future__ import annotations

import pytest

from facetwork.runtime.changers.base import StateChangeResult
from facetwork.runtime.evaluator import Evaluator, ExecutionContext
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.persistence import IterationChanges
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

    s = MongoStore(database_name="afl_test_deferred", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _parked_step(workflow_id="wf-1", step_uuid="leaf-1", container_id="parent-1"):
    s = StepDefinition.create(
        workflow_id=workflow_id,
        object_type=ObjectType.VARIABLE_ASSIGNMENT,
        facet_name="ns.Leaf",
        statement_id=f"stmt-{step_uuid}",
        step_uuid=step_uuid,
    )
    s.state = StepState.STATEMENT_BLOCKS_BEGIN
    s.transition.current_state = StepState.STATEMENT_BLOCKS_BEGIN
    s.container_id = container_id
    return s


class _DeferringChanger:
    """Stands in for a handler that defers: stay(push=True), no state change."""

    def __init__(self, step, context=None):
        self.step = step

    def process(self):
        self.step.request_state_change(False)
        self.step.transition.set_push_me(True)
        return StateChangeResult(step=self.step, continue_processing=True)


def _context(store):
    return ExecutionContext(
        persistence=store,
        telemetry=None,
        changes=IterationChanges(),
        workflow_id="wf-1",
        workflow_ast={},
        workflow_defaults={},
        _dirty_blocks=set(),
    )


def test_push_me_round_trips_through_the_store(store):
    """The stuck-sibling scan reads push_me off the store — so it must survive."""
    step = _parked_step()
    step.transition.set_push_me(True)
    store.save_step(step)

    loaded = store.get_step("leaf-1")
    assert loaded.transition.push_me is True, "push_me lost in persistence — rescue scan is blind"


def test_parked_step_is_visible_to_a_container_scan(store):
    """resume_step scans a dirty container's children for push_me steps."""
    step = _parked_step()
    step.transition.set_push_me(True)
    store.save_step(step)

    siblings = list(store.get_steps_by_container("parent-1"))
    parked = [s for s in siblings if not s.is_terminal and s.transition.push_me]
    assert [s.id for s in parked] == ["leaf-1"]


def test_deferred_step_is_recorded_for_persistence(monkeypatch):
    """A newly-parked step must be committed, or push_me never reaches the DB."""
    import facetwork.runtime.evaluator as ev_mod

    monkeypatch.setattr(ev_mod, "get_state_changer", _DeferringChanger)

    store = MemoryStore()
    step = _parked_step()
    store.save_step(step)
    ctx = _context(store)

    progressed = Evaluator(persistence=store)._process_step(step, ctx)

    # It made no forward progress — but it must not be silently dropped.
    assert progressed is False
    assert [s.id for s in ctx.changes.updated_steps] == ["leaf-1"]
    assert ctx.changes.has_changes is True
    # It must NOT dirty its parent: the parent is waiting on this very step, so
    # a notification there only produces a paused re-evaluation (the old loop).
    assert ctx._dirty_blocks == set()


def test_sibling_progress_requeues_a_parked_step(store, monkeypatch):
    """The liveness guarantee, end to end.

    A parked iteration is rescued when a *sibling* lands: the sibling's progress
    dirties their shared container, and resume_step then scans that container's
    children for push_me steps. This is the path that was dead — the scan reads
    the flag off the store, where it never arrived. Without it a parked step has
    no wake-up at all, which is what froze the capped county-atlas fan-out.
    """
    import facetwork.runtime.evaluator as ev_mod

    processed: list[str] = []

    class _Router:
        """Parked child defers; the sibling completes and notifies the parent."""

        def __init__(self, step, context=None):
            self.step = step

        def process(self):
            processed.append(self.step.id)
            if self.step.id == "leaf-parked":
                self.step.request_state_change(False)
                self.step.transition.set_push_me(True)
                return StateChangeResult(step=self.step, continue_processing=True)
            self.step.state = StepState.STATEMENT_COMPLETE
            self.step.transition.current_state = StepState.STATEMENT_COMPLETE
            self.step.transition.changed = True
            return StateChangeResult(step=self.step)

    monkeypatch.setattr(ev_mod, "get_state_changer", _Router)

    parked = _parked_step(step_uuid="leaf-parked", container_id="parent-1")
    parked.transition.set_push_me(True)
    store.save_step(parked)

    sibling = _parked_step(step_uuid="leaf-sibling", container_id="parent-1")
    sibling.transition.set_push_me(False)
    store.save_step(sibling)

    Evaluator(persistence=store).resume_step(
        workflow_id_val="wf-1",
        step_id="leaf-sibling",
        workflow_ast={},
    )

    assert "leaf-sibling" in processed
    assert "leaf-parked" in processed, (
        "parked step was never re-queued after its sibling progressed — "
        "it has no wake-up path and the fan-out stalls"
    )


def test_already_parked_step_is_not_rewritten(monkeypatch):
    """Persist on the False→True edge only.

    Every sweep re-processes parked steps. Re-committing each time would be a
    write storm across the fleet and a needless optimistic-concurrency conflict
    source, so a step that is already flagged must produce no further update.
    """
    import facetwork.runtime.evaluator as ev_mod

    monkeypatch.setattr(ev_mod, "get_state_changer", _DeferringChanger)

    store = MemoryStore()
    step = _parked_step()
    step.transition.set_push_me(True)  # already parked, as read back from the store
    store.save_step(step)
    ctx = _context(store)

    progressed = Evaluator(persistence=store)._process_step(step, ctx)

    assert progressed is False
    assert ctx.changes.updated_steps == []
    assert ctx.changes.has_changes is False
