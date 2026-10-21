"""The Python runtime against the agent protocol's shared conformance cases.

``agents/protocol/conformance/*.json`` state the claim and retry rules as data.
Every agent library (Java, Scala, Go, TypeScript) runs the same files against
its own implementation, so the rule exists once. This test pins the reference
side: if the runtime changes a rule, this fails until the cases (and so every
library) are updated with it.
"""

from __future__ import annotations

import json
import pathlib
import time

import pytest

from facetwork.runtime.entities import TaskDefinition

mongomock = pytest.importorskip("mongomock")

_CONF = pathlib.Path(__file__).resolve().parents[2] / "agents" / "protocol" / "conformance"
_CLAIM = json.loads((_CONF / "claim_cases.json").read_text())
_RETRY = json.loads((_CONF / "retry_cases.json").read_text())


def _resolve(value, now):
    if isinstance(value, dict) and set(value) == {"rel_ms"}:
        return now + value["rel_ms"]
    return value


@pytest.fixture
def store():
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_conformance", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


@pytest.mark.parametrize("case", _CLAIM["cases"], ids=lambda c: c["id"])
def test_claim_case(store, case):
    now = int(time.time() * 1000)
    doc = {
        "uuid": "t-" + case["id"],
        "runner_id": "r",
        "workflow_id": "w",
        "flow_id": "f",
        "step_id": "s-" + case["id"],
        "created": now,
        "updated": now,
    }
    doc.update({k: _resolve(v, now) for k, v in case["task"].items()})
    # Insert the raw document: the cases describe what is IN the store, which
    # is what another language's agent sees, independent of TaskDefinition.
    store._db.tasks.insert_one(doc)
    srv = _CLAIM["server"]
    got = store.claim_task(
        srv["task_names"],
        task_list=srv["task_lists"],
        server_id="conformance-server",
        resources=srv["resources"],
        known_features=[],
    )
    assert (got is not None) == case["claimed"]
    if got is not None:
        stored = store._db.tasks.find_one({"uuid": doc["uuid"]})
        assert stored["state"] == "running"
        assert stored["server_id"] == "conformance-server"
        assert stored["lease_expires"] > now
        if "retry_count_after" in case:
            assert stored["retry_count"] == case["retry_count_after"]


@pytest.mark.parametrize("case", _RETRY["cases"], ids=lambda c: c["id"])
def test_retry_case(case):
    from facetwork.runtime.base_runner import BaseRunner

    task = TaskDefinition(
        uuid="t",
        name="ns.Facet",
        runner_id="r",
        workflow_id="w",
        flow_id="f",
        step_id="s",
        state="running",
        retry_count=case["retry_count"],
        max_retries=case["max_retries"],
        server_id="me",
    )
    BaseRunner._transition_for_retry(
        object.__new__(BaseRunner),
        task,
        set_next_retry_after=True,
        clear_error_on_retry=False,
        permanent=case["permanent"],
    )
    assert task.state == case["state"]
    assert task.retry_count == case["retry_count_after"]
    if case["state"] == "pending":
        assert task.server_id == ""
        assert task.next_retry_after - task.updated == case["backoff_ms"]


def test_spec_timing_matches_the_runtime():
    from facetwork.runtime.mongo_store.base import _compute_next_retry_after

    timing = json.loads((_CONF.parent / "constants.json").read_text())["timing"]
    assert _compute_next_retry_after(1, 0) == 10000
    assert timing["default_max_retries"] == TaskDefinition(
        uuid="", name="", runner_id="", workflow_id="", flow_id="", step_id=""
    ).max_retries
    from facetwork.runtime.base_runner import BaseRunner

    assert timing["claim_proxy_retry_ms"] == BaseRunner._PROXY_RETRY_S * 1000
