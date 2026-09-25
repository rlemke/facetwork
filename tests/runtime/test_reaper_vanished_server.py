"""A task whose owning server RECORD IS GONE must still be reclaimed.

The reaper finds dead servers by scanning `servers` for a stale or zero
`ping_time`. A record that was DELETED has no ping to be stale — it simply is not
in the collection — so the scan never names it and its running tasks are stranded
until the lease expires.

Measured 2026-09-25: a fleet rollout destroyed the container running a 45-minute
low-zoom step, its server record was pruned, and the task sat `state=running` with
a lease **473 minutes** in the future with nothing able to reclaim it. Every
rollout can strand whatever was running at that moment for most of a day.
"""
import pytest

from facetwork.runtime.mongo_store import MongoStore

mongomock = pytest.importorskip("mongomock")

NOW = 1_000_000_000_000


@pytest.fixture
def store(monkeypatch):
    import facetwork.runtime.mongo_store.base as base
    import facetwork.runtime.mongo_store.tasks as tasks

    monkeypatch.setattr(base, "_current_time_ms", lambda: NOW)
    monkeypatch.setattr(tasks, "_current_time_ms", lambda: NOW)
    return MongoStore(database_name="t_vanish", client=mongomock.MongoClient())


def _task(store, server_id, heartbeat_ms):
    store._db.tasks.insert_one({
        "uuid": "task-1", "name": "osm.Roads.ZoomBuilder.BuildZoomLayers",
        "state": "running", "server_id": server_id, "step_id": "step-1",
        "workflow_id": "wf-1", "updated": NOW - 600_000,
        "task_heartbeat": heartbeat_ms,
        "lease_expires": NOW + 8 * 3_600_000,   # the 473-minute lease
    })


def test_a_task_owned_by_a_deleted_server_is_reclaimed(store):
    """No server record at all — the case the ping scan cannot see."""
    _task(store, "server-that-was-pruned", heartbeat_ms=NOW - 600_000)
    reaped = store.reap_orphaned_tasks(down_timeout_ms=120_000)
    assert [r["name"] for r in reaped] == ["osm.Roads.ZoomBuilder.BuildZoomLayers"]
    assert store._db.tasks.find_one({"uuid": "task-1"})["state"] == "pending"


def test_a_live_handler_is_not_reclaimed_even_if_its_record_vanished(store):
    """⚠️ The guard that makes this safe.

    A runner whose record was pruned while it is ALIVE re-registers on its next
    heartbeat, and its TASK heartbeat stays fresh. Reclaiming on the missing
    record alone would hand a running handler to a second runner and duplicate
    hours of work.
    """
    _task(store, "server-that-was-pruned", heartbeat_ms=NOW - 5_000)
    assert store.reap_orphaned_tasks(down_timeout_ms=120_000) == []
    assert store._db.tasks.find_one({"uuid": "task-1"})["state"] == "running"


def test_a_healthy_owner_is_left_alone(store):
    store._db.servers.insert_one(
        {"uuid": "server-alive", "state": "running", "ping_time": NOW - 1_000}
    )
    _task(store, "server-alive", heartbeat_ms=NOW - 600_000)
    assert store.reap_orphaned_tasks(down_timeout_ms=120_000) == []


def test_the_stale_record_path_still_works(store):
    """The original behaviour must survive the addition."""
    store._db.servers.insert_one(
        {"uuid": "server-stale", "state": "running", "ping_time": NOW - 600_000}
    )
    _task(store, "server-stale", heartbeat_ms=NOW - 600_000)
    reaped = store.reap_orphaned_tasks(down_timeout_ms=120_000)
    assert len(reaped) == 1


def test_no_running_tasks_is_not_an_error(store):
    assert store.reap_orphaned_tasks(down_timeout_ms=120_000) == []
