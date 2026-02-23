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
from typing import TYPE_CHECKING

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

    >>> extract_namespace("osm.geo.Routes.BicycleRoutes")
    'osm.geo.Routes'
    >>> extract_namespace("SimpleWorkflow")
    '(top-level)'
    """
    if "." in workflow_name:
        ns, _ = workflow_name.rsplit(".", 1)
        return ns
    return "(top-level)"


def short_workflow_name(workflow_name: str) -> str:
    """Extract the short name from a qualified workflow name.

    >>> short_workflow_name("osm.geo.Routes.BicycleRoutes")
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
        [{"namespace": "osm.geo", "runners": [...], "counts": {...}, "total": N}]
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

    >>> extract_handler_prefix("osm.geo.Cache")
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
        [{"namespace": "osm.geo", "handlers": [...], "total": N}]
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
