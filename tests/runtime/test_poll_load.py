"""How much an idle runner costs the database, and that cutting it lost nothing.

Measured 2026-10-20: 34 runners with ZERO pending tasks issued ~190
findAndModify/s plus ~55 reads/s. Three changes, each pinned here:

1. one ``claim_any`` query per free slot instead of one ``claim_task`` per kind
   of work (events, resumes, built-ins, continuations, scripts), with the
   lease-reclaim half paced by ``_RECLAIM_SCAN_MS``;
2. the quarantine flag re-read every ``_QUARANTINE_CHECK_MS``, not every poll;
3. idle backoff — consecutive empty cycles double the wait up to a cap.
"""

from __future__ import annotations

import threading
import time

import pytest

mongomock = pytest.importorskip("mongomock")

from facetwork.runtime import Evaluator, MemoryStore, Telemetry  # noqa: E402
from facetwork.runtime.agent import ToolRegistry  # noqa: E402
from facetwork.runtime.continuation import (  # noqa: E402
    CONTINUATION_TASK_LIST,
    CONTINUATION_TASK_NAME,
)
from facetwork.runtime.entities import ServerState, TaskDefinition, TaskState  # noqa: E402
from facetwork.runtime.runner import RunnerConfig, RunnerService  # noqa: E402


@pytest.fixture
def mongo_store():
    from facetwork.runtime.mongo_store import MongoStore

    store = MongoStore(database_name="afl_test_poll", client=mongomock.MongoClient())
    yield store
    store.drop_database()
    store.close()


def _task(uuid, name, task_list="default", state=TaskState.PENDING, **kw):
    return TaskDefinition(
        uuid=uuid,
        name=name,
        runner_id="r1",
        workflow_id="w1",
        flow_id="f1",
        step_id=uuid,
        state=state,
        task_list_name=task_list,
        **kw,
    )


@pytest.fixture
def fam_calls(monkeypatch):
    """Count find_one_and_update calls on any mongomock collection."""
    calls: list[dict] = []
    real = mongomock.collection.Collection.find_one_and_update

    def counting(self, filter, *a, **kw):
        calls.append(filter)
        return real(self, filter, *a, **kw)

    monkeypatch.setattr(mongomock.collection.Collection, "find_one_and_update", counting)
    return calls


# --- 1. one query per slot ---------------------------------------------------


def test_claim_any_finds_each_kind_of_work_in_one_query(mongo_store, fam_calls):
    mongo_store.save_task(_task("ev", "ns.Ev", task_list="ns"))
    mongo_store.save_task(_task("ex", "fw:execute:Wf"))
    mongo_store.save_task(_task("co", CONTINUATION_TASK_NAME, task_list=CONTINUATION_TASK_LIST))
    mongo_store.save_task(_task("sc", "ns.Script", kind="script", environment_hash="h1"))
    mongo_store.save_task(_task("other", "elsewhere.Ev", task_list="elsewhere"))

    claims = [
        (["ns.Ev"], ["ns", "default"]),
        (["fw:execute"], ["ns", "default"]),
        ([CONTINUATION_TASK_NAME], CONTINUATION_TASK_LIST),
    ]
    got = set()
    for _ in range(4):
        fam_calls.clear()
        t = mongo_store.claim_any(claims, server_id="s", script_environments=["h1"])
        assert t is not None
        assert len(fam_calls) == 1  # a hit costs exactly one query
        got.add(t.uuid)
    assert got == {"ev", "ex", "co", "sc"}

    fam_calls.clear()
    assert mongo_store.claim_any(claims, server_id="s", script_environments=["h1"]) is None
    assert len(fam_calls) == 2  # pending + lease reclaim
    fam_calls.clear()
    assert mongo_store.claim_any(claims, server_id="s", reclaim_expired=False) is None
    assert len(fam_calls) == 1  # pending only
    # Work outside every spec was never touched.
    assert mongo_store.get_task("other").state == TaskState.PENDING


def test_claim_any_keeps_each_branch_on_its_own_task_list(mongo_store):
    # A continuation-named task on a handler list, and a handler-named task on
    # the continuation list: neither matches its spec.
    mongo_store.save_task(_task("a", CONTINUATION_TASK_NAME, task_list="ns"))
    mongo_store.save_task(_task("b", "ns.Ev", task_list=CONTINUATION_TASK_LIST))
    claims = [(["ns.Ev"], ["ns"]), ([CONTINUATION_TASK_NAME], CONTINUATION_TASK_LIST)]
    assert mongo_store.claim_any(claims, server_id="s") is None


def test_claim_any_reclaims_expired_leases_only_when_asked(mongo_store):
    mongo_store.save_task(_task("x", "ns.Ev", task_list="ns", state=TaskState.RUNNING))
    mongo_store._db.tasks.update_one({"uuid": "x"}, {"$set": {"lease_expires": 1}})
    claims = [(["ns.Ev"], ["ns"])]
    assert mongo_store.claim_any(claims, server_id="new", reclaim_expired=False) is None
    t = mongo_store.claim_any(claims, server_id="new")
    assert t is not None and t.uuid == "x" and t.retry_count == 1


def test_claim_any_still_applies_capability_filters(mongo_store):
    mongo_store.save_task(_task("big", "ns.Ev", task_list="ns", requires={"memory_gb": 13}))
    mongo_store.save_task(_task("new", "fw:execute:Wf", required_features=["future-thing"]))
    claims = [(["ns.Ev"], ["ns"]), (["fw:execute"], ["default"])]
    kw = {"server_id": "s", "resources": {"memory_gb": 4}, "known_features": ["after"]}
    assert mongo_store.claim_any(claims, **kw) is None
    assert mongo_store.claim_any(claims, **{**kw, "resources": {"memory_gb": 16}}).uuid == "big"


def test_memory_store_default_claim_any_tries_each_spec():
    store = MemoryStore()
    store.save_task(_task("co", CONTINUATION_TASK_NAME, task_list=CONTINUATION_TASK_LIST))
    claims = [(["ns.Ev"], ["ns"]), ([CONTINUATION_TASK_NAME], CONTINUATION_TASK_LIST)]
    assert store.claim_any(claims, server_id="s").uuid == "co"
    assert store.claim_any(claims, server_id="s") is None


def _service(store, **cfg):
    registry = ToolRegistry()
    registry.register("ns.Ev", lambda p: {})
    ev = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
    return RunnerService(store, ev, RunnerConfig(**cfg), registry)


def test_idle_runner_cycle_is_one_query_after_the_first_reclaim_scan(mongo_store, fam_calls):
    svc = _service(mongo_store)
    svc._poll_cycle()
    first = len(fam_calls)
    fam_calls.clear()
    svc._poll_cycle()
    # Was ~9 (pending + reclaim for each of events, resume, built-ins,
    # continuations, plus the script claim).
    assert first == 2
    assert len(fam_calls) == 1


def test_runner_routes_each_claimed_kind_to_its_processor():
    store = MemoryStore()
    svc = _service(store, max_concurrent=10)
    seen = []
    svc._submit_event_task = lambda t: seen.append(("event", t.uuid))
    svc._submit_resume_task = lambda t: seen.append(("resume", t.uuid))
    svc._submit_task = lambda t: seen.append(("builtin", t.uuid))
    svc._submit_continuation_task = lambda t: seen.append(("continuation", t.uuid))
    store.save_task(_task("ev", "ns.Ev", task_list="ns"))
    store.save_task(_task("rs", "fw:resume"))
    store.save_task(_task("ex", "fw:execute"))
    store.save_task(_task("co", CONTINUATION_TASK_NAME, task_list=CONTINUATION_TASK_LIST))
    svc._poll_cycle()
    assert sorted(seen) == [
        ("builtin", "ex"),
        ("continuation", "co"),
        ("event", "ev"),
        ("resume", "rs"),
    ]


# --- 2. quarantine read is cached ---------------------------------------------


def test_quarantine_is_read_at_most_once_per_check_interval():
    store = MemoryStore()
    svc = _service(store)
    svc._register_server()
    reads = []
    real = store.get_server
    store.get_server = lambda sid: reads.append(sid) or real(sid)
    for _ in range(20):
        assert svc._is_quarantined() is False
    assert len(reads) == 1

    server = real(svc.server_id)
    server.state = ServerState.QUARANTINE
    store.save_server(server)
    svc._quarantine_checked_ms -= svc._QUARANTINE_CHECK_MS  # interval elapsed
    assert svc._is_quarantined() is True
    assert len(reads) == 2


# --- 3. idle backoff ----------------------------------------------------------


def test_idle_wait_doubles_to_the_cap_and_resets_on_work():
    svc = _service(MemoryStore())
    svc._POLL_IDLE_MAX_S = 5.0
    waits = []
    for _ in range(6):
        waits.append(svc._idle_wait_seconds(1.0))
        svc._idle_cycles += 1
    assert waits == [1.0, 2.0, 4.0, 5.0, 5.0, 5.0]
    svc._poll_pause(1.0, dispatched=1)
    assert svc._idle_cycles == 0


def test_idle_wait_never_shortens_a_slower_configured_interval():
    svc = _service(MemoryStore())
    svc._POLL_IDLE_MAX_S = 5.0
    svc._idle_cycles = 3
    assert svc._idle_wait_seconds(10.0) == 10.0


def test_a_local_wakeup_resets_the_backoff():
    svc = _service(MemoryStore())
    svc._POLL_IDLE_MAX_S = 5.0
    svc._idle_cycles = 4
    threading.Timer(0.05, svc._on_future_done).start()
    start = time.monotonic()
    svc._poll_pause(1.0, dispatched=0)
    assert time.monotonic() - start < 1.0
    assert svc._idle_cycles == 0
