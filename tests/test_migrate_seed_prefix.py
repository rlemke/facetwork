"""Tests for the example: -> domain: seed-prefix migration (name-gated, idempotent)."""

from __future__ import annotations

import pytest

from facetwork.domains.migrate import migrate_seed_prefix


def _seed(store, path: str, name: str):
    """Insert a minimal flow + workflow under a given seed path."""
    store._db.flows.insert_one({"uuid": f"f-{name}", "name": {"name": name, "path": path}})
    store._db.workflows.insert_one(
        {"uuid": f"w-{name}", "name": f"{name}.W", "flow_id": f"f-{name}", "namespace_id": path}
    )


@pytest.fixture
def store():
    mongomock = pytest.importorskip("mongomock")
    from facetwork.runtime.mongo_store import MongoStore

    s = MongoStore(database_name="t_migrate", client=mongomock.MongoClient())
    yield s
    s.drop_database()
    s.close()


def test_migrates_only_domains(store):
    # A domain seed and a teaching-example seed, both under example:.
    _seed(store, "example:osm-geocoder", "osm-geocoder")  # domain
    _seed(store, "example:hello-agent", "hello-agent")    # teaching example (NOT in DOMAIN_NAMES)

    res = migrate_seed_prefix(store, apply=True, names=["osm-geocoder", "census-us"])
    assert res["names"] == ["osm-geocoder"]  # census-us had no seed; hello-agent not requested
    assert res["flows"] == 1 and res["workflows"] == 1

    # The domain flipped; the teaching example is untouched.
    assert store._db.flows.count_documents({"name.path": "domain:osm-geocoder"}) == 1
    assert store._db.flows.count_documents({"name.path": "example:osm-geocoder"}) == 0
    assert store._db.flows.count_documents({"name.path": "example:hello-agent"}) == 1
    assert store._db.workflows.count_documents({"namespace_id": "domain:osm-geocoder"}) == 1


def test_uuid_preserved(store):
    _seed(store, "example:osm-geocoder", "osm-geocoder")
    before = store._db.flows.find_one({"name.path": "example:osm-geocoder"})["uuid"]
    migrate_seed_prefix(store, apply=True, names=["osm-geocoder"])
    after = store._db.flows.find_one({"name.path": "domain:osm-geocoder"})["uuid"]
    assert after == before  # uuid never rewritten


def test_dry_run_writes_nothing(store):
    _seed(store, "example:osm-geocoder", "osm-geocoder")
    res = migrate_seed_prefix(store, apply=False, names=["osm-geocoder"])
    assert res["flows"] == 1  # reported…
    # …but nothing written.
    assert store._db.flows.count_documents({"name.path": "example:osm-geocoder"}) == 1
    assert store._db.flows.count_documents({"name.path": "domain:osm-geocoder"}) == 0


def test_idempotent(store):
    _seed(store, "example:osm-geocoder", "osm-geocoder")
    migrate_seed_prefix(store, apply=True, names=["osm-geocoder"])
    res = migrate_seed_prefix(store, apply=True, names=["osm-geocoder"])  # second run
    assert res["names"] == [] and res["flows"] == 0  # nothing left to migrate


def test_supersede_when_domain_already_seeded(store):
    # A new runner already seeded domain:osm-geocoder; the old example: one is stale.
    _seed(store, "example:osm-geocoder", "osm-geocoder-old")
    _seed(store, "domain:osm-geocoder", "osm-geocoder-new")
    migrate_seed_prefix(store, apply=True, names=["osm-geocoder"])
    # No duplicate: exactly one domain: flow (the new one), example: gone.
    assert store._db.flows.count_documents({"name.path": "domain:osm-geocoder"}) == 1
    assert store._db.flows.count_documents({"name.path": "example:osm-geocoder"}) == 0
    assert store._db.flows.find_one({"name.path": "domain:osm-geocoder"})["uuid"] == "f-osm-geocoder-new"
