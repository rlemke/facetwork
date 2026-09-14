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
