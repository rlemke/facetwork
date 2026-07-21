"""Liveness-investigation regressions: sequence monotonicity + CAS self-wake.

Root cause of the continuation-chain stalls (see lessons-learned): direct
``save_step`` replaced the whole document with the caller's in-memory
sequence, silently REGRESSING the optimistic-concurrency counter; racing
batch writers then dropped legitimate transitions on a false "re-derives on
next poll" promise. These tests pin the three-part fix: (1) saves advance
the stored sequence monotonically from the STORE's value in BOTH stores,
(2) a CAS-dropped batch write enqueues a self-wake continuation, (3) the
wake dedupes against an existing pending continuation.
"""

from __future__ import annotations

import pytest

from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.persistence import IterationChanges
from facetwork.runtime.step import StepDefinition
from facetwork.runtime.types import ObjectType, VersionInfo

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

    s = MongoStore(database_name="afl_test_liveness", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _step(sid="s-1", seq=0):
    st = StepDefinition.create(
        workflow_id="wf-1",
        object_type=ObjectType.VARIABLE_ASSIGNMENT,
        facet_name="ns.F",
        statement_id="stmt-1",
        step_uuid=sid,
    )
    st.version = VersionInfo(sequence=seq)
    return st


class TestSequenceMonotonicity:
    def test_save_advances_from_stored_value(self, store):
        store.save_step(_step(seq=0))
        assert store.get_step("s-1").version.sequence == 1
        store.save_step(_step(seq=0))  # stale in-memory copy
        assert store.get_step("s-1").version.sequence == 2  # NOT regressed

    def test_stale_caller_cannot_regress(self, store):
        """The historic bug: a resume path bumped the step to seq=5; a direct
        save from a caller holding seq=0 rewrote the document at seq=0,
        resetting the CAS baseline and losing the ordering."""
        for _ in range(5):
            store.save_step(_step(seq=0))
        assert store.get_step("s-1").version.sequence == 5
        stale = _step(seq=0)  # e.g. continue_step holding an old copy
        store.save_step(stale)
        assert store.get_step("s-1").version.sequence == 6


class TestConflictSelfWake:
    """Mongo-only: the CAS branch lives in the batch commit path."""

    @pytest.fixture
    def mongo(self):
        if not MONGOMOCK:
            pytest.skip("mongomock not installed")
        from facetwork.runtime.mongo_store import MongoStore

        s = MongoStore(database_name="afl_test_selfwake", client=mongomock.MongoClient())
        yield s
        s.drop_database()
        s.close()

    def _commit_update(self, mongo, step):
        changes = IterationChanges()
        changes.add_updated_step(step)
        mongo.commit(changes)

    def test_dropped_write_enqueues_wakeup(self, mongo):
        # DB advances to seq=3 via direct saves.
        for _ in range(3):
            mongo.save_step(_step(seq=0))
        # A batch writer holding a stale copy tries to commit at seq=2.
        stale = _step(seq=2)
        self._commit_update(mongo, stale)
        # The write was dropped — but a self-wake continuation now exists.
        tasks = mongo.get_tasks_by_step("s-1")
        wakes = [t for t in tasks if t.name == "_fw_continue" and t.state == "pending"]
        assert len(wakes) == 1
        assert wakes[0].data["reason"] == "cas_conflict_rederive"
        # And the DB copy was not clobbered.
        assert mongo.get_step("s-1").version.sequence == 3

    def test_wakeup_dedupes_against_pending(self, mongo):
        for _ in range(3):
            mongo.save_step(_step(seq=0))
        self._commit_update(mongo, _step(seq=2))
        self._commit_update(mongo, _step(seq=1))  # second conflict, same step
        tasks = mongo.get_tasks_by_step("s-1")
        wakes = [t for t in tasks if t.name == "_fw_continue" and t.state == "pending"]
        assert len(wakes) == 1  # coalesced

    def test_winning_write_enqueues_nothing(self, mongo):
        mongo.save_step(_step(seq=0))  # DB at 1
        fresh = _step(seq=2)  # loaded at 1, bumped to 2 by the resume loop
        self._commit_update(mongo, fresh)
        assert mongo.get_step("s-1").version.sequence == 2
        assert mongo.get_tasks_by_step("s-1") == []
