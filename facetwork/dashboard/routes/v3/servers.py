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
    "running": "var(--st-running)", "startup": "var(--st-warning)",
    "error": "var(--st-error)", "shutdown": "var(--muted)", "down": "var(--st-error)",
}


@router.get("/servers")
def servers_v3(request: Request, tab: str = "running", store=Depends(get_store)):
    """Redesigned Servers list — runner processes grouped by host."""
    from ..v2.dashboard_v2 import (
        _apply_effective_state,
        _count_servers_by_tab,
        _enrich_servers_with_tasks,
        _filter_servers,
    )

    all_servers = _apply_effective_state(list(store.get_all_servers()))
    tab_counts = _count_servers_by_tab(all_servers)
    filtered = _filter_servers(all_servers, tab)
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
            "active_nav": "servers",
        },
    )
