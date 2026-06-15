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

from fastapi import APIRouter, Depends, Request

from ...dependencies import get_store
from ...helpers import group_handlers_by_namespace

router = APIRouter(prefix="/v3")


@router.get("/handlers")
def handlers_v3(request: Request, tab: str = "all", store=Depends(get_store)):
    """Redesigned Handlers list — All / Active / Working, grouped by namespace."""
    from ..v2.dashboard_v2 import _build_handler_stats

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
