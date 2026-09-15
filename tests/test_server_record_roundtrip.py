"""Every ServerDefinition field must survive persistence.

⚠️ Written because one did not. `image` was added to the dataclass with a
default_factory (so no CALLER could omit it — the contract the `container`
field's docstring spells out), populated correctly inside every container, then
silently dropped by MongoStore._server_to_doc, which is an EXPLICIT field list.
`fleet status` read "(image unreported)" across the whole fleet while the value
sat in the process: a stale-image check deployed and inert.

⚠️ This asserts BEHAVIOUR, not source text. The first version of this test
grepped the module for the field name, which passed even with the write removed
— the name still appeared in the read-back path. A test that cannot fail is
worse than no test, because it is counted as coverage.
"""
import dataclasses

import pytest

from facetwork.runtime.entities.server import ServerDefinition
from facetwork.runtime.mongo_store.servers import ServerMixin


def _doc_for(**over) -> dict:
    s = ServerDefinition(uuid="u1", server_group="g", service_name="svc",
                         server_name="h1", **over)
    return ServerMixin._server_to_doc(object.__new__(ServerMixin), s)


def test_every_dataclass_field_is_written():
    doc = _doc_for()
    missing = [f.name for f in dataclasses.fields(ServerDefinition) if f.name not in doc]
    assert not missing, (
        f"ServerDefinition fields dropped by _server_to_doc: {missing}. That dict is "
        f"an explicit field list, so a new field is lost silently — the value exists "
        f"in the process and never reaches the database."
    )


def test_image_value_survives_the_write():
    doc = _doc_for(image="reg:5050/facetwork-runner:abc1234")
    assert doc.get("image") == "reg:5050/facetwork-runner:abc1234"


def test_absent_image_persists_as_empty_not_missing():
    """'' means UNKNOWN to fleet status, which must never be read as stale."""
    doc = _doc_for(image="")
    assert doc.get("image") == ""


def test_store_features_are_advertised_by_default():
    """⚠️ The migration gate reads this to decide whether the fleet can take a
    schema change. A build that does not advertise is treated as NOT ready, so
    an empty default would silently block every migration forever."""
    from facetwork.runtime.entities.server import store_features
    assert "task-index:flexible" in store_features()
    assert "task-index:flexible" in ServerDefinition(
        uuid="u1", server_group="g", service_name="svc", server_name="h1").store_features


def test_store_features_survive_the_write():
    doc = _doc_for(store_features=["task-index:flexible", "something-else"])
    assert doc.get("store_features") == ["task-index:flexible", "something-else"]


def test_store_features_survive_the_read_back():
    """The gate reads server DOCUMENTS. A value that is written but not read back
    reads as 'not supported' at every call site — the same shape as the `image`
    bug this file exists for, one layer further along."""
    doc = _doc_for(store_features=["task-index:flexible"])
    back = ServerMixin._doc_to_server(object.__new__(ServerMixin), doc)
    assert "task-index:flexible" in back.store_features


def test_a_runner_that_advertises_nothing_round_trips_as_empty():
    """Absent means DECLINE, so it must come back as [] rather than as a default
    that would make an old runner look capable."""
    doc = _doc_for()
    doc.pop("store_features", None)          # as an older runner's record would be
    back = ServerMixin._doc_to_server(object.__new__(ServerMixin), doc)
    assert back.store_features == []
