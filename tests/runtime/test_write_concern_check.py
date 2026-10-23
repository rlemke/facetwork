"""Start-up write-concern check (facetwork.runtime.mongo_store.write_concern)."""

from __future__ import annotations

import logging

import pytest

from facetwork.runtime.mongo_store.write_concern import (
    UnsafeWriteConcernError,
    assess_write_concern,
    check_write_concern,
)


class _WC:
    def __init__(self, doc: dict) -> None:
        self.document = doc


class _Coll:
    def __init__(self, wc: dict) -> None:
        self.write_concern = _WC(wc)


class _Admin:
    def __init__(self, hello: dict, default: dict | Exception | None) -> None:
        self._hello = hello
        self._default = default

    def command(self, name: str) -> dict:
        if name == "hello":
            return self._hello
        if name == "getDefaultRWConcern":
            if isinstance(self._default, Exception):
                raise self._default
            return self._default or {}
        raise AssertionError(name)


class _Client:
    def __init__(self, hello: dict, default: dict | Exception | None) -> None:
        self.admin = _Admin(hello, default)


class _DB:
    def __init__(self, hello: dict, wc: dict | None = None, default=None) -> None:
        self.client = _Client(hello, default)
        self._wc = wc or {}

    def __getitem__(self, name: str) -> _Coll:
        return _Coll(self._wc)


STANDALONE = {"isWritablePrimary": True}
RS3 = {"isWritablePrimary": True, "setName": "rs0", "hosts": ["a:1", "b:1", "c:1"]}
MONGOS = {"isWritablePrimary": True, "msg": "isdbgrid"}


def test_standalone_with_no_concern_is_reported_not_refused(monkeypatch, caplog):
    monkeypatch.delenv("FW_REQUIRE_REPLICATED", raising=False)
    with caplog.at_level(logging.WARNING):
        r = check_write_concern(_DB(STANDALONE))
    assert (r.topology, r.verdict) == ("standalone", "standalone")
    assert "single point of failure" in caplog.text


def test_standalone_is_refused_in_a_production_profile(monkeypatch):
    monkeypatch.setenv("FW_REQUIRE_REPLICATED", "1")
    with pytest.raises(UnsafeWriteConcernError, match="FW_REQUIRE_REPLICATED"):
        check_write_concern(_DB(STANDALONE))


@pytest.mark.parametrize("hello", [STANDALONE, RS3, MONGOS])
def test_unacknowledged_writes_are_refused_on_every_topology(hello):
    with pytest.raises(UnsafeWriteConcernError, match="unacknowledged"):
        check_write_concern(_DB(hello, {"w": 0}))


def test_replica_set_below_majority_is_refused():
    with pytest.raises(UnsafeWriteConcernError, match="below majority"):
        check_write_concern(_DB(RS3, {"w": 1}))


@pytest.mark.parametrize("w", ["majority", 2, 3])
def test_replica_set_at_majority_passes(w):
    assert check_write_concern(_DB(RS3, {"w": w})).verdict == "ok"


def test_replica_set_reads_the_servers_implicit_default_when_the_client_sets_none():
    db = _DB(RS3, {}, {"defaultWriteConcern": {"w": "majority"}, "defaultWriteConcernSource": "implicit"})
    r = check_write_concern(db)
    assert (r.verdict, r.source) == ("ok", "implicit")


def test_a_server_default_below_majority_is_refused():
    db = _DB(RS3, {}, {"defaultWriteConcern": {"w": 1}, "defaultWriteConcernSource": "global"})
    with pytest.raises(UnsafeWriteConcernError, match="server-default"):
        check_write_concern(db)


def test_an_unreadable_server_default_is_unverified_never_ok(caplog):
    db = _DB(RS3, {}, RuntimeError("not authorized"))
    with caplog.at_level(logging.WARNING):
        r = check_write_concern(db)
    assert r.verdict == "unverified"
    assert "UNVERIFIED" in caplog.text


def test_a_tag_set_concern_is_not_assumed_to_be_majority():
    assert assess_write_concern(_DB(RS3, {"w": "dc-tags"})).verdict == "unsafe"


def test_sharded_cluster_follows_the_replica_set_rule():
    assert assess_write_concern(_DB(MONGOS, {"w": "majority"})).verdict == "ok"
    assert assess_write_concern(_DB(MONGOS, {"w": 1})).verdict == "unsafe"
