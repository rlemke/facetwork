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

"""Dashboard v2 routes — namespace-grouped workflow, server, and handler views."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..dependencies import get_store
from ..helpers import (
    categorize_step_state,
    extract_handler_prefix,
    group_handlers_by_namespace,
    group_runners_by_namespace,
    group_servers_by_group,
    short_workflow_name,
)
from ..tree import build_step_tree

router = APIRouter(prefix="/v2")

# Runner states by tab
_RUNNING_STATES = {"created", "running", "paused"}
_COMPLETED_STATES = {"completed"}
_FAILED_STATES = {"failed", "cancelled"}

_TAB_STATES = {
    "running": _RUNNING_STATES,
    "completed": _COMPLETED_STATES,
    "failed": _FAILED_STATES,
}


def _filter_runners(runners: list, tab: str) -> list:
    """Filter runners by tab selection."""
    allowed = _TAB_STATES.get(tab, _RUNNING_STATES)
    return [r for r in runners if r.state in allowed]


def _count_by_tab(runners: list) -> dict[str, int]:
    """Count runners per tab."""
    counts = {"running": 0, "completed": 0, "failed": 0}
    for r in runners:
        if r.state in _RUNNING_STATES:
            counts["running"] += 1
        elif r.state in _COMPLETED_STATES:
            counts["completed"] += 1
        elif r.state in _FAILED_STATES:
            counts["failed"] += 1
    return counts


@router.get("/workflows")
def workflow_list(
    request: Request,
    tab: str = "running",
    store=Depends(get_store),
):
    """Main workflow list with state tabs and namespace grouping."""
    all_runners = store.get_all_runners(limit=1000)
    tab_counts = _count_by_tab(all_runners)
    filtered = _filter_runners(all_runners, tab)
    groups = group_runners_by_namespace(filtered)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/list.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
            "active_tab": "workflows",
        },
    )


@router.get("/workflows/partial")
def workflow_list_partial(
    request: Request,
    tab: str = "running",
    store=Depends(get_store),
):
    """HTMX partial for auto-refresh of runner groups."""
    all_runners = store.get_all_runners(limit=1000)
    tab_counts = _count_by_tab(all_runners)
    filtered = _filter_runners(all_runners, tab)
    groups = group_runners_by_namespace(filtered)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/_runner_groups.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
        },
    )


@router.get("/workflows/{runner_id}")
def workflow_detail(
    runner_id: str,
    request: Request,
    step_tab: str = "running",
    store=Depends(get_store),
):
    """Workflow detail with step sub-tabs."""
    runner = store.get_runner(runner_id)
    if not runner:
        return request.app.state.templates.TemplateResponse(
            request,
            "v2/workflows/detail.html",
            {
                "runner": None,
                "steps": [],
                "step_counts": {},
                "step_tab": step_tab,
                "active_tab": "workflows",
            },
        )

    all_steps = list(store.get_steps_by_workflow(runner.workflow_id))

    # Categorize and count steps
    step_counts = {"running": 0, "error": 0, "complete": 0, "other": 0}
    for s in all_steps:
        cat = categorize_step_state(s.state)
        step_counts[cat] = step_counts.get(cat, 0) + 1

    # Filter steps by selected tab
    filtered_steps = [s for s in all_steps if categorize_step_state(s.state) == step_tab]

    tree = build_step_tree(all_steps)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/detail.html",
        {
            "runner": runner,
            "steps": filtered_steps,
            "tree": tree,
            "step_counts": step_counts,
            "step_tab": step_tab,
            "active_tab": "workflows",
        },
    )


@router.get("/workflows/{runner_id}/steps/partial")
def step_rows_partial(
    runner_id: str,
    request: Request,
    step_tab: str = "running",
    view: str = "flat",
    store=Depends(get_store),
):
    """HTMX partial for step table refresh."""
    runner = store.get_runner(runner_id)
    if not runner:
        if view == "tree":
            return HTMLResponse("")
        return request.app.state.templates.TemplateResponse(
            request,
            "v2/workflows/_step_rows.html",
            {"steps": [], "runner": None, "step_tab": step_tab, "step_counts": {}},
        )

    all_steps = list(store.get_steps_by_workflow(runner.workflow_id))
    step_counts = {"running": 0, "error": 0, "complete": 0, "other": 0}
    for s in all_steps:
        cat = categorize_step_state(s.state)
        step_counts[cat] = step_counts.get(cat, 0) + 1

    if view == "tree":
        tree = build_step_tree(all_steps)
        templates = request.app.state.templates
        html = templates.get_template("partials/step_tree.html").render(
            tree=tree, request=request,
        )
        return HTMLResponse(html)

    filtered_steps = [s for s in all_steps if categorize_step_state(s.state) == step_tab]

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/_step_rows.html",
        {
            "steps": filtered_steps,
            "runner": runner,
            "step_tab": step_tab,
            "step_counts": step_counts,
        },
    )


@router.get("/workflows/{runner_id}/steps/{step_id}/expand")
def step_detail_expand(
    runner_id: str,
    step_id: str,
    request: Request,
    store=Depends(get_store),
):
    """HTMX partial for inline step expansion."""
    runner = store.get_runner(runner_id)
    step = store.get_step(step_id) if runner else None
    task = None
    if step:
        try:
            task = store.get_task_for_step(step_id)
        except Exception:
            pass

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/_step_detail.html",
        {
            "step": step,
            "task": task,
            "runner": runner,
        },
    )


# ---------------------------------------------------------------------------
# Server views
# ---------------------------------------------------------------------------

_SERVER_TAB_STATES = {
    "running": {"running"},
    "startup": {"startup"},
    "error": {"error"},
    "shutdown": {"shutdown"},
}


def _filter_servers(servers: list, tab: str) -> list:
    """Filter servers by tab selection."""
    allowed = _SERVER_TAB_STATES.get(tab, {"running"})
    return [s for s in servers if s.state in allowed]


def _count_servers_by_tab(servers: list) -> dict[str, int]:
    """Count servers per tab."""
    counts = {"running": 0, "startup": 0, "error": 0, "shutdown": 0}
    for s in servers:
        if s.state in counts:
            counts[s.state] += 1
    return counts


@router.get("/servers")
def server_list(
    request: Request,
    tab: str = "running",
    store=Depends(get_store),
):
    """Server list with state tabs and group accordion."""
    all_servers = list(store.get_all_servers())
    tab_counts = _count_servers_by_tab(all_servers)
    filtered = _filter_servers(all_servers, tab)
    groups = group_servers_by_group(filtered)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/servers/list.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
            "active_tab": "servers",
        },
    )


@router.get("/servers/partial")
def server_list_partial(
    request: Request,
    tab: str = "running",
    store=Depends(get_store),
):
    """HTMX partial for auto-refresh of server groups."""
    all_servers = list(store.get_all_servers())
    tab_counts = _count_servers_by_tab(all_servers)
    filtered = _filter_servers(all_servers, tab)
    groups = group_servers_by_group(filtered)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/servers/_server_groups.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
        },
    )


@router.get("/servers/{server_id}")
def server_detail(
    server_id: str,
    request: Request,
    store=Depends(get_store),
):
    """Server detail page."""
    server = store.get_server(server_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/servers/detail.html",
        {
            "server": server,
            "active_tab": "servers",
        },
    )


@router.get("/servers/{server_id}/partial")
def server_detail_partial(
    server_id: str,
    request: Request,
    store=Depends(get_store),
):
    """HTMX partial for server detail refresh."""
    server = store.get_server(server_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/servers/_detail_content.html",
        {
            "server": server,
        },
    )


# ---------------------------------------------------------------------------
# Handler views
# ---------------------------------------------------------------------------


def _count_handlers_by_prefix(handlers: list) -> dict[str, int]:
    """Count handlers per namespace prefix tab, including 'all'."""
    counts: dict[str, int] = {"all": len(handlers)}
    for h in handlers:
        prefix = extract_handler_prefix(h.facet_name)
        counts[prefix] = counts.get(prefix, 0) + 1
    return counts


def _filter_handlers_by_prefix(handlers: list, tab: str) -> list:
    """Filter handlers by namespace prefix tab."""
    if tab == "all":
        return handlers
    return [h for h in handlers if extract_handler_prefix(h.facet_name) == tab]


@router.get("/handlers")
def handler_list(
    request: Request,
    tab: str = "all",
    store=Depends(get_store),
):
    """Handler list with namespace-prefix tabs and namespace-group accordion."""
    all_handlers = store.list_handler_registrations()
    tab_counts = _count_handlers_by_prefix(all_handlers)
    # Build sorted list of unique prefixes (excluding 'all')
    prefixes = sorted({extract_handler_prefix(h.facet_name) for h in all_handlers})
    filtered = _filter_handlers_by_prefix(all_handlers, tab)
    groups = group_handlers_by_namespace(filtered)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/list.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
            "prefixes": prefixes,
            "active_tab": "handlers",
        },
    )


@router.get("/handlers/partial")
def handler_list_partial(
    request: Request,
    tab: str = "all",
    store=Depends(get_store),
):
    """HTMX partial for auto-refresh of handler groups."""
    all_handlers = store.list_handler_registrations()
    filtered = _filter_handlers_by_prefix(all_handlers, tab)
    groups = group_handlers_by_namespace(filtered)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/_handler_groups.html",
        {
            "groups": groups,
        },
    )


@router.get("/handlers/{facet_name:path}/partial")
def handler_detail_partial(
    facet_name: str,
    request: Request,
    store=Depends(get_store),
):
    """HTMX partial for handler detail refresh."""
    handler = store.get_handler_registration(facet_name)
    active_tasks = store.get_tasks_by_facet_name(facet_name, states=["pending", "running"])
    recent_logs = store.get_step_logs_by_facet(facet_name, limit=20)
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/_detail_content.html",
        {
            "handler": handler,
            "active_tasks": active_tasks,
            "recent_logs": recent_logs,
        },
    )


@router.post("/handlers/{facet_name:path}/delete")
def handler_delete(
    facet_name: str,
    store=Depends(get_store),
):
    """Delete a handler registration and redirect to list."""
    store.delete_handler_registration(facet_name)
    return RedirectResponse(url="/v2/handlers", status_code=303)


@router.get("/handlers/{facet_name:path}")
def handler_detail(
    facet_name: str,
    request: Request,
    store=Depends(get_store),
):
    """Handler detail page."""
    handler = store.get_handler_registration(facet_name)
    active_tasks = store.get_tasks_by_facet_name(facet_name, states=["pending", "running"])
    recent_logs = store.get_step_logs_by_facet(facet_name, limit=20)
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/detail.html",
        {
            "handler": handler,
            "active_tab": "handlers",
            "active_tasks": active_tasks,
            "recent_logs": recent_logs,
        },
    )
