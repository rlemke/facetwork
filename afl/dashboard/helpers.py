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
    '(top-level)'
    """
    if "." in workflow_name:
        ns, _ = workflow_name.rsplit(".", 1)
        return ns
    return "(top-level)"


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
    return "(top-level)"


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
