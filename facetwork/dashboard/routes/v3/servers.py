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

"""v3 Servers page — individual runner processes, grouped by host.

Complements the Fleet page (per-host rollout/health) by showing each runner
process: state, handler count, active tasks, version, last heartbeat.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from ...dependencies import get_store

router = APIRouter(prefix="/v3")

# server effective-state → colour var (reused for dot + pill)
_DOT = {
    "running": "var(--st-running)",
    "startup": "var(--st-warning)",
    "error": "var(--st-error)",
    "shutdown": "var(--muted)",
    "down": "var(--st-error)",
}


def _norm_host(name) -> str:
    """fleet-agent records use 'server2.local'; runner heartbeats use 'server2'."""
    return str(name or "").removesuffix(".local").lower()


@router.get("/servers")
def servers_v3(
    request: Request,
    tab: str = "running",
    host: str | None = None,
    group: str | None = None,
    store=Depends(get_store),
):
    """Redesigned Servers list — runner processes grouped by host.

    ``host`` (from the Fleet hosts table) narrows to one host (normalized,
    ``.local`` stripped); ``group`` (from a Fleet role with no namespace, e.g.
    ffl-runner) narrows to one server_group / role.
    """
    from ...viewdata import (
        _apply_effective_state,
        _count_servers_by_tab,
        _enrich_servers_with_tasks,
        _filter_servers,
    )

    all_servers = _apply_effective_state(list(store.get_all_servers()))
    tab_counts = _count_servers_by_tab(all_servers)
    filtered = _filter_servers(all_servers, tab)
    if host:
        h = _norm_host(host)
        filtered = [s for s in filtered if _norm_host(getattr(s, "server_name", "")) == h]
    if group:
        filtered = [s for s in filtered if (getattr(s, "server_group", "") or "") == group]
    _enrich_servers_with_tasks(filtered, store)

    now = int(time.time() * 1000)
    by_host: dict[str, list] = {}
    for s in filtered:
        s.last_ping_s = round((now - (getattr(s, "ping_time", 0) or 0)) / 1000)
        by_host.setdefault(s.server_name or s.uuid[:8], []).append(s)

    groups = [
        {
            "host": h,
            "servers": sorted(v, key=lambda x: x.uuid),
            "count": len(v),
            "tasks": sum(getattr(x, "active_task_count", 0) for x in v),
        }
        for h, v in sorted(by_host.items())
    ]

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/servers/list.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
            "dot": _DOT,
            "total": len(filtered),
            "filter_host": host,
            "filter_group": group,
            "active_nav": "servers",
        },
    )


@router.get("/servers/{server_id}")
def server_detail_v3(server_id: str, request: Request, store=Depends(get_store)):
    """Detail for one runner process: identity, the handlers it serves, its tasks."""
    from ...viewdata import _apply_effective_state, _build_server_detail_context

    server = store.get_server(server_id)
    if server:
        _apply_effective_state([server])
    ctx = (
        _build_server_detail_context(server, store)
        if server
        else {"task_groups": [], "task_counts": {}}
    )
    last_ping_s = None
    if server and getattr(server, "ping_time", 0):
        last_ping_s = round((int(time.time() * 1000) - server.ping_time) / 1000)

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/servers/detail.html",
        {
            "server": server,
            "server_id": server_id,
            "last_ping_s": last_ping_s,
            "dot": _DOT,
            "active_nav": "servers",
            **ctx,
        },
    )
