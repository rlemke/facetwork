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


@router.get("")
def task_list(request: Request, state: str | None = None, store=Depends(get_store)):
    """List all tasks, optionally filtered by state."""
    if state:
        tasks = store.get_tasks_by_state(state)
    else:
        tasks = store.get_all_tasks()

    step_names: dict[str, str] = {}
    for task in tasks:
        if task.step_id and task.step_id not in step_names:
            step = store.get_step(task.step_id)
            if step:
                step_names[task.step_id] = (
                    step.statement_name or step.statement_id or step.facet_name or ""
                )

    return request.app.state.templates.TemplateResponse(
        request,
        "tasks/list.html",
        {"tasks": tasks, "filter_state": state, "step_names": step_names},
    )


@router.get("/partial")
def task_list_partial(request: Request, state: str | None = None, store=Depends(get_store)):
    """HTMX partial for auto-refresh of task table."""
    if state:
        tasks = store.get_tasks_by_state(state)
    else:
        tasks = store.get_all_tasks()

    step_names: dict[str, str] = {}
    for task in tasks:
        if task.step_id and task.step_id not in step_names:
            step = store.get_step(task.step_id)
            if step:
                step_names[task.step_id] = (
                    step.statement_name or step.statement_id or step.facet_name or ""
                )

    return request.app.state.templates.TemplateResponse(
        request,
        "tasks/_table_content.html",
        {"tasks": tasks, "filter_state": state, "step_names": step_names},
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
