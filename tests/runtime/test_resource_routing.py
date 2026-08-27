"""A runner must not claim work it cannot finish.

Motivation, measured 2026-08-26: MaxPro (7.75 GiB Docker VM) and server3
(13.63 GiB) are BOTH in the `heavy` server group, so group-based placement could
not tell them apart. MaxPro claimed a German Kreise split, OOM'd at a 5.8 GB
budget, failed, and only then retried onto server3. Resource-aware claim routing
turns that accept-then-fail into a skip.

Follows the two capability dimensions the claim already had (environment_hash,
required_features): the task carries a requirement, the runner advertises what it
provides, absent means unconstrained.
"""
import pytest

from facetwork.runtime.entities.task import TaskDefinition
from facetwork.runtime.memory_store import MemoryStore

BIG = {"memory_gb": 13.6, "cpus": 12, "scratch_gb": 3400}
SMALL = {"memory_gb": 7.75, "cpus": 12, "scratch_gb": 1600}


def _store(**requires):
    s = MemoryStore()
    s.save_task(TaskDefinition(uuid="t1", name="osm.planet.BuildAdminSet", runner_id="r",
                               workflow_id="w", flow_id="f", step_id="s",
                               task_list_name="osm", requires=requires))
    return s


def _claim(store, resources):
    return store.claim_task(task_names=["osm.planet.BuildAdminSet"], task_list="osm",
                            server_id="srv", resources=resources)


def test_a_task_with_no_requirements_is_claimable_by_anyone():
    """Absent means unconstrained — this must stay inert for existing work."""
    assert _claim(_store(), SMALL) is not None
    assert _claim(_store(), None) is not None


def test_the_small_host_declines_what_it_cannot_finish():
    """The exact 2026-08-26 case: 10 GB floor vs MaxPro's 7.75 GiB."""
    assert _claim(_store(memory_gb=10), SMALL) is None


def test_the_capable_host_takes_it():
    assert _claim(_store(memory_gb=10), BIG) is not None


def test_a_requirement_met_exactly_is_claimable():
    assert _claim(_store(memory_gb=7.75), SMALL) is not None


def test_every_dimension_must_be_met():
    """Meeting memory but not scratch is still a decline."""
    assert _claim(_store(memory_gb=4, scratch_gb=9999), BIG) is None


def test_an_unadvertised_dimension_blocks_the_claim():
    """A runner that cannot measure a dimension must NOT assume it passes.

    Ignoring unknown dimensions would silently reintroduce accept-then-fail,
    which is the whole failure this prevents.
    """
    assert _claim(_store(gpu_count=1), BIG) is None


def test_a_runner_advertising_nothing_declines_all_requirements():
    assert _claim(_store(memory_gb=1), {}) is None
    # ...but still takes unconstrained work, so an un-probed runner is not idled.
    assert _claim(_store(), {}) is not None


# --- the two stores must agree; the Mongo one is what actually runs ----------
mongomock = pytest.importorskip("mongomock")
from facetwork.runtime.mongo_store import MongoStore  # noqa: E402

CASES = [
    ({},                              SMALL, True,  "unconstrained -> anyone"),
    ({"memory_gb": 10},               SMALL, False, "small host declines"),
    ({"memory_gb": 10},               BIG,   True,  "capable host takes it"),
    ({"memory_gb": 7.75},             SMALL, True,  "exact fit"),
    ({"memory_gb": 4, "scratch_gb": 9999}, BIG, False, "one dimension fails -> decline"),
    ({"gpu_count": 1},                BIG,   False, "unadvertised dimension blocks"),
    ({"memory_gb": 1},                {},    False, "runner advertising nothing declines"),
]


@pytest.mark.parametrize("requires,resources,expected,why", CASES)
def test_mongo_store_matches_memory_store(requires, resources, expected, why):
    ms = MongoStore(database_name="t_res", client=mongomock.MongoClient())
    ms.save_task(TaskDefinition(uuid="t1", name="osm.planet.BuildAdminSet", runner_id="r",
                                workflow_id="w", flow_id="f", step_id="s",
                                task_list_name="osm", requires=requires))
    got = ms.claim_task(task_names=["osm.planet.BuildAdminSet"], task_list="osm",
                        server_id="srv", resources=resources)
    assert (got is not None) is expected, f"MongoStore disagrees: {why}"

    mem = MemoryStore()
    mem.save_task(TaskDefinition(uuid="t1", name="osm.planet.BuildAdminSet", runner_id="r",
                                 workflow_id="w", flow_id="f", step_id="s",
                                 task_list_name="osm", requires=requires))
    got2 = mem.claim_task(task_names=["osm.planet.BuildAdminSet"], task_list="osm",
                          server_id="srv", resources=resources)
    assert (got2 is not None) is expected, f"MemoryStore disagrees: {why}"


def test_requires_survives_the_round_trip():
    """The field must actually persist — required_features did NOT, so the
    feature-routing filter queried a field nothing ever wrote (0 of 15,600 live
    task docs carried it)."""
    ms = MongoStore(database_name="t_rt", client=mongomock.MongoClient())
    ms.save_task(TaskDefinition(uuid="t9", name="n", runner_id="r", workflow_id="w",
                                flow_id="f", step_id="s",
                                requires={"memory_gb": 10}, required_features=["after"]))
    back = ms.get_task("t9")
    assert back.requires == {"memory_gb": 10}
    assert back.required_features == ["after"]
