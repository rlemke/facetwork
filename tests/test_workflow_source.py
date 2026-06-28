# Copyright 2025 Ralph Lemke
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for facetwork.workflow_source.reconstruct_workflow_source."""

from __future__ import annotations

import glob

import pytest

from facetwork.ast_utils import find_all_workflows, normalize_program_ast
from facetwork.emitter import emit_dict
from facetwork.parser import parse
from facetwork.workflow_source import (
    WorkflowNotFoundError,
    reconstruct_workflow_source,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _compile(src: str) -> dict:
    return normalize_program_ast(emit_dict(parse(src), include_locations=False))


def _norm(node):
    """Strip volatile ids/locations and normalize script trailing whitespace.

    Trailing whitespace inside an embedded ``script`` block is insignificant
    Python and is normalized away by the brace-form rendering, so it is not a
    meaningful difference for round-trip comparison.
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in ("id", "location"):
                continue
            if k == "code" and isinstance(v, str):
                out[k] = "\n".join(line.rstrip() for line in v.rstrip().splitlines())
            else:
                out[k] = _norm(v)
        return out
    if isinstance(node, list):
        return [_norm(x) for x in node]
    return node


def _qname(program: dict, wf: dict) -> str:
    for decl in program.get("declarations", []):
        if decl.get("type") == "Namespace":
            for child in decl.get("declarations", []):
                if child is wf:
                    return f"{decl['name']}.{wf['name']}"
        elif decl is wf:
            return wf["name"]
    return wf["name"]


# ---------------------------------------------------------------------------
# focused behavioural tests
# ---------------------------------------------------------------------------

_MULTI_NS = """
namespace lib.types {
  schema Geo { lat: Double, lon: Double }
  schema Unused { x: Long }
}
namespace lib.ops {
  use lib.types
  facet Locate(addr: String) => (geo: Geo)
  facet NeverUsed(x: Long) => (y: Long)
}
namespace app {
  use lib.ops
  use lib.types
  event facet Render(geo: Geo) => (html: String)
  facet AlsoUnused() => (z: Long)
  workflow ShowMap(addr: String) => (html: String) andThen {
    g = Locate(addr = $.addr)
    r = Render(geo = g.geo)
    yield ShowMap(html = r.html)
  }
  workflow OtherFlow() => (z: Long) andThen {
    a = AlsoUnused()
    yield OtherFlow(z = a.z)
  }
}
"""


def test_includes_cross_namespace_references():
    ffl = reconstruct_workflow_source(_compile(_MULTI_NS), "app.ShowMap")
    for needed in (
        "workflow ShowMap",
        "facet Locate",
        "event facet Render",
        "schema Geo",
        "namespace lib.types",
        "namespace lib.ops",
        "use lib.types",
        "use lib.ops",
    ):
        assert needed in ffl, f"expected {needed!r} in reconstructed source"


def test_omits_unreferenced_declarations():
    ffl = reconstruct_workflow_source(_compile(_MULTI_NS), "app.ShowMap")
    for omitted in ("Unused", "NeverUsed", "AlsoUnused", "OtherFlow"):
        assert omitted not in ffl, f"{omitted!r} should not appear"


def test_reconstruction_recompiles_identically():
    program = _compile(_MULTI_NS)
    ffl = reconstruct_workflow_source(program, "app.ShowMap")
    recompiled = _compile(ffl)
    original = next(w for w in find_all_workflows(program) if w["name"] == "ShowMap")
    rebuilt = next(w for w in find_all_workflows(recompiled) if w["name"] == "ShowMap")
    assert _norm(original) == _norm(rebuilt)


def test_simple_name_resolves():
    ffl = reconstruct_workflow_source(_compile(_MULTI_NS), "ShowMap")
    assert "workflow ShowMap" in ffl


def test_missing_workflow_raises():
    with pytest.raises(WorkflowNotFoundError):
        reconstruct_workflow_source(_compile(_MULTI_NS), "DoesNotExist")


def test_preserves_operator_precedence_parens():
    # (a + 1) * 100 must keep its parens — without them `*` would rebind.
    src = """
    namespace n {
      facet C(below: Double) => (penalty: Double)
      workflow W(x: Double) => (out: String) andThen {
        c = C(below = -0.15)
        yield W(out = "p " ++ (c.penalty + 1) * 100 ++ "%")
      }
    }
    """
    program = _compile(src)
    ffl = reconstruct_workflow_source(program, "n.W")
    assert "(c.penalty + 1) * 100" in ffl
    original = next(w for w in find_all_workflows(program) if w["name"] == "W")
    rebuilt = next(w for w in find_all_workflows(_compile(ffl)) if w["name"] == "W")
    assert _norm(original) == _norm(rebuilt)


def test_ambiguous_simple_name_raises():
    src = """
    namespace a { workflow Dup() => (x: Long) andThen { s = a.Dup() yield Dup(x = 1) } }
    namespace b { workflow Dup() => (x: Long) andThen { s = b.Dup() yield Dup(x = 1) } }
    """
    with pytest.raises(WorkflowNotFoundError):
        reconstruct_workflow_source(_compile(src), "Dup")


# ---------------------------------------------------------------------------
# corpus round-trip: every example workflow reconstructs to valid, equivalent FFL
# ---------------------------------------------------------------------------


def _corpus_cases():
    cases = []
    for path in sorted(glob.glob("examples/**/*.ffl", recursive=True)):
        try:
            program = _compile(open(path).read())
        except Exception:
            continue  # examples that need external libraries to parse — skip
        for wf in find_all_workflows(program):
            cases.append((path, wf["name"]))
    return cases


@pytest.mark.parametrize("path,wf_name", _corpus_cases())
def test_corpus_roundtrip(path, wf_name):
    program = _compile(open(path).read())
    wf = next(w for w in find_all_workflows(program) if w["name"] == wf_name)
    ffl = reconstruct_workflow_source(program, _qname(program, wf))

    # 1. The reconstruction is valid FFL.
    recompiled = _compile(ffl)

    # 2. The workflow's structure survives the round-trip.
    rebuilt = next(w for w in find_all_workflows(recompiled) if w["name"] == wf_name)
    assert _norm(wf) == _norm(rebuilt)
