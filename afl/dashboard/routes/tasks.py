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

"""Task queue viewer routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..dependencies import get_store

router = APIRouter(prefix="/tasks")


def _count_tasks_by_state(store) -> dict[str, int]:
    """Count tasks per state for subnav tabs."""
    counts: dict[str, int] = {
        "all": 0,
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "ignored": 0,
        "canceled": 0,
    }
    for doc in store._db.tasks.aggregate([
        {"$group": {"_id": "$state", "count": {"$sum": 1}}},
    ]):
        state = doc["_id"]
        n = doc["count"]
        counts["all"] += n
        if state in counts:
            counts[state] = n
    return counts


def _resolve_step_names(tasks, store) -> dict[str, str]:
    """Resolve step names for a list of tasks."""
    step_names: dict[str, str] = {}
    for task in tasks:
        if task.step_id and task.step_id not in step_names:
            step = store.get_step(task.step_id)
            if step:
                step_names[task.step_id] = (
                    step.statement_name or step.statement_id or step.facet_name or ""
                )
    return step_names


def _resolve_server_info(tasks, store) -> dict[str, dict]:
    """Resolve server name and ping time for tasks."""
    server_info: dict[str, dict] = {}
    for task in tasks:
        sid = task.server_id
        if sid and sid not in server_info:
            server = store.get_server(sid)
            if server:
                server_info[sid] = {
                    "name": server.server_name,
                    "ping_time": server.ping_time,
                }
    return server_info


@router.get("")
def task_list(request: Request, state: str | None = None, store=Depends(get_store)):
    """List all tasks, optionally filtered by state."""
    if state:
        tasks = store.get_tasks_by_state(state)
    else:
        tasks = store.get_all_tasks()

    step_names = _resolve_step_names(tasks, store)
    server_info = _resolve_server_info(tasks, store)
    tab_counts = _count_tasks_by_state(store)

    return request.app.state.templates.TemplateResponse(
        request,
        "tasks/list.html",
        {
            "tasks": tasks,
            "filter_state": state,
            "step_names": step_names,
            "server_info": server_info,
            "tab_counts": tab_counts,
            "active_tab": "tasks",
        },
    )


@router.get("/partial")
def task_list_partial(request: Request, state: str | None = None, store=Depends(get_store)):
    """HTMX partial for auto-refresh of task table."""
    if state:
        tasks = store.get_tasks_by_state(state)
    else:
        tasks = store.get_all_tasks()

    step_names = _resolve_step_names(tasks, store)
    server_info = _resolve_server_info(tasks, store)

    return request.app.state.templates.TemplateResponse(
        request,
        "tasks/_table_content.html",
        {"tasks": tasks, "filter_state": state, "step_names": step_names, "server_info": server_info},
    )


@router.get("/{task_id}")
def task_detail(task_id: str, request: Request, store=Depends(get_store)):
    """Show task detail."""
    task = store.get_task(task_id)
    step_name = ""
    if task and task.step_id:
        step = store.get_step(task.step_id)
        if step:
            step_name = step.statement_name or step.statement_id or step.facet_name or ""
    return request.app.state.templates.TemplateResponse(
        request,
        "tasks/detail.html",
        {"task": task, "step_name": step_name},
    )
