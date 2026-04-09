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

"""Tests for afl.ast_utils — normalize_program_ast, find_workflow, find_all_workflows."""

import copy

from facetwork.ast_utils import find_all_workflows, find_workflow, normalize_program_ast

# ---------------------------------------------------------------------------
# Fixtures — sample program dicts
# ---------------------------------------------------------------------------

_WF_HELLO = {"type": "WorkflowDecl", "name": "Hello", "params": []}
_WF_GOODBYE = {"type": "WorkflowDecl", "name": "Goodbye", "params": []}
_FACET_A = {"type": "FacetDecl", "name": "FacetA", "params": []}
_EVENT_B = {"type": "EventFacetDecl", "name": "EventB", "params": []}
_SCHEMA_S = {"type": "SchemaDecl", "name": "MySchema", "fields": []}
_IMPLICIT_I = {"type": "ImplicitDecl", "name": "impl", "value": "x"}


def _ns(name, **contents):
    """Build a namespace dict with the given categorized contents."""
    node = {"type": "Namespace", "name": name}
    for key, items in contents.items():
        node[key] = items
    return node


def _ns_decl(name, declarations):
    """Build a namespace dict using only the declarations key."""
    return {"type": "Namespace", "name": name, "declarations": declarations}


# ---------------------------------------------------------------------------
# normalize_program_ast
# ---------------------------------------------------------------------------


class TestNormalizeProgramAst:
    """Tests for normalize_program_ast."""

    def test_empty_program(self):
        result = normalize_program_ast({"type": "Program"})
        assert result == {"type": "Program"}
        assert "workflows" not in result
        assert "declarations" not in result

    def test_categorized_only_builds_declarations(self):
        """When only categorized keys exist, declarations is built from them."""
        prog = {
            "type": "Program",
            "workflows": [_WF_HELLO],
            "facets": [_FACET_A],
            "eventFacets": [_EVENT_B],
            "schemas": [_SCHEMA_S],
        }
        result = normalize_program_ast(prog)

        assert "workflows" not in result
        assert "facets" not in result
        assert "eventFacets" not in result
        assert "schemas" not in result
        assert "declarations" in result

        types = [d["type"] for d in result["declarations"]]
        assert "FacetDecl" in types
        assert "EventFacetDecl" in types
        assert "WorkflowDecl" in types
        assert "SchemaDecl" in types

    def test_both_formats_strips_categorized(self):
        """When both formats present (current emitter output), categorized keys are stripped."""
        prog = {
            "type": "Program",
            "workflows": [_WF_HELLO],
            "facets": [_FACET_A],
            "declarations": [_FACET_A, _WF_HELLO],
        }
        result = normalize_program_ast(prog)

        assert "workflows" not in result
        assert "facets" not in result
        assert result["declarations"] == [_FACET_A, _WF_HELLO]

    def test_idempotent(self):
        """Normalizing an already-normalized dict returns the same structure."""
        prog = {
            "type": "Program",
            "declarations": [_WF_HELLO, _FACET_A],
        }
        first = normalize_program_ast(prog)
        second = normalize_program_ast(first)
        assert first == second

    def test_does_not_mutate_input(self):
        prog = {
            "type": "Program",
            "workflows": [_WF_HELLO],
            "declarations": [_WF_HELLO],
        }
        original = copy.deepcopy(prog)
        normalize_program_ast(prog)
        assert prog == original

    def test_nested_namespace_normalization(self):
        """Namespace nodes inside declarations are recursively normalized."""
        ns_node = _ns("myns", workflows=[_WF_HELLO], facets=[_FACET_A])
        # Give the namespace a declarations key too (like the emitter does)
        ns_node["declarations"] = [_FACET_A, _WF_HELLO]

        prog = {
            "type": "Program",
            "namespaces": [ns_node],
            "declarations": [ns_node],
        }
        result = normalize_program_ast(prog)

        assert "namespaces" not in result
        ns_out = result["declarations"][0]
        assert ns_out["type"] == "Namespace"
        assert "workflows" not in ns_out
        assert "facets" not in ns_out
        assert len(ns_out["declarations"]) == 2

    def test_namespace_without_declarations_key(self):
        """Namespace that only has categorized keys gets declarations built."""
        ns_node = _ns("myns", workflows=[_WF_HELLO], eventFacets=[_EVENT_B])

        prog = {
            "type": "Program",
            "declarations": [ns_node],
        }
        result = normalize_program_ast(prog)

        ns_out = result["declarations"][0]
        assert "workflows" not in ns_out
        assert "eventFacets" not in ns_out
        assert len(ns_out["declarations"]) == 2

    def test_preserves_metadata(self):
        prog = {
            "type": "Program",
            "id": "test-123",
            "location": {"line": 1, "column": 1},
            "workflows": [_WF_HELLO],
            "declarations": [_WF_HELLO],
        }
        result = normalize_program_ast(prog)
        assert result["type"] == "Program"
        assert result["id"] == "test-123"
        assert result["location"] == {"line": 1, "column": 1}

    def test_preserves_uses(self):
        ns_node = {
            "type": "Namespace",
            "name": "myns",
            "uses": ["other"],
            "declarations": [_WF_HELLO],
        }
        prog = {
            "type": "Program",
            "declarations": [ns_node],
        }
        result = normalize_program_ast(prog)
        assert result["declarations"][0]["uses"] == ["other"]


# ---------------------------------------------------------------------------
# find_workflow
# ---------------------------------------------------------------------------


class TestFindWorkflow:
    """Tests for find_workflow."""

    def test_simple_name_top_level(self):
        prog = {"type": "Program", "declarations": [_WF_HELLO, _WF_GOODBYE]}
        assert find_workflow(prog, "Hello") is _WF_HELLO
        assert find_workflow(prog, "Goodbye") is _WF_GOODBYE

    def test_simple_name_inside_namespace(self):
        ns = _ns_decl("myns", [_WF_HELLO])
        prog = {"type": "Program", "declarations": [ns]}
        assert find_workflow(prog, "Hello") is _WF_HELLO

    def test_qualified_name_flat_match(self):
        ns = _ns_decl("myns", [_WF_HELLO])
        prog = {"type": "Program", "declarations": [ns]}
        assert find_workflow(prog, "myns.Hello") is _WF_HELLO

    def test_qualified_name_nested_navigation(self):
        inner_ns = _ns_decl("inner", [_WF_HELLO])
        outer_ns = _ns_decl("outer", [inner_ns])
        prog = {"type": "Program", "declarations": [outer_ns]}
        assert find_workflow(prog, "outer.inner.Hello") is _WF_HELLO

    def test_not_found_returns_none(self):
        prog = {"type": "Program", "declarations": [_WF_HELLO]}
        assert find_workflow(prog, "Missing") is None
        assert find_workflow(prog, "ns.Missing") is None

    def test_works_on_normalized_input(self):
        prog = {
            "type": "Program",
            "declarations": [_WF_HELLO],
        }
        normalized = normalize_program_ast(prog)
        assert find_workflow(normalized, "Hello") is not None
        assert find_workflow(normalized, "Hello")["name"] == "Hello"


# ---------------------------------------------------------------------------
# find_all_workflows
# ---------------------------------------------------------------------------


class TestFindAllWorkflows:
    """Tests for find_all_workflows."""

    def test_collects_top_level(self):
        prog = {"type": "Program", "declarations": [_WF_HELLO, _WF_GOODBYE]}
        result = find_all_workflows(prog)
        assert len(result) == 2
        assert _WF_HELLO in result
        assert _WF_GOODBYE in result

    def test_collects_from_namespaces(self):
        ns = _ns_decl("myns", [_WF_HELLO])
        prog = {"type": "Program", "declarations": [_WF_GOODBYE, ns]}
        result = find_all_workflows(prog)
        assert len(result) == 2

    def test_empty_program(self):
        result = find_all_workflows({"type": "Program"})
        assert result == []

    def test_nested_namespaces(self):
        inner = _ns_decl("inner", [_WF_HELLO])
        outer = _ns_decl("outer", [inner, _WF_GOODBYE])
        prog = {"type": "Program", "declarations": [outer]}
        result = find_all_workflows(prog)
        names = {w["name"] for w in result}
        assert names == {"Hello", "Goodbye"}


# ---------------------------------------------------------------------------
# Integration: compile → normalize → find round-trip
# ---------------------------------------------------------------------------


class TestCompileNormalizeFindRoundTrip:
    """End-to-end: parse FFL → emit → normalize → find."""

    def test_simple_workflow(self):
        from facetwork import emit_dict, parse

        ast = parse('workflow Hello() => (msg: String) andThen { yield Hello(msg = "hi") }')
        compiled = emit_dict(ast)
        normalized = normalize_program_ast(compiled)

        assert "workflows" not in normalized
        assert "declarations" in normalized

        wf = find_workflow(normalized, "Hello")
        assert wf is not None
        assert wf["name"] == "Hello"

    def test_namespaced_workflow(self):
        from facetwork import emit_dict, parse

        source = """
namespace myns {
    workflow Inner() => (x: Int) andThen { yield Inner(x = 1) }
}
"""
        ast = parse(source)
        compiled = emit_dict(ast)
        normalized = normalize_program_ast(compiled)

        assert find_workflow(normalized, "Inner") is not None
        assert find_workflow(normalized, "myns.Inner") is not None

    def test_find_all_from_compiled(self):
        from facetwork import emit_dict, parse

        source = """
workflow A() => (x: Int) andThen { yield A(x = 1) }
namespace ns {
    workflow B() => (y: Int) andThen { yield B(y = 2) }
}
"""
        ast = parse(source)
        compiled = emit_dict(ast)
        all_wfs = find_all_workflows(compiled)
        names = {w["name"] for w in all_wfs}
        assert names == {"A", "B"}


# ---------------------------------------------------------------------------
# Normalization of namespace eventFacets
# ---------------------------------------------------------------------------


class TestNormalizeNamespaceEventFacets:
    """Verify that normalize_program_ast moves eventFacets into declarations."""

    def test_namespace_eventfacets_moved_to_declarations(self):
        """eventFacets inside a namespace are folded into declarations."""
        ns_node = {
            "type": "Namespace",
            "name": "myns",
            "eventFacets": [_EVENT_B],
            "facets": [_FACET_A],
        }
        prog = {
            "type": "Program",
            "declarations": [ns_node],
        }
        result = normalize_program_ast(prog)

        ns_out = result["declarations"][0]
        assert "eventFacets" not in ns_out
        assert "facets" not in ns_out
        types = [d["type"] for d in ns_out["declarations"]]
        assert "EventFacetDecl" in types
        assert "FacetDecl" in types

    def test_compile_event_facet_normalize_roundtrip(self):
        """Compile FFL with event facets, normalize, confirm eventFacets absent."""
        from facetwork import emit_dict, parse

        source = """
namespace myns {
    event facet DoWork(input: String) => (result: String)
    workflow Main() => (out: String) andThen {
        s = myns.DoWork(input = "hello")
        yield Main(out = s.result)
    }
}
"""
        ast = parse(source)
        compiled = emit_dict(ast)

        # Before normalization: namespace may have eventFacets key
        ns_before = None
        for d in compiled.get("declarations", []):
            if d.get("type") == "Namespace":
                ns_before = d
                break
        assert ns_before is not None

        normalized = normalize_program_ast(compiled)

        # After normalization: eventFacets key must be absent
        ns_after = None
        for d in normalized["declarations"]:
            if d.get("type") == "Namespace":
                ns_after = d
                break
        assert ns_after is not None
        assert "eventFacets" not in ns_after

        # Event facet should still be findable in declarations
        event_facets = [d for d in ns_after["declarations"] if d.get("type") == "EventFacetDecl"]
        assert len(event_facets) == 1
        assert event_facets[0]["name"] == "DoWork"

        # Workflow should still be findable
        wf = find_workflow(normalized, "myns.Main")
        assert wf is not None
        assert wf["name"] == "Main"
