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

"""Namespace browser routes."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Request

from ...dependencies import get_store

router = APIRouter(prefix="/namespaces")


@dataclass
class WorkflowEntry:
    """Workflow with its flow context for generating Run links."""

    name: str
    short_name: str
    uuid: str
    flow_id: str
    version: str = ""
    documentation: dict | str | None = None


@dataclass
class FacetEntry:
    """Facet with parameters for display."""

    name: str
    short_name: str
    parameters: list = field(default_factory=list)
    return_type: str | None = None
    documentation: dict | str | None = None


@dataclass
class NamespaceSummary:
    """Aggregated namespace info across flows."""

    name: str
    flow_count: int = 0
    facet_count: int = 0
    workflow_count: int = 0
    facets: list[FacetEntry] = field(default_factory=list)
    workflows: list[WorkflowEntry] = field(default_factory=list)


def _aggregate_namespaces(store) -> dict[str, NamespaceSummary]:
    """Aggregate namespace data from all flows."""
    flows = store.get_all_flows()
    ns_map: dict[str, NamespaceSummary] = {}

    for flow in flows:
        seen_in_flow: set[str] = set()
        for ns in flow.namespaces:
            if ns.name not in ns_map:
                ns_map[ns.name] = NamespaceSummary(name=ns.name)
            if ns.name not in seen_in_flow:
                ns_map[ns.name].flow_count += 1
                seen_in_flow.add(ns.name)

        # Collect facets via namespace_id resolution
        for facet in flow.facets:
            ns_name = _resolve_ns_name(flow, facet.namespace_id)
            if ns_name and ns_name in ns_map:
                short = facet.name
                prefix = ns_name + "."
                if short.startswith(prefix):
                    short = short[len(prefix) :]
                ns_map[ns_name].facet_count += 1
                ns_map[ns_name].facets.append(
                    FacetEntry(
                        name=facet.name,
                        short_name=short,
                        parameters=list(facet.parameters),
                        return_type=facet.return_type,
                        documentation=getattr(facet, "documentation", None),
                    )
                )

        # Collect embedded workflows (from flow.workflows field)
        for wf in flow.workflows:
            ns_name = _resolve_ns_name(flow, wf.namespace_id)
            if ns_name and ns_name in ns_map:
                existing = {w.name for w in ns_map[ns_name].workflows}
                if wf.name not in existing:
                    _add_workflow(ns_map[ns_name], wf, flow.uuid)

        # Collect workflows from the store collection by qualified name prefix
        store_workflows = store.get_workflows_by_flow(flow.uuid)
        for wf in store_workflows:
            ns_name = _match_ns_by_name(wf.name, ns_map)
            if ns_name:
                # Deduplicate by name (seed may store multiple records
                # for the same workflow due to declarations/workflows overlap)
                existing = {w.name for w in ns_map[ns_name].workflows}
                if wf.name not in existing:
                    _add_workflow(ns_map[ns_name], wf, flow.uuid)

    return ns_map


def _add_workflow(ns: NamespaceSummary, wf, flow_id: str) -> None:
    """Add a workflow entry to a namespace summary."""
    short = wf.name
    prefix = ns.name + "."
    if short.startswith(prefix):
        short = short[len(prefix) :]
    ns.workflow_count += 1
    ns.workflows.append(
        WorkflowEntry(
            name=wf.name,
            short_name=short,
            uuid=wf.uuid,
            flow_id=flow_id,
            version=getattr(wf, "version", ""),
            documentation=getattr(wf, "documentation", None),
        )
    )


def _match_ns_by_name(qualified_name: str, ns_map: dict[str, NamespaceSummary]) -> str | None:
    """Match a qualified workflow name to its namespace by prefix.

    Tries longest prefix first so 'a.b.Wf' matches 'a.b' over 'a'.
    """
    if "." not in qualified_name:
        return None
    # Sort by length descending to match most specific namespace first
    for ns_name in sorted(ns_map.keys(), key=len, reverse=True):
        if qualified_name.startswith(ns_name + "."):
            return ns_name
    return None


def _resolve_ns_name(flow, ns_id: str) -> str | None:
    """Resolve a namespace ID to its name within a flow."""
    for ns in flow.namespaces:
        if ns.uuid == ns_id:
            return ns.name
    return None


@router.get("")
def namespace_list(request: Request, store=Depends(get_store)):
    """List all namespaces aggregated across flows."""
    ns_map = _aggregate_namespaces(store)
    namespaces = sorted(ns_map.values(), key=lambda n: n.name)
    return request.app.state.templates.TemplateResponse(
        request,
        "namespaces/list.html",
        {"namespaces": namespaces},
    )


@router.get("/{namespace_name:path}")
def namespace_detail(namespace_name: str, request: Request, store=Depends(get_store)):
    """Show namespace detail with facets and workflows."""
    ns_map = _aggregate_namespaces(store)
    namespace = ns_map.get(namespace_name)
    return request.app.state.templates.TemplateResponse(
        request,
        "namespaces/detail.html",
        {"namespace": namespace},
    )
