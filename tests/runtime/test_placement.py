"""Fast-host placement: a slower host leaves a task to faster ones when they
would finish it sooner (facetwork/runtime/placement.py).

Measured 2026-11-17, fleet stitched TESS search with CPU booking: a 3-CPU
chunk took a median 3,808 s on the fastest host and 10,629-26,884 s on the
other four, and the run ended with the slowest hosts holding its last chunks.
"""

from __future__ import annotations

import time

import pytest

mongomock = pytest.importorskip("mongomock")

from facetwork import FFLParser, emit_dict  # noqa: E402
from facetwork.ast_utils import find_workflow  # noqa: E402
from facetwork.runtime import Evaluator, MemoryStore, Telemetry  # noqa: E402
from facetwork.runtime.claimer import Claimer, Waiter  # noqa: E402
from facetwork.runtime.entities import (  # noqa: E402
    ServerDefinition,
    ServerState,
    TaskDefinition,
    TaskState,
)
from facetwork.runtime.placement import (  # noqa: E402
    PlacementGate,
    declined_facets,
    record_timing,
    should_claim,
)

FACET = "ns.Search"
FAST, SLOW = "fast-host", "slow-host"


@pytest.fixture(params=["mongo", "memory"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="afl_test_placement", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _server(store, uuid, name, cpus=12.0, handlers=(FACET,), ping=None):
    store.save_server(
        ServerDefinition(
            uuid=uuid,
            server_group="g",
            service_name="runner",
            server_name=name,
            start_time=1,
            ping_time=_now_ms() if ping is None else ping,
            state=ServerState.RUNNING,
            task_list="",
            handlers=list(handlers),
            resources={"cpus": cpus},
        )
    )


def _task(uuid, state=TaskState.PENDING, cpus=3.0, prefer="fast", name=FACET):
    return TaskDefinition(
        uuid=uuid,
        name=name,
        runner_id="r1",
        workflow_id="w1",
        flow_id="f1",
        step_id=uuid,
        state=state,
        task_list_name="ns",
        requires={"cpus": cpus} if cpus else {},
        placement={"prefer": prefer} if prefer else {},
    )


def _fleet(store, pending, fast_s=3808.0, slow_s=26884.0):
    _server(store, "f1", FAST, cpus=16.0)
    _server(store, "s1", SLOW, cpus=12.0)
    for _ in range(3):
        store.record_task_timing(FACET, FAST, fast_s, 3.0)
        store.record_task_timing(FACET, SLOW, slow_s, 3.0)
    for i in range(pending):
        store.save_task(_task(f"p{i}"))


# -- the rule ---------------------------------------------------------------------


def test_unmeasured_or_fastest_hosts_always_claim():
    assert should_claim(None, [(5, 100.0)], 1)
    assert should_claim(500.0, [], 1)


def test_a_long_queue_puts_slow_hosts_to_work():
    # 5 fast slots at 3,808 s drain 47 tasks in ~35,800 s: a 26,884 s chunk
    # started now on the slow host finishes first.
    assert should_claim(26884.0, [(5, 3808.0)], 47)


def test_a_short_queue_is_left_to_the_fast_host():
    # 5 tasks left: the fast host finishes them by ~7,600 s.
    assert not should_claim(26884.0, [(5, 3808.0)], 5)
    assert not should_claim(10629.0, [(5, 3808.0)], 5)


def test_the_crossover_follows_the_speed_ratio():
    # Claim iff pending >= slots * (slow/fast - 1): 5 * (4 - 1) = 15.
    assert should_claim(4000.0, [(5, 1000.0)], 15)
    assert not should_claim(4000.0, [(5, 1000.0)], 14)


# -- store inputs -----------------------------------------------------------------


def test_timings_are_per_host_per_cpu_floor_newest_first(store):
    store.record_task_timing(FACET, FAST, 10.0, 3.0)
    store.record_task_timing(FACET, FAST, 20.0, 3.0)
    store.record_task_timing(FACET, FAST, 99.0, 8.0)  # another floor
    store.record_task_timing(FACET, SLOW, 40.0, 3.0)
    store.record_task_timing("ns.Other", SLOW, 1.0, 3.0)
    got = store.facet_timings(FACET, 3.0)
    assert sorted(got[FAST]) == [10.0, 20.0] and got[SLOW] == [40.0]
    assert store.facet_timings(FACET, 8.0) == {FAST: [99.0]}
    assert len(store.facet_timings(FACET, 3.0, per_host=1)[FAST]) == 1


def test_demand_counts_only_pending_fast_placement_tasks(store):
    store.save_task(_task("a"))
    store.save_task(_task("b"))
    store.save_task(_task("c", state=TaskState.RUNNING))
    store.save_task(_task("d", prefer=""))
    assert store.prefer_fast_demand() == {FACET: (2, 3.0)}


def test_live_hosts_need_a_fresh_ping_and_the_handler(store):
    _server(store, "a", FAST, cpus=16.0)
    _server(store, "b", SLOW, cpus=12.0, ping=1)  # stale
    _server(store, "c", "other", handlers=("ns.Else",))
    assert store.live_host_cpus(FACET) == {FAST: 16.0}


def test_placement_survives_the_round_trip(store):
    store.save_task(_task("rt"))
    assert store.get_task("rt").placement == {"prefer": "fast"}


# -- the decision -----------------------------------------------------------------


def test_near_the_end_the_slow_host_declines_and_the_fast_one_does_not(store):
    _fleet(store, pending=5)
    assert declined_facets(store, SLOW) == {FACET}
    assert declined_facets(store, FAST) == set()


def test_early_in_a_run_every_host_works(store):
    _fleet(store, pending=47)
    assert declined_facets(store, SLOW) == set()


def test_a_dead_fast_host_does_not_hold_work_back(store):
    _fleet(store, pending=5)
    _server(store, "f1", FAST, cpus=16.0, ping=1)
    assert declined_facets(store, SLOW) == set()


def test_a_host_with_no_timing_yet_claims(store):
    _fleet(store, pending=5)
    _server(store, "n1", "new-host")
    assert declined_facets(store, "new-host") == set()


def test_the_gate_strips_declined_names_and_can_be_switched_off(store, monkeypatch):
    _fleet(store, pending=5)
    gate = PlacementGate(store, SLOW)
    claims = [([FACET, "ns.Other"], ["ns"], {"x": 1})]
    assert gate.strip(claims) == [(["ns.Other"], ["ns"], {"x": 1})]
    monkeypatch.setenv("FW_CLAIM_PLACEMENT", "0")
    assert PlacementGate(store, SLOW).strip(claims) == claims


def test_an_empty_queue_is_not_cached(store):
    # The gate looked before the run's chunks existed; they then appear all at
    # once. The slow host must judge them on its next look, not after the TTL.
    _fleet(store, pending=0)
    gate = PlacementGate(store, SLOW, clock=lambda: 100.0)
    assert gate.declined() == set()
    for i in range(5):
        store.save_task(_task(f"q{i}"))
    assert gate.declined() == {FACET}


def test_a_waiting_queue_is_cached_for_the_ttl(store):
    _fleet(store, pending=5)
    now = [100.0]
    gate = PlacementGate(store, SLOW, clock=lambda: now[0])
    assert gate.declined() == {FACET}
    for i in range(60):
        store.save_task(_task(f"q{i}"))
    assert gate.declined() == {FACET}  # still cached
    now[0] += 31
    assert gate.declined() == set()


def test_the_claimer_on_a_slow_host_leaves_the_last_tasks(store):
    _fleet(store, pending=5)
    store.save_task(_task("plain", prefer="", name="ns.Other"))
    c = Claimer(store, server_name=SLOW, min_gap_s=0.0)
    c._register()
    c._waiters.append(
        Waiter(server_id="s1", claims=[([FACET, "ns.Other"], ["ns"])], resources={"cpus": 12.0})
    )
    assert c.cycle() is True
    assert store.get_task("plain").state == TaskState.RUNNING
    assert all(store.get_task(f"p{i}").state == TaskState.PENDING for i in range(5))


def test_only_placement_tasks_are_timed(store):
    record_timing(store, _task("t"), FAST, 4000)
    record_timing(store, _task("u", prefer=""), FAST, 4000)
    assert store.facet_timings(FACET, 3.0) == {FAST: [4.0]}


# -- FFL --------------------------------------------------------------------------

SRC = """
namespace demo.model {
    event facet Search(workers: Int) => (n: Int)
        with Requires(cpus = "$.workers") with Placement(prefer = "fast")
    event facet Plain(x: Int) => (n: Int)
}
namespace demo.workflows {
    use demo.model
    workflow Run(w: Int = 3) => (n: Int) andThen {
        s = demo.model.Search(workers = $.w)
        p = demo.model.Plain(x = 1) with Placement(prefer = "fast")
        yield Run(n = s.n)
    }
}
"""


def test_placement_is_not_mistaken_for_a_typo():
    """`Placement` is near a domain's `Plan`; the near-match warning must not fire."""
    from facetwork.validator import validate

    src = """
namespace demo {
    event facet Plan(x: Int) => (n: Int)
    event facet Search(x: Int) => (n: Int) with Placement(prefer = "fast")
}
"""
    result = validate(FFLParser().parse(src))
    assert not [w for w in result.warnings if "Placement" in w.message]


def test_placement_mixin_reaches_the_task():
    program = emit_dict(FFLParser().parse(SRC))
    store = MemoryStore()
    result = Evaluator(persistence=store, telemetry=Telemetry(enabled=False)).execute(
        find_workflow(program, "demo.workflows.Run"),
        inputs={"w": 3},
        program_ast=program,
        qualified_workflow_name="demo.workflows.Run",
    )
    tasks = {t.name: t for t in store.get_tasks_by_workflow(result.workflow_id)}
    assert tasks["demo.model.Search"].placement == {"prefer": "fast"}
    assert tasks["demo.model.Search"].requires == {"cpus": 3.0}
    assert tasks["demo.model.Plain"].placement == {"prefer": "fast"}  # call site
