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

"""v3 Tasks + Events pages (both task-backed)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...dependencies import get_store

router = APIRouter(prefix="/v3")

# task state → colour var (dot + pill)
_DOT = {
    "pending": "var(--st-warning)",
    "running": "var(--st-running)",
    "completed": "var(--st-complete)",
    "failed": "var(--st-error)",
    "dead_letter": "var(--st-error)",
    "canceled": "var(--muted)",
    "ignored": "var(--muted)",
}
_CAP = 500  # max rows rendered (these lists can be large)


@router.get("/tasks")
def tasks_v3(
    request: Request,
    state: str | None = None,
    task_list: str | None = None,
    store=Depends(get_store),
):
    """Redesigned Tasks list — state tabs, task-list filter, no-handler flag."""
    from ..execution.tasks import (
        _count_tasks_by_state,
        _resolve_server_info,
        _resolve_step_names,
    )

    tasks = (
        store.get_tasks_by_state(state, task_list=task_list)
        if state
        else store.get_all_tasks(task_list=task_list)
    )
    tasks = list(tasks)
    total = len(tasks)
    shown = tasks[:_CAP]

    step_names = _resolve_step_names(shown, store)
    server_info = _resolve_server_info(shown, store)

    # "No matching handler": a non-terminal task no live runner advertises.
    dispatchable = (
        store.dispatchable_facet_names() if hasattr(store, "dispatchable_facet_names") else set()
    )
    unmatched: set[str] = set()
    if dispatchable:
        for t in shown:
            nm = getattr(t, "name", "") or ""
            if (
                t.state in ("pending", "running")
                and nm
                and not nm.startswith(("fw:execute", "fw:resume"))
                and nm not in dispatchable
            ):
                unmatched.add(t.uuid)

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/tasks/list.html",
        {
            "tasks": shown,
            "total": total,
            "shown": len(shown),
            "filter_state": state,
            "filter_task_list": task_list,
            "step_names": step_names,
            "server_info": server_info,
            "tab_counts": _count_tasks_by_state(store),
            "list_counts": sorted((store.task_list_counts() or {}).items()),
            "unmatched": unmatched,
            "dot": _DOT,
            "active_nav": "tasks",
        },
    )


@router.get("/events")
def events_v3(request: Request, state: str | None = None, store=Depends(get_store)):
    """Redesigned Events list — the task event stream by state."""
    from ..execution.events import _count_events_by_state

    tasks = list(store.get_tasks_by_state(state) if state else store.get_all_tasks())
    total = len(tasks)
    # newest first by update time
    tasks.sort(key=lambda t: getattr(t, "updated", 0) or 0, reverse=True)
    shown = tasks[:_CAP]

    step_names: dict[str, str] = {}
    for t in shown:
        if t.step_id and t.step_id not in step_names:
            step = store.get_step(t.step_id)
            if step:
                step_names[t.step_id] = step.statement_name or step.facet_name or ""

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/events/list.html",
        {
            "events": shown,
            "total": total,
            "shown": len(shown),
            "filter_state": state,
            "step_names": step_names,
            "tab_counts": _count_events_by_state(store),
            "dot": _DOT,
            "active_nav": "events",
        },
    )
