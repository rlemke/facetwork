"""Singleton lease for background work in a leaderless fleet.

Motivation: the dashboard is stateless and several instances serve at once (two
run here). Its reaper loop WRITES — it resets orphaned and stuck tasks — so one
copy per instance means N dashboards reap N times as often and enter the
reap-vs-reclaim race N times as often.

The lease must EXPIRE rather than be assigned, so that a dead holder is replaced
automatically. A config flag naming one instance would leave nobody reaping
whenever that instance is down, which is the failure the reaper exists to catch.
"""
import pytest

from facetwork.runtime.mongo_store import MongoStore

mongomock = pytest.importorskip("mongomock")


@pytest.fixture
def store():
    return MongoStore(database_name="t_lease", client=mongomock.MongoClient())


def test_only_one_holder_wins(store):
    assert store.try_acquire_lease("reaper", "host-a:1", 60_000) is True
    assert store.try_acquire_lease("reaper", "host-b:2", 60_000) is False


def test_holder_can_renew_its_own_lease(store):
    assert store.try_acquire_lease("reaper", "host-a:1", 60_000) is True
    assert store.try_acquire_lease("reaper", "host-a:1", 60_000) is True, "renewal must not lock a holder out"


def test_an_expired_lease_is_taken_over(store, monkeypatch):
    """A dead holder must not park the job forever — this is the whole point."""
    import facetwork.runtime.mongo_store.base as base

    now = [1_000_000]
    monkeypatch.setattr(base, "_current_time_ms", lambda: now[0])

    assert store.try_acquire_lease("reaper", "dead-host:1", 5_000) is True
    assert store.try_acquire_lease("reaper", "live-host:2", 5_000) is False

    now[0] += 6_000                                   # dead holder's lease lapses
    assert store.try_acquire_lease("reaper", "live-host:2", 5_000) is True
    # ...and the dead one does not silently steal it back while the new holder is live
    assert store.try_acquire_lease("reaper", "dead-host:1", 5_000) is False


def test_separate_names_do_not_contend(store):
    assert store.try_acquire_lease("reaper", "host-a:1", 60_000) is True
    assert store.try_acquire_lease("other-job", "host-b:2", 60_000) is True
