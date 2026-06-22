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

"""v3 Handlers page — registered facets, their load/busy status + throughput."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from ...dependencies import get_store
from ...helpers import group_handlers_by_namespace

router = APIRouter(prefix="/v3")


@router.get("/handlers")
def handlers_v3(request: Request, tab: str = "all", store=Depends(get_store)):
    """Redesigned Handlers list — All / Active / Working, grouped by namespace."""
    from ...viewdata import _build_handler_stats

    all_h = list(store.list_handler_registrations())
    active, busy, stats = _build_handler_stats(store)

    counts = {
        "all": len(all_h),
        "active": len([h for h in all_h if h.facet_name in active]),
        "working": len([h for h in all_h if h.facet_name in busy]),
    }
    if tab == "active":
        filtered = [h for h in all_h if h.facet_name in active]
    elif tab == "working":
        filtered = [h for h in all_h if h.facet_name in busy]
    else:
        filtered = all_h

    groups = group_handlers_by_namespace(filtered)

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/handlers/list.html",
        {
            "groups": groups,
            "tab": tab,
            "counts": counts,
            "active": active,
            "busy": busy,
            "stats": stats,
            "active_nav": "handlers",
        },
    )


@router.get("/handlers/{facet_name:path}")
def handler_detail_v3(facet_name: str, request: Request, store=Depends(get_store)):
    """Detail for one handler/facet: registration, the servers serving it, recent tasks."""
    reg = store.get_handler_registration(facet_name)

    now = int(time.time() * 1000)
    servers = []
    for s in store.get_all_servers():
        if facet_name in (getattr(s, "handlers", None) or []):
            alive = s.state == "running" and (now - (getattr(s, "ping_time", 0) or 0)) < 60_000
            servers.append({
                "uuid": s.uuid,
                "name": s.server_name or s.uuid[:12],
                "group": getattr(s, "server_group", "") or "—",
                "state": s.state,
                "alive": alive,
            })
    servers.sort(key=lambda x: (not x["alive"], x["uuid"]))

    tasks = store.get_tasks_by_facet_name(facet_name)[:50]
    state_counts: dict[str, int] = {}
    for t in tasks:
        state_counts[t.state] = state_counts.get(t.state, 0) + 1

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/handlers/detail.html",
        {
            "facet_name": facet_name,
            "reg": reg,
            "servers": servers,
            "tasks": tasks,
            "state_counts": state_counts,
            "active_nav": "handlers",
        },
    )
