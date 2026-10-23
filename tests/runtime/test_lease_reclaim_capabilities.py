"""Lease-expiry reclaim must apply the same capability floors as a pending claim.

The reclaim branch of ``claim_any`` computed the feature and resource filters
and then dropped them, so a task whose lease expired could be taken by a runner
the pending path refuses: a 10 GB task by a 7.75 GiB laptop, or a task using a
construct an older executor silently drops.
"""

from __future__ import annotations

import pytest

from facetwork.runtime.mongo_store import MongoStore
from facetwork.runtime.mongo_store.base import _current_time_ms

mongomock = pytest.importorskip("mongomock")

CLAIM = [(["ns.Big"], "ns")]


@pytest.fixture
def store():
    return MongoStore(client=mongomock.MongoClient(), database_name="t", create_indexes=False)


def _expired(store, **extra) -> None:
    now = _current_time_ms()
    store._db.tasks.insert_one(
        {
            "uuid": "t1",
            "name": "ns.Big",
            "task_list_name": "ns",
            "state": "running",
            "server_id": "dead-host",
            "step_id": "s1",
            "workflow_id": "w1",
            "runner_id": "r1",
            "flow_id": "f1",
            "lease_expires": now - 60_000,
            "retry_count": 0,
            "max_retries": 5,
            "updated": now - 3_600_000,
            # Derived from `requires` exactly as MongoStore._task_to_doc writes it.
            "requires_dims": sorted(extra.get("requires") or {}),
            **extra,
        }
    )


def test_reclaim_refuses_a_runner_below_the_memory_floor(store):
    _expired(store, requires={"memory_gb": 10})
    assert store.claim_any(CLAIM, server_id="small", resources={"memory_gb": 7.75}) is None
    assert store._db.tasks.find_one({"uuid": "t1"})["server_id"] == "dead-host"


def test_reclaim_takes_it_on_a_runner_that_meets_the_floor(store):
    _expired(store, requires={"memory_gb": 10})
    task = store.claim_any(CLAIM, server_id="big", resources={"memory_gb": 13.6})
    assert task is not None and task.server_id == "big"


def test_reclaim_refuses_a_runner_missing_a_required_feature(store):
    _expired(store, required_features=["after"])
    assert store.claim_any(CLAIM, server_id="old", known_features=[]) is None
    task = store.claim_any(CLAIM, server_id="new", known_features=["after"])
    assert task is not None and task.server_id == "new"


def test_reclaim_of_an_unconstrained_task_is_unchanged(store):
    _expired(store)
    task = store.claim_any(CLAIM, server_id="any")
    assert task is not None and task.retry_count == 1
