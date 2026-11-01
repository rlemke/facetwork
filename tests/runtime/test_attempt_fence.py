"""The attempt fence: a per-claim counter that makes a superseded execution's
result unable to land (execution-semantics.md §1.2, research note feature 14).

Before it, the terminal write was fenced by ``server_id`` alone. That cannot
tell two claims by the SAME runner apart: the dead-server reaper resets a
paused runner's task, the runner wakes, re-registers under its old id, and
re-claims the task. The first execution's late completion then matched the
filter and landed beside the second. Durable Functions, Boki and Halfmoon all
close this with a per-step sequence number checked at commit; ``attempt`` is
that number.
"""

import dataclasses

import pytest

from facetwork.runtime.entities import TaskDefinition, TaskState
from facetwork.runtime.handler_context import HandlerContext
from facetwork.runtime.memory_store import MemoryStore
from facetwork.runtime.mongo_store import MongoStore

mongomock = pytest.importorskip("mongomock")


@pytest.fixture
def mongo():
    return MongoStore(database_name="t_attempt_fence", client=mongomock.MongoClient())


def _pending(uuid: str = "t1", name: str = "ns.Work") -> TaskDefinition:
    return TaskDefinition(
        uuid=uuid,
        name=name,
        runner_id="r1",
        workflow_id="w1",
        flow_id="f1",
        step_id="s1",
        state=TaskState.PENDING,
        task_list_name="default",
    )


def _reap(store, uuid: str) -> None:
    """What the dead-server reaper does: back to pending, owner cleared."""
    t = store.get_task(uuid)
    store.save_task(dataclasses.replace(t, state=TaskState.PENDING, server_id=""))


class TestClaimCounts:
    def test_first_claim_is_attempt_one(self, mongo):
        mongo.save_task(_pending())
        task = mongo.claim_task(["ns.Work"], server_id="host-a")
        assert task.attempt == 1
        assert mongo.get_task("t1").attempt == 1

    def test_every_claim_increments_and_nothing_resets_it(self, mongo):
        mongo.save_task(_pending())
        assert mongo.claim_task(["ns.Work"], server_id="host-a").attempt == 1
        _reap(mongo, "t1")
        assert mongo.claim_task(["ns.Work"], server_id="host-a").attempt == 2

    def test_lease_reclaim_increments(self, mongo, monkeypatch):
        import facetwork.runtime.mongo_store.tasks as tasks_mod

        now = [1_000_000]
        monkeypatch.setattr(tasks_mod, "_current_time_ms", lambda: now[0])
        mongo.save_task(_pending())
        first = mongo.claim_task(["ns.Work"], server_id="host-a")
        now[0] = first.lease_expires + 1
        second = mongo.claim_task(["ns.Work"], server_id="host-b")
        assert second is not None and second.attempt == 2

    def test_script_claim_increments(self, mongo):
        t = dataclasses.replace(_pending(), kind="script", environment_hash="env1")
        mongo.save_task(t)
        assert mongo.claim_script_task(["env1"], server_id="host-a").attempt == 1

    def test_memory_store_claim_increments(self):
        store = MemoryStore()
        store.save_task(_pending())
        assert store.claim_task(["ns.Work"], server_id="host-a").attempt == 1


class TestTerminalWriteFence:
    def test_same_runner_reclaim_drops_the_stale_completion(self, mongo):
        """The case server_id alone could not catch."""
        mongo.save_task(_pending())
        stale = mongo.claim_task(["ns.Work"], server_id="host-a")  # attempt 1
        _reap(mongo, "t1")
        fresh = mongo.claim_task(["ns.Work"], server_id="host-a")  # attempt 2

        late = dataclasses.replace(stale, state=TaskState.COMPLETED)
        assert mongo.save_task_if_owned(late, "host-a") is False
        assert mongo.get_task("t1").state == TaskState.RUNNING

        done = dataclasses.replace(fresh, state=TaskState.COMPLETED)
        assert mongo.save_task_if_owned(done, "host-a") is True
        assert mongo.get_task("t1").state == TaskState.COMPLETED

    def test_other_runner_reclaim_still_fenced_by_server_id(self, mongo):
        mongo.save_task(_pending())
        stale = mongo.claim_task(["ns.Work"], server_id="host-a")
        _reap(mongo, "t1")
        mongo.claim_task(["ns.Work"], server_id="host-b")
        late = dataclasses.replace(stale, state=TaskState.COMPLETED)
        assert mongo.save_task_if_owned(late, "host-a") is False

    def test_pre_counter_task_is_fenced_by_server_id_only(self, mongo):
        """A task claimed by an older image carries no attempt; the fence must
        not refuse its owner's write for lacking one."""
        legacy = dataclasses.replace(_pending(), state=TaskState.RUNNING, server_id="host-a")
        mongo.save_task(legacy)
        mongo._db.tasks.update_one({"uuid": "t1"}, {"$unset": {"attempt": ""}})
        assert mongo.get_task("t1").attempt == 0
        done = dataclasses.replace(legacy, state=TaskState.COMPLETED)
        assert mongo.save_task_if_owned(done, "host-a") is True

    def test_memory_store_fence_with_a_held_copy(self):
        store = MemoryStore()
        store.save_task(_pending())
        stale = dataclasses.replace(store.claim_task(["ns.Work"], server_id="host-a"))
        _reap(store, "t1")
        store.claim_task(["ns.Work"], server_id="host-a")
        late = dataclasses.replace(stale, state=TaskState.COMPLETED)
        assert store.save_task_if_owned(late, "host-a") is False


class TestCancellationSeesTheAttempt:
    def test_superseded_attempt_is_cancelled(self, mongo):
        mongo.save_task(_pending())
        mongo.claim_task(["ns.Work"], server_id="host-a")
        _reap(mongo, "t1")
        mongo.claim_task(["ns.Work"], server_id="host-a")
        reason = mongo.task_cancellation_reason("t1", "host-a", 1)
        assert reason and "newer attempt" in reason
        assert mongo.task_cancellation_reason("t1", "host-a", 2) is None

    def test_attempt_zero_skips_the_check(self, mongo):
        mongo.save_task(_pending())
        mongo.claim_task(["ns.Work"], server_id="host-a")
        assert mongo.task_cancellation_reason("t1", "host-a") is None

    def test_memory_store_parity(self):
        store = MemoryStore()
        store.save_task(_pending())
        store.claim_task(["ns.Work"], server_id="host-a")
        _reap(store, "t1")
        store.claim_task(["ns.Work"], server_id="host-a")
        assert store.task_cancellation_reason("t1", "host-a", 1)
        assert store.task_cancellation_reason("t1", "host-a", 2) is None


class TestHandlerContext:
    def test_from_payload_carries_attempt_and_step(self):
        ctx = HandlerContext.from_payload({"_step_id": "s1", "_attempt": 3})
        assert ctx.attempt == 3
        assert ctx.step_id == "s1"
        assert ctx.to_payload_keys()["_attempt"] == 3

    def test_attempt_path_is_unique_per_attempt(self):
        a = HandlerContext(step_id="s1", attempt=1)
        b = HandlerContext(step_id="s1", attempt=2)
        assert a.attempt_path("s3://bucket/out/") == "s3://bucket/out/s1/1"
        assert b.attempt_path("/data/out") == "/data/out/s1/2"

    def test_attempt_path_refuses_without_a_step(self):
        with pytest.raises(RuntimeError, match="_step_id"):
            HandlerContext(attempt=1).attempt_path("/data/out")
