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

"""Shared helper utilities for the dashboard."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from afl.runtime.entities import HandlerRegistration, RunnerDefinition, ServerDefinition

SERVER_DOWN_TIMEOUT_MS = 300_000  # 5 minutes


def effective_server_state(server: ServerDefinition) -> str:
    """Return 'down' if a running/startup server's ping_time is stale (>5 min).

    Servers in ``shutdown`` or ``error`` keep their original state.
    A ``ping_time`` of 0 with ``running`` state means the server never pinged
    and is treated as down.
    """
    if server.state not in ("running", "startup"):
        return server.state
    now_ms = time.time() * 1000
    if server.ping_time == 0 or (now_ms - server.ping_time) > SERVER_DOWN_TIMEOUT_MS:
        return "down"
    return server.state


def extract_namespace(workflow_name: str) -> str:
    """Extract the namespace prefix from a qualified workflow name.

    >>> extract_namespace("osm.Routes.BicycleRoutes")
    'osm.Routes'
    >>> extract_namespace("SimpleWorkflow")
    'system.unnamespaced'
    """
    if "." in workflow_name:
        ns, _ = workflow_name.rsplit(".", 1)
        return ns
    return "system.unnamespaced"


def short_workflow_name(workflow_name: str) -> str:
    """Extract the short name from a qualified workflow name.

    >>> short_workflow_name("osm.Routes.BicycleRoutes")
    'BicycleRoutes'
    >>> short_workflow_name("SimpleWorkflow")
    'SimpleWorkflow'
    """
    if "." in workflow_name:
        _, short = workflow_name.rsplit(".", 1)
        return short
    return workflow_name


def categorize_step_state(state: str) -> str:
    """Categorize a step state into running/complete/error/other.

    ``running`` covers states where handler interaction happens or the step
    is newly created.  ``other`` covers internal evaluator states (block
    execution, mixin blocks, statement blocks, capture, scripts).

    >>> categorize_step_state("state.statement.Complete")
    'complete'
    >>> categorize_step_state("state.statement.Error")
    'error'
    >>> categorize_step_state("state.statement.Created")
    'running'
    >>> categorize_step_state("state.block.execution.Begin")
    'other'
    """
    from afl.runtime.states import StepState

    if state == StepState.STATEMENT_COMPLETE:
        return "complete"
    if state == StepState.STATEMENT_ERROR:
        return "error"
    if state in {
        StepState.CREATED,
        StepState.EVENT_TRANSMIT,
        StepState.FACET_INIT_BEGIN,
        StepState.FACET_INIT_END,
    }:
        return "running"
    return "other"


def qualify_step_names(steps: list) -> None:
    """Add ``display_name`` to each step with ancestor context.

    Walks up the step hierarchy to build a dotted path like
    ``Alabama.imp.imported`` instead of just ``imported``.

    For AndThen block steps (which have no ``statement_name``), builds
    a name from ``foreach_value`` and ancestor context, e.g. ``Alabama``
    for a foreach block or ``Alabama.imp.andThen`` for a subroutine block.

    Mutates steps in place.
    """
    by_id: dict[str, Any] = {s.id: s for s in steps}

    def _ancestor_segments(start_id: str | None) -> list[str]:
        """Walk up from start_id collecting name segments."""
        segments: list[str] = []
        seen: set[str] = set()
        current_id = start_id
        while current_id and current_id not in seen:
            seen.add(current_id)
            ancestor = by_id.get(current_id)
            if ancestor is None:
                break

            foreach_val = getattr(ancestor, "foreach_value", None)
            name = getattr(ancestor, "statement_name", None)
            if foreach_val:
                segments.append(str(foreach_val))
            elif name:
                segments.append(name)

            # Navigate up: block → container (owning step) → its block
            container_id = getattr(ancestor, "container_id", None)
            if container_id and container_id not in seen:
                container = by_id.get(container_id)
                if container:
                    seen.add(container_id)
                    c_foreach = getattr(container, "foreach_value", None)
                    c_name = getattr(container, "statement_name", None)
                    if c_foreach:
                        segments.append(str(c_foreach))
                    elif c_name:
                        segments.append(c_name)
                    current_id = getattr(container, "block_id", None)
                    continue

            current_id = getattr(ancestor, "block_id", None)

        segments.reverse()
        return segments

    for step in steps:
        is_block = getattr(step, "is_block", False)

        if is_block:
            # Give block steps a display-friendly facet name
            if not getattr(step, "facet_name", None):
                object_type = getattr(step, "object_type", "")
                step.facet_name = object_type or "AndThen"

            # Block steps: build name from foreach_value + container context
            foreach_val = getattr(step, "foreach_value", None)
            container_id = getattr(step, "container_id", None)
            container = by_id.get(container_id) if container_id else None

            # Get ancestor path from the container's perspective
            segments = []
            if container:
                c_name = getattr(container, "statement_name", None)
                c_block_id = getattr(container, "block_id", None)
                ancestor_segs = _ancestor_segments(c_block_id)
                if ancestor_segs:
                    segments.extend(ancestor_segs)
                if c_name:
                    segments.append(c_name)

            if foreach_val:
                segments.append(str(foreach_val))
            elif not segments:
                # Top-level block with no context
                step.display_name = ""
                continue

            step.display_name = ".".join(segments)

        elif step.statement_name:
            segments = _ancestor_segments(step.block_id)
            segments.append(step.statement_name)
            step.display_name = ".".join(segments)

        else:
            step.display_name = ""


def group_runners_by_namespace(
    runners: list[RunnerDefinition],
) -> list[dict]:
    """Group runners by their workflow namespace.

    Returns a sorted list of dicts:
        [{"namespace": "osm.geocode", "runners": [...], "counts": {...}, "total": N}]
    """
    ns_map: dict[str, list[RunnerDefinition]] = {}
    for r in runners:
        ns = extract_namespace(r.workflow.name)
        ns_map.setdefault(ns, []).append(r)

    groups = []
    for ns in sorted(ns_map):
        ns_runners = ns_map[ns]
        counts: dict[str, int] = {}
        for r in ns_runners:
            counts[r.state] = counts.get(r.state, 0) + 1
        groups.append(
            {
                "namespace": ns,
                "runners": ns_runners,
                "counts": counts,
                "total": len(ns_runners),
            }
        )
    return groups


def extract_handler_prefix(facet_name: str) -> str:
    """Extract the top-level namespace prefix from a handler facet name.

    Returns the first dotted segment, or ``(top-level)`` if there are no dots.

    >>> extract_handler_prefix("osm.Cache")
    'osm'
    >>> extract_handler_prefix("SimpleHandler")
    '(top-level)'
    """
    if "." in facet_name:
        return facet_name.split(".", 1)[0]
    return "system.unnamespaced"


def group_handlers_by_namespace(
    handlers: list[HandlerRegistration],
) -> list[dict]:
    """Group handlers by their full namespace (all segments except last).

    Returns a sorted list of dicts:
        [{"namespace": "osm.geocode", "handlers": [...], "total": N}]
    """
    ns_map: dict[str, list[HandlerRegistration]] = {}
    for h in handlers:
        ns = extract_namespace(h.facet_name)
        ns_map.setdefault(ns, []).append(h)

    groups = []
    for ns in sorted(ns_map):
        ns_handlers = ns_map[ns]
        groups.append(
            {
                "namespace": ns,
                "handlers": ns_handlers,
                "total": len(ns_handlers),
            }
        )
    return groups


def group_servers_by_group(
    servers: list,
) -> list[dict]:
    """Group servers by their server_group field.

    Returns a sorted list of dicts:
        [{"group": "osm-geocoder", "servers": [...], "total": N}]
    """
    group_map: dict[str, list] = {}
    for s in servers:
        group_map.setdefault(s.server_group, []).append(s)

    groups = []
    for grp in sorted(group_map):
        grp_servers = group_map[grp]
        groups.append(
            {
                "group": grp,
                "servers": grp_servers,
                "total": len(grp_servers),
            }
        )
    return groups


def group_tasks_by_state(tasks: list) -> dict:
    """Count tasks by state category.

    Returns a dict with ``running``, ``completed``, ``failed``, ``pending``,
    and ``total`` counts.
    """
    counts: dict[str, int] = {"running": 0, "completed": 0, "failed": 0, "pending": 0, "total": 0}
    for t in tasks:
        counts["total"] += 1
        if t.state == "running":
            counts["running"] += 1
        elif t.state == "completed":
            counts["completed"] += 1
        elif t.state in ("failed", "error"):
            counts["failed"] += 1
        elif t.state == "pending":
            counts["pending"] += 1
    return counts


def group_tasks_by_runner(tasks: list, store: object) -> list[dict]:
    """Group tasks by runner_id and enrich with runner metadata.

    Returns a sorted list (most recently active first) of dicts::

        [{"runner_id": "...", "workflow_name": "...", "runner_state": "...",
          "tasks": [...], "counts": {...}, "total": N}]
    """
    runner_map: dict[str, list] = {}
    for t in tasks:
        runner_map.setdefault(t.runner_id, []).append(t)

    # Cache runner lookups
    runner_cache: dict[str, object] = {}
    groups = []
    for runner_id, runner_tasks in runner_map.items():
        if runner_id not in runner_cache:
            runner_cache[runner_id] = getattr(store, "get_runner", lambda _: None)(runner_id)
        runner = runner_cache[runner_id]

        wf_name = ""
        runner_state = ""
        if runner is not None:
            wf = getattr(runner, "workflow", None)
            wf_name = getattr(wf, "name", "") if wf else ""
            runner_state = getattr(runner, "state", "")

        counts = group_tasks_by_state(runner_tasks)
        max_updated = max((getattr(t, "updated", 0) or 0) for t in runner_tasks)
        groups.append(
            {
                "runner_id": runner_id,
                "workflow_name": wf_name or runner_id[:12],
                "runner_state": runner_state,
                "tasks": sorted(
                    runner_tasks, key=lambda t: getattr(t, "updated", 0) or 0, reverse=True
                ),
                "counts": counts,
                "total": len(runner_tasks),
                "_max_updated": max_updated,
            }
        )

    def _sort_key(g: dict) -> int:
        return int(g.get("_max_updated") or 0)

    groups.sort(key=_sort_key, reverse=True)
    return groups


def compute_step_progress(runner: RunnerDefinition, steps: list) -> dict:
    """Compute step completion progress for a runner.

    Returns a dict with ``completed``, ``total``, and ``pct`` keys.
    """
    total = len(steps)
    completed = sum(1 for s in steps if categorize_step_state(s.state) == "complete")
    return {
        "completed": completed,
        "total": total,
        "pct": int(100 * completed / total) if total > 0 else 0,
    }


@dataclass
class TimelineEntry:
    """A single bar in the step execution timeline."""

    step_id: str
    label: str
    state: str
    start_ms: int
    end_ms: int
    offset_pct: float
    width_pct: float


def compute_timeline(steps: list, workflow_start: int = 0) -> list[TimelineEntry]:
    """Compute timeline entries for steps with timestamps.

    Each entry gets ``offset_pct`` and ``width_pct`` relative to the total
    workflow duration, suitable for rendering as CSS positioned bars.
    """
    # Collect steps that have usable timestamps
    timed = []
    for s in steps:
        start = getattr(s, "start_time", 0) or 0
        end = getattr(s, "last_modified", 0) or start
        if start <= 0:
            continue
        label = getattr(s, "statement_name", None) or getattr(s, "facet_name", None) or s.id[:8]
        timed.append((s.id, label, s.state, start, max(end, start)))

    if not timed:
        return []

    # Determine global time range
    global_start = workflow_start if workflow_start > 0 else min(t[3] for t in timed)
    global_end = max(t[4] for t in timed)
    duration = global_end - global_start
    if duration <= 0:
        duration = 1  # avoid division by zero

    entries = []
    for step_id, label, state, start, end in sorted(timed, key=lambda t: t[3]):
        offset_pct = round(100.0 * (start - global_start) / duration, 2)
        width_pct = round(100.0 * max(end - start, 1) / duration, 2)
        # Clamp to valid range
        width_pct = max(width_pct, 0.5)
        entries.append(
            TimelineEntry(
                step_id=step_id,
                label=label,
                state=state,
                start_ms=start,
                end_ms=end,
                offset_pct=offset_pct,
                width_pct=width_pct,
            )
        )

    return entries


def search_all(query: str, store: Any) -> list[dict]:
    """Search across workflows (runners), flows, servers, and handlers.

    Returns a list of dicts:
        [{"name": "...", "type": "workflow|flow|server|handler", "href": "..."}]
    """
    if not query or len(query.strip()) < 1:
        return []

    q = query.lower().strip()
    results: list[dict] = []

    # Search runners (workflows)
    try:
        runners = store.get_all_runners(limit=500)
        for r in runners:
            name = r.workflow.name if r.workflow else r.uuid
            if q in name.lower():
                results.append(
                    {
                        "name": name,
                        "type": "workflow",
                        "href": f"/v2/workflows/{r.uuid}",
                        "icon": "&#x25B6;",
                    }
                )
    except Exception:
        pass

    # Search flows
    try:
        flows = store.get_all_flows()
        for f in flows:
            name = f.name.name if hasattr(f.name, "name") else str(f.name)
            if q in name.lower():
                results.append(
                    {
                        "name": name,
                        "type": "flow",
                        "href": f"/flows/{f.uuid}",
                        "icon": "&#x2B22;",
                    }
                )
    except Exception:
        pass

    # Search servers
    try:
        servers = list(store.get_all_servers())
        for s in servers:
            name = getattr(s, "server_group", "") or s.uuid
            if q in name.lower() or q in s.uuid.lower():
                results.append(
                    {
                        "name": f"{name} ({s.uuid[:8]})",
                        "type": "server",
                        "href": f"/v2/servers/{s.uuid}",
                        "icon": "&#x2699;",
                    }
                )
    except Exception:
        pass

    # Search handlers
    try:
        handlers = store.list_handler_registrations()
        for h in handlers:
            if q in h.facet_name.lower():
                results.append(
                    {
                        "name": h.facet_name,
                        "type": "handler",
                        "href": f"/v2/handlers/{h.facet_name}",
                        "icon": "&#x26A1;",
                    }
                )
    except Exception:
        pass

    # Limit to top 20 results
    return results[:20]


# ---------------------------------------------------------------------------
# Facet definition lookup
# ---------------------------------------------------------------------------


@dataclass
class FacetInfo:
    """Extracted facet metadata for display on handler detail pages."""

    description: str = ""
    params: list[dict[str, Any]] = None  # type: ignore[assignment]
    returns: list[dict[str, Any]] = None  # type: ignore[assignment]
    doc: dict[str, Any] | None = None
    kind: str = ""  # "event facet", "facet", "workflow"

    def __post_init__(self):
        if self.params is None:
            self.params = []
        if self.returns is None:
            self.returns = []


def lookup_facet_info(facet_name: str, store) -> FacetInfo | None:
    """Look up facet definition from published AFL sources.

    Given a fully-qualified facet name like ``deploy.Rollback.RollbackDeployment``,
    searches published sources for the namespace and extracts the facet's
    documentation, parameters, and return types.

    Returns None if the source is not published or parsing fails.
    """
    try:
        from afl import emit_dict, parse
    except ImportError:
        return None

    # Try progressively shorter namespace prefixes
    # e.g. "a.b.C" → try "a.b", then "a"
    parts = facet_name.rsplit(".", 1)
    if len(parts) < 2:
        return None

    namespace_name = parts[0]
    short_name = parts[1]

    # Collect candidate namespace names (exact + parent prefixes)
    candidates = []
    ns_parts = namespace_name.split(".")
    for i in range(len(ns_parts), 0, -1):
        candidates.append(".".join(ns_parts[:i]))

    source = None
    for candidate in candidates:
        try:
            source = store.get_source_by_namespace(candidate)
            if source is not None:
                break
        except Exception:
            continue

    if source is None:
        return None

    try:
        tree = parse(source.source_text)
        program = emit_dict(tree)
    except Exception:
        return None

    # Walk the declarations tree to find the matching facet
    return _find_facet_in_declarations(program.get("declarations", []), facet_name, short_name)


def _find_facet_in_declarations(
    declarations: list[dict], full_name: str, short_name: str, prefix: str = ""
) -> FacetInfo | None:
    """Recursively search declarations for a facet matching the given name."""
    for decl in declarations:
        decl_type = decl.get("type", "")

        if decl_type == "Namespace":
            ns_name = decl.get("name", "")
            qualified = f"{prefix}.{ns_name}" if prefix else ns_name
            result = _find_facet_in_declarations(
                decl.get("declarations", []), full_name, short_name, qualified
            )
            if result is not None:
                return result

        elif decl_type in ("EventFacetDecl", "FacetDecl", "WorkflowDecl"):
            decl_name = decl.get("name", "")
            qualified = f"{prefix}.{decl_name}" if prefix else decl_name

            if qualified == full_name or decl_name == short_name:
                kind_map = {
                    "EventFacetDecl": "event facet",
                    "FacetDecl": "facet",
                    "WorkflowDecl": "workflow",
                }
                doc = decl.get("doc")
                params = decl.get("params", [])
                returns = decl.get("returns", [])

                return FacetInfo(
                    description=doc.get("description", "") if doc else "",
                    params=params,
                    returns=returns,
                    doc=doc,
                    kind=kind_map.get(decl_type, ""),
                )

    return None
