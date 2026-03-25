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

import os
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

if TYPE_CHECKING:
    from afl.runtime.persistence import PersistenceAPI

from ..dependencies import get_store
from ..graph import compute_dag_layout
from ..helpers import (
    categorize_step_state,
    compute_step_progress,
    compute_timeline,
    effective_server_state,
    extract_handler_prefix,
    group_handlers_by_namespace,
    group_runners_by_namespace,
    group_servers_by_group,
    group_tasks_by_runner,
    group_tasks_by_state,
    lookup_facet_info,
    search_all,
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
    progress = _enrich_runners_with_progress(filtered, store)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/list.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
            "active_tab": "workflows",
            "progress": progress,
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
    progress = _enrich_runners_with_progress(filtered, store)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/_runner_groups.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
            "progress": progress,
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
    timeline = compute_timeline(all_steps, runner.start_time or 0)
    dag = compute_dag_layout(all_steps)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/detail.html",
        {
            "runner": runner,
            "steps": filtered_steps,
            "tree": tree,
            "timeline": timeline,
            "dag": dag,
            "step_counts": step_counts,
            "step_tab": step_tab,
            "active_tab": "workflows",
        },
    )


@router.get("/workflows/{runner_id}/summary/partial")
def workflow_summary_partial(
    runner_id: str,
    request: Request,
    store=Depends(get_store),
):
    """HTMX partial for auto-refresh of workflow summary and progress bar."""
    runner = store.get_runner(runner_id)
    if not runner:
        return HTMLResponse("")

    all_steps = list(store.get_steps_by_workflow(runner.workflow_id))
    step_counts = {"running": 0, "error": 0, "complete": 0, "other": 0}
    for s in all_steps:
        cat = categorize_step_state(s.state)
        step_counts[cat] = step_counts.get(cat, 0) + 1

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/_summary.html",
        {
            "runner": runner,
            "step_counts": step_counts,
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
            tree=tree,
            request=request,
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
    "down": {"down"},
}


def _apply_effective_state(servers: list) -> list:
    """Mutate each server's state to its effective value (e.g. 'down')."""
    for s in servers:
        s.state = effective_server_state(s)
    return servers


def _filter_servers(servers: list, tab: str) -> list:
    """Filter servers by tab selection."""
    allowed = _SERVER_TAB_STATES.get(tab, {"running"})
    return [s for s in servers if s.state in allowed]


def _count_servers_by_tab(servers: list) -> dict[str, int]:
    """Count servers per tab."""
    counts = {"running": 0, "startup": 0, "error": 0, "shutdown": 0, "down": 0}
    for s in servers:
        if s.state in counts:
            counts[s.state] += 1
    return counts


def _enrich_servers_with_tasks(servers: list, store: Any) -> None:
    """Attach active tasks to each server, avoiding N+1 queries."""
    # Bulk-fetch running + pending tasks and distribute by server_id
    tasks_by_server: dict[str, list] = {}
    for state in ("running", "pending"):
        for t in store.get_tasks_by_state(state):
            sid = getattr(t, "server_id", "") or ""
            if sid:
                tasks_by_server.setdefault(sid, []).append(t)

    for s in servers:
        s.active_tasks = tasks_by_server.get(s.uuid, [])
        s.active_task_count = len(s.active_tasks)


@router.get("/servers")
def server_list(
    request: Request,
    tab: str = "running",
    store=Depends(get_store),
):
    """Server list with state tabs and group accordion."""
    all_servers = _apply_effective_state(list(store.get_all_servers()))
    tab_counts = _count_servers_by_tab(all_servers)
    filtered = _filter_servers(all_servers, tab)
    _enrich_servers_with_tasks(filtered, store)
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
    all_servers = _apply_effective_state(list(store.get_all_servers()))
    tab_counts = _count_servers_by_tab(all_servers)
    filtered = _filter_servers(all_servers, tab)
    _enrich_servers_with_tasks(filtered, store)
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


def _build_server_detail_context(server: Any, store: Any) -> dict:
    """Build the template context for a server detail page."""
    tasks = list(store.get_tasks_by_server_id(server.uuid, limit=500))
    task_groups = group_tasks_by_runner(tasks, store)
    task_counts = group_tasks_by_state(tasks)
    return {
        "task_groups": task_groups,
        "task_counts": task_counts,
    }


@router.get("/servers/{server_id}")
def server_detail(
    server_id: str,
    request: Request,
    store=Depends(get_store),
):
    """Server detail page."""
    server = store.get_server(server_id)
    if server:
        server.state = effective_server_state(server)
    ctx = {
        "server": server,
        "active_tab": "servers",
        "task_groups": [],
        "task_counts": {"running": 0, "completed": 0, "failed": 0, "pending": 0, "total": 0},
    }
    if server:
        ctx.update(_build_server_detail_context(server, store))
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/servers/detail.html",
        ctx,
    )


@router.get("/servers/{server_id}/partial")
def server_detail_partial(
    server_id: str,
    request: Request,
    store=Depends(get_store),
):
    """HTMX partial for server detail refresh."""
    server = store.get_server(server_id)
    if server:
        server.state = effective_server_state(server)
    ctx = {
        "server": server,
        "task_groups": [],
        "task_counts": {"running": 0, "completed": 0, "failed": 0, "pending": 0, "total": 0},
    }
    if server:
        ctx.update(_build_server_detail_context(server, store))
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/servers/_detail_content.html",
        ctx,
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
    facet_info = lookup_facet_info(facet_name, store)
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/_detail_content.html",
        {
            "handler": handler,
            "active_tasks": active_tasks,
            "facet_info": facet_info,
            "recent_logs": recent_logs,
        },
    )


@router.get("/handlers/new")
def handler_new_form(
    request: Request,
    store=Depends(get_store),
):
    """Render create handler registration form."""
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/form.html",
        {
            "active_tab": "handlers",
            "handler": None,
            "error": None,
        },
    )


@router.post("/handlers/new")
def handler_create(
    request: Request,
    facet_name: str = Form(""),
    module_uri: str = Form(""),
    entrypoint: str = Form("handle"),
    timeout_ms: int = Form(30000),
    description: str = Form(""),
    store=Depends(get_store),
):
    """Create a new handler registration and redirect to detail."""
    from afl.runtime.entities import HandlerRegistration

    if not facet_name.strip():
        return request.app.state.templates.TemplateResponse(
            request,
            "v2/handlers/form.html",
            {
                "active_tab": "handlers",
                "handler": None,
                "error": "Facet name is required.",
            },
        )

    existing = store.get_handler_registration(facet_name.strip())
    if existing:
        return request.app.state.templates.TemplateResponse(
            request,
            "v2/handlers/form.html",
            {
                "active_tab": "handlers",
                "handler": None,
                "error": f"Handler '{facet_name}' already exists.",
            },
        )

    now_ms = int(time.time() * 1000)
    reg = HandlerRegistration(
        facet_name=facet_name.strip(),
        module_uri=module_uri.strip(),
        entrypoint=entrypoint.strip() or "handle",
        timeout_ms=timeout_ms if timeout_ms > 0 else 30000,
        metadata={"description": description.strip()} if description.strip() else {},
        created=now_ms,
        updated=now_ms,
    )
    store.save_handler_registration(reg)
    return RedirectResponse(url=f"/v2/handlers/{facet_name.strip()}", status_code=303)


@router.get("/handlers/{facet_name:path}/edit")
def handler_edit_form(
    facet_name: str,
    request: Request,
    store=Depends(get_store),
):
    """Render edit handler registration form."""
    handler = store.get_handler_registration(facet_name)
    if not handler:
        return RedirectResponse(url="/v2/handlers", status_code=303)
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/form.html",
        {
            "active_tab": "handlers",
            "handler": handler,
            "error": None,
        },
    )


@router.post("/handlers/{facet_name:path}/edit")
def handler_update(
    facet_name: str,
    request: Request,
    module_uri: str = Form(""),
    entrypoint: str = Form("handle"),
    timeout_ms: int = Form(0),
    description: str = Form(""),
    store=Depends(get_store),
):
    """Update a handler registration and redirect to detail."""
    handler = store.get_handler_registration(facet_name)
    if not handler:
        return RedirectResponse(url="/v2/handlers", status_code=303)

    from afl.runtime.entities import HandlerRegistration

    now_ms = int(time.time() * 1000)
    updated = HandlerRegistration(
        facet_name=handler.facet_name,
        module_uri=module_uri.strip() or handler.module_uri,
        entrypoint=entrypoint.strip() or handler.entrypoint,
        version=handler.version,
        checksum=handler.checksum,
        timeout_ms=timeout_ms if timeout_ms > 0 else handler.timeout_ms,
        requirements=handler.requirements,
        metadata={**handler.metadata, "description": description.strip()}
        if description.strip()
        else handler.metadata,
        created=handler.created,
        updated=now_ms,
    )
    store.save_handler_registration(updated)
    return RedirectResponse(url=f"/v2/handlers/{facet_name}", status_code=303)


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
    facet_info = lookup_facet_info(facet_name, store)
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/detail.html",
        {
            "handler": handler,
            "active_tab": "handlers",
            "active_tasks": active_tasks,
            "recent_logs": recent_logs,
            "facet_info": facet_info,
        },
    )


# ---------------------------------------------------------------------------
# PostGIS Summary
# ---------------------------------------------------------------------------


def _get_postgis_summary() -> dict | None:
    """Query PostGIS for region summary data."""
    try:
        import psycopg2
    except ImportError:
        return None

    postgis_url = os.environ.get(
        "AFL_POSTGIS_URL", "postgresql://afl_osm:afl_osm_2024@afl-postgres:5432/osm"
    )
    try:
        conn = psycopg2.connect(postgis_url)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT il.region, il.node_count, il.way_count,
                       il.imported_at::text
                FROM osm_import_log il
                WHERE il.id IN (
                    SELECT DISTINCT ON (region) id
                    FROM osm_import_log
                    ORDER BY region, imported_at DESC
                )
                ORDER BY il.region
            """)
            rows = cur.fetchall()

            cur.execute("""
                SELECT pg_size_pretty(pg_database_size(current_database()))
            """)
            db_size = cur.fetchone()[0]
        conn.close()

        regions = []
        total_nodes = 0
        total_ways = 0
        for region, nodes, ways, imported_at in rows:
            total_nodes += nodes or 0
            total_ways += ways or 0
            regions.append(
                {
                    "region": region,
                    "node_count": nodes or 0,
                    "way_count": ways or 0,
                    "total": (nodes or 0) + (ways or 0),
                    "imported_at": imported_at or "",
                }
            )

        return {
            "regions": regions,
            "total_regions": len(regions),
            "total_nodes": total_nodes,
            "total_ways": total_ways,
            "total_elements": total_nodes + total_ways,
            "db_size": db_size,
        }
    except Exception:
        return None


@router.get("/postgis")
def postgis_summary(request: Request):
    """PostGIS database summary page."""
    data = _get_postgis_summary()
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/postgis/summary.html",
        {
            "active_tab": "postgis",
            "data": data,
        },
    )


@router.get("/postgis/partial")
def postgis_summary_partial(request: Request):
    """HTMX partial for PostGIS summary refresh."""
    data = _get_postgis_summary()
    return request.app.state.templates.TemplateResponse(
        request,
        "v2/postgis/_summary_content.html",
        {
            "data": data,
        },
    )


# ---------------------------------------------------------------------------
# Global search API
# ---------------------------------------------------------------------------


@router.get("/search", name="v2_search")
def global_search(
    request: Request,
    q: str = "",
    store=Depends(get_store),
):
    """Search across all resources — returns HTML partial for command palette."""
    results = search_all(q, store)

    # Group results by type
    grouped: dict[str, list] = {}
    for r in results:
        grouped.setdefault(r["type"], []).append(r)

    templates = request.app.state.templates
    html = templates.get_template("partials/_search_results.html").render(
        results=results,
        grouped=grouped,
        query=q.strip(),
        request=request,
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Progress enrichment
# ---------------------------------------------------------------------------


def _enrich_runners_with_progress(
    runners: list,
    store: PersistenceAPI,
) -> dict[str, dict]:
    """Compute step progress for a list of runners.

    Returns a dict mapping runner UUID to progress info.
    """
    progress: dict[str, dict] = {}
    for r in runners:
        try:
            steps = list(store.get_steps_by_workflow(r.workflow_id))
            progress[r.uuid] = compute_step_progress(r, steps)
        except Exception:
            progress[r.uuid] = {"completed": 0, "total": 0, "pct": 0}
    return progress


# =============================================================================
# Fleet — aggregate task counts by facet across all servers
# =============================================================================


@router.get("/fleet")
def fleet_view(request: Request, store=Depends(get_store)):
    """Fleet overview: per-server task counts broken down by event facet."""
    all_servers = _apply_effective_state(list(store.get_all_servers()))
    running = [s for s in all_servers if s.state == "running"]

    # Collect all facet names and per-server counts
    facet_set: set[str] = set()
    server_rows: list[dict] = []
    for s in running:
        counts: dict[str, int] = {}
        for h in s.handled:
            facet_set.add(h.handler)
            counts[h.handler] = h.handled
        server_rows.append(
            {
                "uuid": s.uuid,
                "name": s.server_name or s.uuid[:12],
                "ips": ", ".join(s.server_ips),
                "handler_count": len(s.handlers),
                "counts": counts,
            }
        )

    facets = sorted(facet_set)

    # Aggregate totals per facet
    totals: dict[str, int] = {}
    for f in facets:
        totals[f] = sum(r["counts"].get(f, 0) for r in server_rows)

    # Compute max for bar scaling
    max_total = max(totals.values()) if totals else 1

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/fleet/overview.html",
        {
            "servers": server_rows,
            "facets": facets,
            "totals": totals,
            "max_total": max_total,
            "server_count": len(running),
            "active_tab": "fleet",
        },
    )


@router.get("/fleet/partial")
def fleet_partial(request: Request, store=Depends(get_store)):
    """HTMX partial for auto-refresh of fleet data."""
    all_servers = _apply_effective_state(list(store.get_all_servers()))
    running = [s for s in all_servers if s.state == "running"]

    facet_set: set[str] = set()
    server_rows: list[dict] = []
    for s in running:
        counts: dict[str, int] = {}
        for h in s.handled:
            facet_set.add(h.handler)
            counts[h.handler] = h.handled
        server_rows.append(
            {
                "uuid": s.uuid,
                "name": s.server_name or s.uuid[:12],
                "ips": ", ".join(s.server_ips),
                "handler_count": len(s.handlers),
                "counts": counts,
            }
        )

    facets = sorted(facet_set)
    totals: dict[str, int] = {}
    for f in facets:
        totals[f] = sum(r["counts"].get(f, 0) for r in server_rows)
    max_total = max(totals.values()) if totals else 1

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/fleet/_fleet_content.html",
        {
            "servers": server_rows,
            "facets": facets,
            "totals": totals,
            "max_total": max_total,
            "server_count": len(running),
        },
    )
