"""Heartbeat fields must survive the Mongo store's read and write.

Measured 2026-10-13: ``_doc_to_task`` dropped ``task_heartbeat``, so the
runner's execution-timeout watchdog (which reads the task back to find its last
activity) always measured from the claim and reset a US low-zoom build at
exactly 4h00m while it reported progress every minute. And ``save_task``
replaces the document, so every save erased what heartbeats had written.
"""

from __future__ import annotations

import mongomock

from facetwork.runtime.entities.task import TaskDefinition
from facetwork.runtime.mongo_store import MongoStore


def _store_with_running_task() -> MongoStore:
    ms = MongoStore(database_name="t_hb", client=mongomock.MongoClient())
    ms.save_task(
        TaskDefinition(
            uuid="t1",
            name="osm.Roads.ZoomBuilder.BuildZoomLayers",
            runner_id="r",
            workflow_id="w",
            flow_id="f",
            step_id="s",
            state="running",
            server_id="srv-a",
        )
    )
    return ms


def test_heartbeat_is_visible_on_read():
    ms = _store_with_running_task()
    ms.update_task_heartbeat(
        "t1",
        1_000_000,
        progress_pct=40,
        progress_message="bypasses: 60,000/133,453",
        expected_server_id="srv-a",
    )
    back = ms.get_task("t1")
    assert back.task_heartbeat == 1_000_000
    assert back.lease_expires > 1_000_000
    assert back.progress_pct == 40
    assert back.progress_message == "bypasses: 60,000/133,453"


def test_save_after_heartbeat_keeps_it():
    """save_task replaces the whole doc; a round-tripped task must carry the
    heartbeat back rather than erase it."""
    ms = _store_with_running_task()
    ms.update_task_heartbeat("t1", 2_000_000, progress_message="rings", expected_server_id="srv-a")
    task = ms.get_task("t1")
    task.retry_count = 1
    ms.save_task(task)
    doc = ms._db.tasks.find_one({"uuid": "t1"})
    assert doc["task_heartbeat"] == 2_000_000
    assert doc["lease_expires"] > 2_000_000
    assert doc["progress_message"] == "rings"


def test_new_task_reads_as_never_heartbeated():
    ms = _store_with_running_task()
    back = ms.get_task("t1")
    assert back.task_heartbeat == 0
    assert back.progress_message is None
