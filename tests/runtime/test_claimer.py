"""The per-host claimer: one merged claim for every runner on a machine.

Pins the properties the design depends on:

* each runner's spec keeps its OWN capabilities inside the merged query, so a
  task reaches only a runner that could have claimed it directly;
* the task is handed over with an ownership-gated write, and a claimer that dies
  holding a task is recovered by the dead-server reaper like any runner;
* the memory ledger stops two big tasks fitting into the same free gap (the
  claim race measured 2026-10-16 / 2026-10-19);
* a runner whose claimer is down claims directly instead of stalling.
"""

from __future__ import annotations

import threading
import time

import pytest

mongomock = pytest.importorskip("mongomock")

from facetwork.runtime import Evaluator, Telemetry  # noqa: E402
from facetwork.runtime.agent import ToolRegistry  # noqa: E402
from facetwork.runtime.claimer import Claimer, Waiter, serve, spec_accepts  # noqa: E402
from facetwork.runtime.entities import TaskDefinition, TaskState  # noqa: E402
from facetwork.runtime.runner import RunnerConfig, RunnerService  # noqa: E402


@pytest.fixture
def store():
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_claimer", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _task(uuid, name, task_list="ns", **kw):
    return TaskDefinition(
        uuid=uuid,
        name=name,
        runner_id="r1",
        workflow_id="w1",
        flow_id="f1",
        step_id=uuid,
        state=TaskState.PENDING,
        task_list_name=task_list,
        **kw,
    )


def _spec(server_id, names, lists=("ns",), **kw):
    return {"server_id": server_id, "claims": [[list(names), list(lists)]], **kw}


def _claimer(store, **kw):
    c = Claimer(store, idle_max_s=0.2, min_gap_s=0.0, heartbeat_s=60, **kw)
    c.start()
    return c


def _ask(c, spec, out, key, wait_s=2.0, kick=True):
    out[key] = c.request(spec, wait_s=wait_s, kick=kick)


# --- claim_any per-entry capabilities ----------------------------------------


def test_claim_any_applies_each_entrys_own_capabilities(store):
    store.save_task(_task("new", "ns.A", required_features=["after"]))
    store.save_task(_task("big", "ns.B", requires={"memory_gb": 12}))
    old_runner = (["ns.A", "ns.B"], ["ns"], {"known_features": [], "resources": {"memory_gb": 4}})
    assert store.claim_any([old_runner], server_id="s") is None
    knows = (["ns.A"], ["ns"], {"known_features": ["after"], "resources": {}})
    big = (["ns.B"], ["ns"], {"known_features": [], "resources": {"memory_gb": 16}})
    got = {store.claim_any([old_runner, knows, big], server_id="s").uuid for _ in range(2)}
    assert got == {"new", "big"}


def test_spec_accepts_mirrors_the_claim_filters():
    w = Waiter(server_id="r", claims=[(["fw:execute"], ["default"])], known_features=["after"])
    assert spec_accepts(w, _task("t", "fw:execute:Wf", task_list="default"))
    assert not spec_accepts(w, _task("t", "fw:execute:Wf", task_list="ns"))
    assert not spec_accepts(
        w, _task("t", "fw:execute", task_list="default", requires={"memory_gb": 1})
    )
    assert not spec_accepts(
        w, _task("t", "fw:execute", task_list="default", required_features=["future"])
    )
    s = Waiter(server_id="r", claims=[], script_environments=["h1"])
    assert spec_accepts(s, _task("t", "ns.Script", kind="script", environment_hash="h1"))
    assert not spec_accepts(s, _task("t", "ns.Script", kind="script", environment_hash="h2"))


# --- routing through the claimer ----------------------------------------------


def test_one_merged_query_routes_each_task_to_the_runner_that_can_run_it(store):
    store.save_task(_task("a", "ns.A"))
    store.save_task(_task("b", "other.B", task_list="other"))
    c = _claimer(store)
    try:
        out: dict = {}
        ts = [
            threading.Thread(target=_ask, args=(c, _spec("runner-a", ["ns.A"]), out, "a")),
            threading.Thread(
                target=_ask, args=(c, _spec("runner-b", ["other.B"], ["other"]), out, "b")
            ),
        ]
        for t in ts:
            t.start()
        for t in ts:
            t.join(5)
        assert out == {"a": "a", "b": "b"}
        assert store.get_task("a").server_id == "runner-a"
        assert store.get_task("b").server_id == "runner-b"
        assert store.get_task("a").state == TaskState.RUNNING
    finally:
        c.stop()


def test_an_idle_host_costs_the_claimers_queries_not_one_per_runner(store, monkeypatch):
    c = Claimer(store, idle_max_s=5.0, min_gap_s=0.0, heartbeat_s=60)
    c.start()
    calls = []
    real = store.claim_any
    monkeypatch.setattr(store, "claim_any", lambda *a, **kw: calls.append(1) or real(*a, **kw))
    try:
        out: dict = {}
        ts = [
            threading.Thread(
                target=_ask, args=(c, _spec(f"r{i}", [f"ns.F{i}"]), out, i, 1.0, False)
            )
            for i in range(7)
        ]
        for t in ts:
            t.start()
        for t in ts:
            t.join(5)
        assert all(v is None for v in out.values())
        # 7 runners waiting ~1 s: one or two merged queries (the idle backoff
        # starts at 1 s), where direct polling costs one per runner per cycle.
        assert 1 <= len(calls) <= 2
    finally:
        c.stop()


def test_a_request_with_no_work_returns_after_its_wait(store):
    c = _claimer(store)
    try:
        t0 = time.monotonic()
        assert c.request(_spec("r", ["ns.A"]), wait_s=0.3) is None
        assert 0.25 <= time.monotonic() - t0 < 3
    finally:
        c.stop()


# --- ownership and recovery ----------------------------------------------------


def test_a_task_no_waiter_can_take_is_released_to_the_queue(store, monkeypatch):
    store.save_task(_task("a", "ns.A"))
    c = Claimer(store, min_gap_s=0.0)
    c._register()
    w = Waiter(server_id="r", claims=[(["ns.A"], ["ns"])], deadline=time.monotonic() + 5)
    c._waiters.append(w)
    real = store.claim_any

    def claim_then_runner_leaves(*a, **kw):
        task = real(*a, **kw)
        c._waiters.clear()  # the runner gave up while the query ran
        return task

    monkeypatch.setattr(store, "claim_any", claim_then_runner_leaves)
    assert c.cycle() is False
    t = store.get_task("a")
    assert t.state == TaskState.PENDING and t.server_id == ""
    assert c.stats["released"] == 1


def test_a_claimer_that_dies_holding_a_task_is_recovered_by_the_reaper(store):
    store.save_task(_task("a", "ns.A"))
    c = Claimer(store, min_gap_s=0.0)
    c._register()
    c._waiters.append(Waiter(server_id="r", claims=[(["ns.A"], ["ns"])]))
    real_hand_over = c._hand_over
    c.cycle()  # claimed under the claimer's id; it "dies" before handing over
    assert store.get_task("a").server_id == c.server_id
    server = store.get_server(c.server_id)
    server.ping_time = int(time.time() * 1000) - 600_000
    store.save_server(server)
    store.reap_orphaned_tasks(down_timeout_ms=120_000)
    t = store.get_task("a")
    assert t.state == TaskState.PENDING
    assert real_hand_over(t, "r") is False  # a late hand-over cannot steal it back


def test_hand_over_is_refused_once_the_task_was_reclaimed(store):
    store.save_task(_task("a", "ns.A"))
    c = Claimer(store)
    task = store.claim_any([(["ns.A"], ["ns"])], server_id=c.server_id)
    store._db.tasks.update_one({"uuid": "a"}, {"$set": {"server_id": "someone-else"}})
    assert c._hand_over(task, "r") is False
    assert store.get_task("a").server_id == "someone-else"


# --- memory ledger ---------------------------------------------------------------


def test_the_ledger_stops_two_big_tasks_fitting_one_gap(store):
    store.save_task(_task("big1", "ns.Zoom", requires={"memory_gb": 12}))
    store.save_task(_task("big2", "ns.Zoom", requires={"memory_gb": 12}))
    c = Claimer(store, min_gap_s=0.0, reserve_hold_s=600)
    c._register()
    free = {"memory_gb": 15.0}
    for rid in ("r1", "r2"):
        c._waiters.append(
            Waiter(server_id=rid, claims=[(["ns.Zoom"], ["ns"])], resources=dict(free))
        )
    assert c.cycle() is True  # 15 free -> one 12 GB task
    assert c.reserved_gb() == 12.0
    assert c.cycle() is False  # offered 15 - 12 = 3: the second must wait
    pending = [t for t in ("big1", "big2") if store.get_task(t).state == TaskState.PENDING]
    assert len(pending) == 1

    # The first finishes: its reservation is dropped and the second fits.
    done = next(t for t in ("big1", "big2") if t not in pending)
    store._db.tasks.update_one({"uuid": done}, {"$set": {"state": "completed"}})
    c._last_reserve_check = -1e9
    assert c.reserved_gb() == 0.0
    assert c.cycle() is True


def test_a_reservation_expires_after_the_hold(store):
    clock = [1000.0]
    c = Claimer(store, reserve_hold_s=600, clock=lambda: clock[0])
    c._reserved["x"] = (12.0, clock[0])
    c._last_reserve_check = clock[0]
    assert c.reserved_gb() == 12.0
    clock[0] += 601
    assert c.reserved_gb() == 0.0


# --- end to end: a RunnerService behind the claimer -------------------------------


def _service(store, **cfg):
    registry = ToolRegistry()
    registry.register("ns.Ev", lambda p: {})
    ev = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
    return RunnerService(store, ev, RunnerConfig(**cfg), registry)


def test_a_runner_claims_through_the_claimer_over_http(store):
    c = _claimer(store)
    httpd = serve(c, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        svc = _service(store)
        svc._CLAIM_PROXY_URL = f"http://127.0.0.1:{httpd.server_address[1]}"
        svc._POLL_IDLE_MAX_S = 0.5
        store.save_task(_task("ev", "ns.Ev"))
        task = svc._claim_next([(["ns.Ev"], ["ns"])], resources={}, known_features=[])
        assert task is not None and task.uuid == "ev"
        assert store.get_task("ev").server_id == svc.server_id
        assert svc._claim_next([(["ns.Ev"], ["ns"])], resources={}, known_features=[]) is None
        assert c.stats["handed"] == 1
    finally:
        httpd.shutdown()
        c.stop()


def test_a_runner_whose_claimer_is_down_claims_directly(store):
    svc = _service(store)
    svc._CLAIM_PROXY_URL = "http://127.0.0.1:9"  # nothing listens on discard
    store.save_task(_task("ev", "ns.Ev"))
    task = svc._claim_next([(["ns.Ev"], ["ns"])], resources={}, known_features=[])
    assert task is not None and task.uuid == "ev"
    assert svc._proxy_down_until > time.monotonic()  # skipped for a while, then retried


def test_an_empty_long_poll_replaces_the_idle_wait():
    svc = _service(__import__("facetwork.runtime", fromlist=["MemoryStore"]).MemoryStore())
    svc._proxy_waited = True
    t0 = time.monotonic()
    svc._poll_pause(5.0, dispatched=0)
    assert time.monotonic() - t0 < 0.5
    assert svc._idle_cycles == 1 and svc._proxy_waited is False
