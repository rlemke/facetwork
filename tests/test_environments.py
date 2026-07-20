"""Tests for environment manifest freezing (facetwork/environments.py)."""

from __future__ import annotations

import json

import pytest

from facetwork import parse
from facetwork.emitter import JSONEmitter
from facetwork.environments import (
    annotate_program,
    environment_for_decl,
    freeze_environment,
    manifest_hash,
)


def _compiled(src: str) -> dict:
    return json.loads(JSONEmitter(include_locations=False).emit(parse(src)))


SRC = """
namespace geo {
    environment PyGeo {
        language = "python",
        requires = ["shapely==2.0.4", "networkx==3.3"]
    }
    event facet B(x: String) => (y: String) in environment PyGeo script { "result['y']=1" }
}
namespace app {
    use geo
    workflow W(x: String) => (y: String) in environment PyGeo andThen {
        s = geo.B(x = $.x)
        yield W(y = s.y)
    }
}
"""


class TestFreeze:
    def test_exact_pins_pass_through_without_network(self):
        m = freeze_environment(
            {"language": "python", "requires": ["shapely==2.0.4", "networkx==3.3"]}
        )
        assert m["pins"] == ["shapely==2.0.4", "networkx==3.3"]
        assert m["resolved"] is True

    def test_foreign_language_is_opaque(self):
        m = freeze_environment({"language": "scala", "requires": ["org.foo:bar:1.2"]})
        assert m["pins"] == ["org.foo:bar:1.2"]
        assert m["resolved"] is True

    def test_loose_specs_freeze_unresolved_when_disabled(self, monkeypatch):
        monkeypatch.setenv("FW_ENV_RESOLVE", "off")
        m = freeze_environment({"language": "python", "requires": ["shapely>=2.0"]})
        assert m["pins"] == ["shapely>=2.0"]
        assert m["resolved"] is False

    def test_hash_deterministic_and_identity_only(self):
        a = manifest_hash({"language": "python", "pins": ["b==1", "a==2"]})
        b = manifest_hash({"language": "python", "pins": ["a==2", "b==1"], "requires": ["x"]})
        c = manifest_hash({"language": "python", "pins": ["a==2", "b==3"]})
        assert a == b  # pin order and provenance fields don't change identity
        assert a != c


class TestAnnotate:
    def test_annotate_program_injects_manifest(self):
        d = _compiled(SRC)
        hashes = annotate_program(d)
        assert set(hashes) == {"geo.PyGeo"}
        env = environment_for_decl(d, "PyGeo", "geo")
        assert env is not None
        assert env["manifest_hash"] == hashes["geo.PyGeo"]
        assert env["manifest"]["pins"] == ["shapely==2.0.4", "networkx==3.3"]

    def test_annotate_is_idempotent(self):
        d = _compiled(SRC)
        first = annotate_program(d)
        env = environment_for_decl(d, "PyGeo", "geo")
        env["manifest"]["pins"] = ["tampered==0"]  # would change hash if re-frozen
        second = annotate_program(d)
        assert first == second  # untouched — no re-resolution of stored ASTs

    def test_resolution_through_use_graph(self):
        d = _compiled(SRC)
        annotate_program(d)
        # app has `use geo` — short name resolves through the import
        env = environment_for_decl(d, "PyGeo", "app")
        assert env is not None and env["name"] == "PyGeo"
        # qualified always works
        env_q = environment_for_decl(d, "geo.PyGeo", "elsewhere")
        assert env_q is not None
        # unknown → None
        assert environment_for_decl(d, "Nope", "app") is None
