"""The running-task uniqueness guard must cover STEPS, not stepless tasks.

⚠️ The index enforces "at most one RUNNING task per step". Its filter was
{"state": "running"} alone, which made the empty string a value like any other:
every fw:execute bootstrap task carries step_id "", so only ONE could be running
fleet-wide and a second concurrent workflow submission could not claim. Control
tasks with step_id None collided identically — measured 2026-09-14, that broke a
runner's whole poll cycle with a repeating DuplicateKeyError, a symptom that
reads as a database fault rather than a scheduling one.

These tests pin BOTH directions, because a filter that is too loose silently
blocks legitimate work and one that is too tight silently permits double
execution — and the second is the reason the index exists.
"""
import pytest

pymongo = pytest.importorskip("pymongo")
from pymongo import errors  # noqa: E402

FILTER = {"state": "running", "step_id": {"$gt": ""}}


@pytest.fixture
def coll(tmp_path):
    """An in-process Mongo is not available in unit tests, so use mongomock if
    present; otherwise skip rather than silently assert nothing."""
    mongomock = pytest.importorskip("mongomock")
    c = mongomock.MongoClient()["t"]["tasks"]
    c.create_index("step_id", unique=True, partialFilterExpression=FILTER,
                   name="task_step_id_running_unique_index")
    return c


def _ins(c, step_id, state="running"):
    c.insert_one({"step_id": step_id, "state": state})


def test_many_stepless_tasks_may_run_concurrently(coll):
    """fw:execute carries "" and control tasks carried None — neither has a step."""
    _ins(coll, ""); _ins(coll, "")
    _ins(coll, None); _ins(coll, None)


def test_the_same_real_step_cannot_run_twice(coll):
    """The invariant the index exists for. If this ever passes, the filter has
    been loosened too far and a step can execute twice concurrently."""
    _ins(coll, "step-a")
    with pytest.raises(errors.DuplicateKeyError):
        _ins(coll, "step-a")


def test_different_real_steps_are_unaffected(coll):
    _ins(coll, "step-a"); _ins(coll, "step-b")


def test_a_finished_task_does_not_hold_the_slot(coll):
    _ins(coll, "step-a", state="completed")
    _ins(coll, "step-a", state="running")


def test_the_shipped_filter_matches_what_is_tested():
    """Guard against the code and these tests drifting apart."""
    import inspect
    from facetwork.runtime.mongo_store import base
    src = inspect.getsource(base)
    assert '"step_id": {"$gt": ""}' in src, (
        "the shipped partialFilterExpression no longer excludes stepless tasks"
    )
