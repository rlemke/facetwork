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

"""AST utility functions for compiled FFL program dicts.

Provides:
- ``normalize_program_ast`` — strip categorized keys, keep ``declarations`` only
- ``find_workflow`` — locate a WorkflowDecl by simple or qualified name
- ``find_all_workflows`` — collect every WorkflowDecl (including inside namespaces)
"""

from __future__ import annotations

# Keys emitted by the compiler under both the categorized and declarations formats.
_CATEGORIZED_KEYS = frozenset(
    {"namespaces", "facets", "eventFacets", "workflows", "implicits", "schemas"}
)

# Declaration types that can appear in the categorized keys.
_TYPE_FOR_KEY: dict[str, str | None] = {
    "namespaces": "Namespace",
    "facets": "FacetDecl",
    "eventFacets": "EventFacetDecl",
    "workflows": "WorkflowDecl",
    "implicits": "ImplicitDecl",
    "schemas": "SchemaDecl",
}


# ---------------------------------------------------------------------------
# normalize_program_ast
# ---------------------------------------------------------------------------


def normalize_program_ast(compiled: dict) -> dict:
    """Return a copy with only ``declarations`` as the source of truth.

    * If ``declarations`` is present, categorized keys are stripped.
    * If ``declarations`` is absent (old / external JSON), it is built from
      the categorized keys.
    * Namespace nodes inside ``declarations`` are recursively normalized.
    * The input dict is **not** mutated.
    * The function is idempotent.
    """
    result = {k: v for k, v in compiled.items() if k not in _CATEGORIZED_KEYS}

    declarations = compiled.get("declarations")
    if declarations is not None:
        # Already has declarations — normalize namespace children and keep.
        result["declarations"] = [_normalize_node(d) for d in declarations]
    else:
        # Build declarations from categorized keys.
        decls: list[dict] = []
        for key in ("namespaces", "facets", "eventFacets", "workflows", "implicits", "schemas"):
            for item in compiled.get(key, []):
                decls.append(_normalize_node(item))
        if decls:
            result["declarations"] = decls

    return result


def _normalize_node(node: dict) -> dict:
    """Recursively normalize a single declaration node.

    For Namespace nodes, strip categorized keys and ensure a ``declarations``
    list exists.  All other node types are returned as-is.
    """
    if node.get("type") != "Namespace":
        return node

    out = {k: v for k, v in node.items() if k not in _CATEGORIZED_KEYS}

    child_declarations = node.get("declarations")
    if child_declarations is not None:
        out["declarations"] = [_normalize_node(d) for d in child_declarations]
    else:
        decls: list[dict] = []
        for key in ("facets", "eventFacets", "workflows", "implicits", "schemas"):
            for item in node.get(key, []):
                decls.append(_normalize_node(item))
        if decls:
            out["declarations"] = decls

    return out


# ---------------------------------------------------------------------------
# find_workflow
# ---------------------------------------------------------------------------


def find_workflow(program: dict, workflow_name: str) -> dict | None:
    """Find a WorkflowDecl in *program* by name.

    Supports:
    * Simple names — ``"MyWorkflow"``
    * Qualified names — ``"ns.sub.MyWorkflow"``

    Expects a normalized program dict (declarations-only).
    """
    if "." in workflow_name:
        return _find_qualified(program, workflow_name)
    return _find_simple(program, workflow_name)


def _find_simple(program: dict, name: str) -> dict | None:
    """Find an unqualified workflow name at any nesting level."""
    for decl in program.get("declarations", []):
        if decl.get("type") == "WorkflowDecl" and decl.get("name") == name:
            return decl
        if decl.get("type") == "Namespace":
            result = _search_namespace_workflows(decl, name)
            if result:
                return result

    return None


def _find_qualified(program: dict, qualified_name: str) -> dict | None:
    """Find a workflow by dotted qualified name (e.g. ``ns.sub.Workflow``)."""
    parts = qualified_name.split(".")
    short_name = parts[-1]
    ns_prefix = ".".join(parts[:-1])

    # Strategy 1: flat namespace match (dotted name equals full prefix)
    for decl in program.get("declarations", []):
        if decl.get("type") == "Namespace" and decl.get("name") == ns_prefix:
            for d in decl.get("declarations", []):
                if d.get("type") == "WorkflowDecl" and d.get("name") == short_name:
                    return d

    # Strategy 2: nested namespace navigation (step by step)
    ns_parts = parts[:-1]
    current: dict = program
    for ns_name in ns_parts:
        found_ns = None
        for decl in current.get("declarations", []):
            if decl.get("type") == "Namespace" and decl.get("name") == ns_name:
                found_ns = decl
                break
        if not found_ns:
            return None
        current = found_ns

    for decl in current.get("declarations", []):
        if decl.get("type") == "WorkflowDecl" and decl.get("name") == short_name:
            return decl

    return None


def _search_namespace_workflows(namespace: dict, workflow_name: str) -> dict | None:
    """Recursively search a namespace for a workflow by name."""
    for decl in namespace.get("declarations", []):
        if decl.get("type") == "WorkflowDecl" and decl.get("name") == workflow_name:
            return decl
        if decl.get("type") == "Namespace":
            result = _search_namespace_workflows(decl, workflow_name)
            if result:
                return result
    return None


# ---------------------------------------------------------------------------
# find_all_workflows
# ---------------------------------------------------------------------------


def find_all_workflows(program: dict) -> list[dict]:
    """Return every WorkflowDecl in *program*, including inside namespaces."""
    result: list[dict] = []
    _collect_workflows(program, result)
    return result


def _collect_workflows(node: dict, acc: list[dict]) -> None:
    """Recursively collect WorkflowDecl nodes from *node* into *acc*."""
    for decl in node.get("declarations", []):
        if decl.get("type") == "WorkflowDecl":
            acc.append(decl)
        elif decl.get("type") == "Namespace":
            _collect_workflows(decl, acc)
