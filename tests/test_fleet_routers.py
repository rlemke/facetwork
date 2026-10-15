"""Per-region GraphHopper routers: lease-based placement across fleet-agents.

What is pinned here is the claim protocol (`scripts/lib/_helpers/_routers.py`):
leaderless, at most one new slot per host per cycle, a lapsed lease moves the
slot to another host, a removed region stops its container, and a container no
slot accounts for is stopped (two hosts must never both serve one slot).
"""

import importlib.util
import sys
from pathlib import Path

import mongomock
import pytest

_ROUTERS = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "_helpers" / "_routers.py"


def _load():
    spec = importlib.util.spec_from_file_location("_routers_under_test", _ROUTERS)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # @dataclass resolves its module through sys.modules
    spec.loader.exec_module(mod)
    return mod


rt = _load()


class FakeDocker:
    """Containers per project on one host; `healthy` decides what probe answers."""

    def __init__(self):
        self.running: dict[str, int] = {}
        self.healthy = True
        self.forced_state: str | None = None
        self.starts: list[str] = []

    def ops(self):
        return rt.Docker(start=self.start, state=self.state, probe=self.probe,
                         stop=self.stop, projects=lambda: set(self.running))

    def start(self, proj, region, profile, port, heap):
        self.running[proj] = port
        self.starts.append(proj)
        return True

    def state(self, proj):
        if self.forced_state and proj in self.running:
            return self.forced_state
        return "running" if proj in self.running else None

    def probe(self, port):
        return {"bbox": [0, 0, 1, 1]} if self.healthy and port in self.running.values() else None

    def stop(self, proj):
        self.running.pop(proj, None)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def db():
    return mongomock.MongoClient()["facetwork"]


def _host(db, name, clock, group="heavy", max_here=2, docker=None):
    d = docker or FakeDocker()
    r = rt.RouterReconciler(db, name, group, f"addr-{name}", d.ops(), max_here=max_here,
                            log=lambda m: None, now=clock)
    return r, d


def _want(db, region, replicas=1, groups=None):
    db[rt.DESIRED].insert_one({"_id": rt.desired_key(region, "car"), "region": region,
                               "profile": "car", "replicas": replicas,
                               "server_groups": groups or []})


def test_slug_is_a_valid_compose_project_name():
    assert rt.slug("north-america/us-major-roads", "car") == "fw-gh-north-america-us-major-roads-car"


def test_one_claim_per_cycle_spreads_regions_across_hosts(db):
    clock = Clock()
    for r in ("europe/a", "europe/b"):
        _want(db, r)
    a, da = _host(db, "A", clock)
    b, db_ = _host(db, "B", clock)
    assert a.reconcile()["claimed"] == "europe/a|car|0"
    assert b.reconcile()["claimed"] == "europe/b|car|0"  # A took one, B gets the other
    assert set(da.running) == {"fw-gh-europe-a-car"}
    assert set(db_.running) == {"fw-gh-europe-b-car"}


def test_slot_goes_ready_with_a_url_once_healthy(db):
    clock = Clock()
    _want(db, "europe/a")
    a, da = _host(db, "A", clock)
    da.healthy = False
    a.reconcile()
    a.reconcile()
    s = db[rt.SLOTS].find_one()
    assert s["state"] == "starting"
    da.healthy = True
    a.reconcile()
    s = db[rt.SLOTS].find_one()
    assert s["state"] == "ready" and s["url"] == f"http://addr-A:{rt.PORT_BASE}"
    assert s["bbox"] == [0, 0, 1, 1]


def test_a_lapsed_lease_moves_the_slot_and_the_old_host_stops_its_container(db):
    clock = Clock()
    _want(db, "europe/a")
    a, da = _host(db, "A", clock)
    b, db_ = _host(db, "B", clock)
    a.reconcile()
    assert b.reconcile()["claimed"] is None  # held and fresh
    clock.t += rt.LEASE_S + 1  # A went away and stopped renewing
    assert b.reconcile()["claimed"] == "europe/a|car|0"
    assert db[rt.SLOTS].find_one()["host"] == "B"
    # A comes back: it holds no slot, so its container is a stray.
    a.reconcile()
    assert not da.running and set(db_.running) == {"fw-gh-europe-a-car"}


def test_removing_a_region_stops_its_router(db):
    clock = Clock()
    _want(db, "europe/a")
    a, da = _host(db, "A", clock)
    a.reconcile()
    db[rt.DESIRED].delete_many({})
    a.reconcile()
    assert not da.running and db[rt.SLOTS].count_documents({}) == 0


def test_server_groups_keep_routers_off_other_hosts(db):
    clock = Clock()
    _want(db, "europe/a", groups=["heavy"])
    light, dl = _host(db, "L", clock, group="runner")
    assert light.reconcile()["claimed"] is None and not dl.running


def test_role_level_groups_apply_when_a_region_names_none(db):
    clock = Clock()
    _want(db, "europe/a")
    d = FakeDocker()
    light = rt.RouterReconciler(db, "L", "runner", "addr", d.ops(), default_groups=["heavy"],
                                log=lambda m: None, now=clock)
    assert light.reconcile()["claimed"] is None


def test_per_host_limit_and_one_replica_of_a_region_per_host(db):
    clock = Clock()
    _want(db, "europe/a", replicas=2)
    _want(db, "europe/b")
    _want(db, "europe/c")
    a, da = _host(db, "A", clock, max_here=2)
    for _ in range(5):
        a.reconcile()
    held = {s["_id"] for s in db[rt.SLOTS].find({"host": "A"})}
    assert len(held) == 2
    assert not {"europe/a|car|0", "europe/a|car|1"} <= held  # never both replicas here
    ports = [s["port"] for s in db[rt.SLOTS].find({"host": "A"})]
    assert len(set(ports)) == 2  # distinct published ports


def test_scaling_replicas_down_releases_the_extra_slot(db):
    clock = Clock()
    _want(db, "europe/a", replicas=2)
    a, _ = _host(db, "A", clock)
    b, db_ = _host(db, "B", clock)
    a.reconcile()
    b.reconcile()
    assert db[rt.SLOTS].count_documents({}) == 2
    db[rt.DESIRED].update_one({}, {"$set": {"replicas": 1}})
    a.reconcile()
    b.reconcile()
    assert [s["_id"] for s in db[rt.SLOTS].find()] == ["europe/a|car|0"]


def test_a_dead_container_is_restarted_and_a_crash_loop_reads_failed(db):
    clock = Clock()
    _want(db, "europe/a")
    a, da = _host(db, "A", clock)
    a.reconcile()
    da.running.clear()  # container gone
    assert a.reconcile()["started"] == ["fw-gh-europe-a-car"]

    da.forced_state = "restarting"
    a.reconcile()
    assert db[rt.SLOTS].find_one()["state"] == "failed"


def test_a_container_left_created_is_replaced(db):
    """A start that failed (port taken) leaves a 'created' container Docker
    never retries; the slot must not sit at 'starting' forever."""
    clock = Clock()
    _want(db, "europe/a")
    a, da = _host(db, "A", clock)
    a.reconcile()
    da.forced_state = "created"
    assert a.reconcile()["started"] == ["fw-gh-europe-a-car"]


def test_a_failed_start_reads_failed(db):
    clock = Clock()
    _want(db, "europe/a")
    a, da = _host(db, "A", clock)
    da.start = lambda *args: False
    a.docker = da.ops()
    a.reconcile()
    a.reconcile()
    assert db[rt.SLOTS].find_one()["state"] == "failed"


def test_ports_in_use_on_the_host_are_skipped(db):
    clock = Clock()
    _want(db, "europe/a")
    d = FakeDocker()
    ops = d.ops()
    ops.port_busy = lambda p: p == rt.PORT_BASE
    a = rt.RouterReconciler(db, "A", "heavy", "addr", ops, log=lambda m: None, now=clock)
    a.reconcile()
    assert db[rt.SLOTS].find_one()["port"] == rt.PORT_BASE + 1
