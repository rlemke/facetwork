"""CPU floors are booked: a claim offers capacity minus what running tasks hold.

Measured 2026-11-14: ``tess.stitch.SearchChunk`` asks for 8 CPUs, and the
floor was checked against capacity only, so a 12-CPU host ran up to 10 chunks
(30 search processes) at once; its chunks took 8x as long as on a host that was
not overloaded.
"""

from __future__ import annotations

import pytest

mongomock = pytest.importorskip("mongomock")

from facetwork.runtime import Evaluator, MemoryStore, Telemetry  # noqa: E402
from facetwork.runtime import base_runner as br  # noqa: E402
from facetwork.runtime.agent import ToolRegistry  # noqa: E402
from facetwork.runtime.claimer import Claimer, Waiter  # noqa: E402
from facetwork.runtime.entities import (  # noqa: E402
    ServerDefinition,
    ServerState,
    TaskDefinition,
    TaskState,
)
from facetwork.runtime.runner import RunnerConfig, RunnerService  # noqa: E402

HOST = "host-a"


@pytest.fixture(params=["mongo", "memory"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_cpu_booking", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _server(store, uuid, name=HOST):
    store.save_server(
        ServerDefinition(
            uuid=uuid,
            server_group="g",
            service_name="runner",
            server_name=name,
            start_time=1,
            ping_time=1,
            state=ServerState.RUNNING,
            task_list="",
        )
    )


def _task(uuid, state=TaskState.PENDING, server_id="", cpus=8):
    return TaskDefinition(
        uuid=uuid,
        name="ns.Search",
        runner_id="r1",
        workflow_id="w1",
        flow_id="f1",
        step_id=uuid,
        state=state,
        server_id=server_id,
        task_list_name="ns",
        requires={"cpus": cpus} if cpus else {},
    )


def test_held_counts_running_cpu_floors_on_this_host_only(store):
    _server(store, "a1")
    _server(store, "a2")
    _server(store, "b1", name="host-b")
    store.save_task(_task("t1", TaskState.RUNNING, "a1", cpus=8))
    store.save_task(_task("t2", TaskState.RUNNING, "a2", cpus=3))
    store.save_task(_task("t3", TaskState.RUNNING, "b1", cpus=8))  # another host
    store.save_task(_task("t4", TaskState.COMPLETED, "a1", cpus=8))  # finished
    store.save_task(_task("t5", TaskState.RUNNING, "a1", cpus=0))  # no floor
    assert store.cpus_held_on_host(HOST) == 11.0
    assert store.cpus_held_on_host("host-c") == 0.0
    assert store.cpus_held_on_host("") == 0.0


def test_offer_free_cpus():
    assert br.offer_free_cpus({"cpus": 12.0, "memory_gb": 5}, 8.0) == {"cpus": 4.0, "memory_gb": 5}
    assert br.offer_free_cpus({"cpus": 12.0}, 20.0) == {"cpus": 0.0}
    assert br.offer_free_cpus({"cpus": 12.0}, None) == {"cpus": 12.0}  # unknown: capacity
    assert br.offer_free_cpus({"memory_gb": 5}, 8.0) == {"memory_gb": 5}  # no CPU dimension


def _claimer(store):
    c = Claimer(store, server_name=HOST, min_gap_s=0.0)
    c._register()
    for rid in ("r1", "r2"):
        _server(store, rid)
        c._waiters.append(
            Waiter(server_id=rid, claims=[(["ns.Search"], ["ns"])], resources={"cpus": 12.0})
        )
    return c


def test_the_claimer_does_not_put_two_8_cpu_tasks_on_a_12_cpu_host(store):
    for i in range(3):
        store.save_task(_task(f"s{i}"))
    c = _claimer(store)
    assert c.cycle() is True  # 12 free -> one 8-CPU task
    assert c.cycle() is False  # offered 12 - 8 = 4, inside the cache window
    c._cpu_held_at = -1e9
    assert c.cycle() is False  # and from the store once the task runs here
    running = [f"s{i}" for i in range(3) if store.get_task(f"s{i}").state == TaskState.RUNNING]
    assert len(running) == 1

    # It finishes: its CPUs are free again and the next one fits.
    t = store.get_task(running[0])
    t.state = TaskState.COMPLETED
    store.save_task(t)
    c._cpu_held_at = -1e9
    assert c.cycle() is True


def test_tasks_running_on_another_host_do_not_count(store):
    _server(store, "b1", name="host-b")
    store.save_task(_task("elsewhere", TaskState.RUNNING, "b1", cpus=12))
    store.save_task(_task("s0"))
    assert _claimer(store).cycle() is True


def test_booking_can_be_switched_off(store, monkeypatch):
    monkeypatch.setenv("FW_CLAIM_LIVE_CPUS", "0")
    for i in range(2):
        store.save_task(_task(f"s{i}"))
    c = _claimer(store)
    assert c.cycle() is True
    assert c.cycle() is True  # capacity-only, as before


def _service(store, monkeypatch):
    monkeypatch.setattr(br, "measured_resources", lambda: {"cpus": 12.0})
    registry = ToolRegistry()
    registry.register("ns.Search", lambda p: {})
    ev = Evaluator(persistence=store, telemetry=Telemetry(enabled=False))
    svc = RunnerService(store, ev, RunnerConfig(server_name=HOST), registry)
    _server(store, svc.server_id)
    return svc


def test_a_runner_claiming_directly_offers_only_free_cpus(store, monkeypatch):
    svc = _service(store, monkeypatch)
    _server(store, "other-runner")
    store.save_task(_task("busy", TaskState.RUNNING, "other-runner", cpus=8))
    assert svc._claim_resources()["cpus"] == 4.0


def test_through_the_claimer_a_runner_offers_capacity(store, monkeypatch):
    """The claimer books CPUs for the whole host; subtracting here as well would
    count every running task twice."""
    svc = _service(store, monkeypatch)
    _server(store, "other-runner")
    store.save_task(_task("busy", TaskState.RUNNING, "other-runner", cpus=8))
    svc._CLAIM_PROXY_URL = "http://127.0.0.1:9"
    assert svc._claim_resources()["cpus"] == 12.0


def test_a_direct_claim_books_its_cpus_before_the_next_claim(store, monkeypatch):
    svc = _service(store, monkeypatch)
    for i in range(2):
        store.save_task(_task(f"s{i}"))
    claims = [(["ns.Search"], ["ns"])]
    first = svc._claim_next(claims, resources=svc._claim_resources(), known_features=[])
    assert first is not None
    # Same cache window: the claim just made is counted without a store read.
    assert svc._claim_resources()["cpus"] == 4.0
    assert svc._claim_next(claims, resources=svc._claim_resources(), known_features=[]) is None
