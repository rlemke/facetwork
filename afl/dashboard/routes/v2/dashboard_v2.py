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

from ...dependencies import get_store
from ...graph import compute_dag_layout
from ...helpers import (
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
    qualify_step_names,
    search_all,
)
from ...tree import build_step_tree

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

    # Add qualified display names (e.g. "Algeria.imp" instead of "imp")
    qualify_step_names(all_steps)

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


@router.get("/workflows/{runner_id}/tasks/partial")
def task_progress_partial(
    runner_id: str,
    request: Request,
    store=Depends(get_store),
):
    """HTMX partial for task progress view with last log per task."""
    runner = store.get_runner(runner_id)
    if not runner:
        return HTMLResponse("")

    tasks = list(store.get_tasks_by_workflow(runner.workflow_id))
    now_ms = int(time.time() * 1000)

    # Build server name lookup
    server_names: dict[str, str] = {}
    for t in tasks:
        sid = t.server_id
        if sid and sid not in server_names:
            srv = store.get_server(sid)
            server_names[sid] = srv.server_name if srv else sid[:12]

    # Count states
    from collections import Counter

    state_counts = Counter(t.state for t in tasks)
    total = len(tasks)
    completed = state_counts.get("completed", 0)

    # Build outstanding task list with last log
    outstanding = []
    for t in tasks:
        if t.state == "completed":
            continue
        # Extract region from task data (may be a plain string or
        # an AttributeValue dict with {name, value, type_hint})
        data = t.data if isinstance(t.data, dict) else {}
        region = data.get("region", "")
        if isinstance(region, dict):
            region = region.get("value", "")
        if not region and isinstance(data.get("cache"), dict):
            cache = data["cache"]
            region = cache.get("region", "")
            if isinstance(region, dict):
                region = region.get("value", "")

        # Get last log for this step
        last_log_entry = store._db.step_logs.find_one({"step_id": t.step_id}, sort=[("time", -1)])
        last_log = ""
        last_log_full = ""
        last_log_age = ""
        last_log_age_s = 0.0
        last_log_level = ""
        if last_log_entry:
            last_log_age_s = (now_ms - last_log_entry.get("time", 0)) / 1000
            if last_log_age_s < 60:
                last_log_age = f"{last_log_age_s:.0f}s ago"
            elif last_log_age_s < 3600:
                last_log_age = f"{last_log_age_s / 60:.0f}m ago"
            else:
                last_log_age = f"{last_log_age_s / 3600:.1f}h ago"
            last_log_full = last_log_entry.get("message", "")
            last_log = last_log_full[:80]
            last_log_level = last_log_entry.get("level", "")

        outstanding.append(
            {
                "name": t.name,
                "region": region,
                "state": t.state,
                "server_name": server_names.get(t.server_id, ""),
                "step_id": t.step_id,
                "runner_id": t.runner_id,
                "last_log": last_log,
                "last_log_full": last_log_full,
                "last_log_age": last_log_age,
                "last_log_age_s": last_log_age_s,
                "last_log_level": last_log_level,
            }
        )

    outstanding.sort(key=lambda x: (x["name"], x["region"]))

    # --- Generate insights ---
    insights: list[str] = []
    running_tasks = [t for t in outstanding if t["state"] == "running"]
    pending_tasks = [t for t in outstanding if t["state"] == "pending"]
    failed_tasks = [t for t in outstanding if t["state"] == "failed"]
    active_tasks = [t for t in running_tasks if t["last_log_age_s"] < 300]
    stale_tasks = [t for t in running_tasks if t["last_log_age_s"] >= 1800]

    if active_tasks:
        names = ", ".join(t["region"] or t["name"] for t in active_tasks)
        insights.append(f"Actively processing: {names}")

    if stale_tasks:
        names = ", ".join(t["region"] or t["name"] for t in stale_tasks)
        stale_age = max(t["last_log_age_s"] for t in stale_tasks)
        if stale_age < 7200:
            age_str = f"{stale_age / 60:.0f}m"
        else:
            age_str = f"{stale_age / 3600:.1f}h"
        if len(active_tasks) >= 3:
            insights.append(
                f"Stale ({names}): no log activity for ~{age_str} "
                f"— likely waiting for DB connections while "
                f"{len(active_tasks)} other imports are active. "
                f"Will proceed when active imports finish."
            )
        else:
            insights.append(
                f"Stale ({names}): no log activity for ~{age_str} "
                f"— may be blocked or waiting for resources."
            )

    if pending_tasks:
        names = ", ".join(t["region"] or t["name"] for t in pending_tasks)
        # Check if already imported
        already_imported = [
            t
            for t in pending_tasks
            if t["last_log_level"] == "success" or "imported" in t.get("last_log_full", "").lower()
        ]
        if already_imported:
            imported_names = ", ".join(t["region"] or t["name"] for t in already_imported)
            insights.append(
                f"Pending — already imported ({imported_names}): "
                f"will skip automatically when claimed."
            )
            remaining = [t for t in pending_tasks if t not in already_imported]
            if remaining:
                rem_names = ", ".join(t["region"] or t["name"] for t in remaining)
                insights.append(f"Pending — awaiting runner capacity: {rem_names}")
        else:
            insights.append(
                f"Pending ({names}): waiting for runner capacity "
                f"({len(running_tasks)} task(s) currently running)."
            )

    if failed_tasks:
        for t in failed_tasks:
            error_hint = ""
            log = t.get("last_log_full", "")
            if "connection" in log.lower() or "password" in log.lower():
                error_hint = " — likely a database connection issue"
            elif "duplicate key" in log.lower():
                error_hint = " — concurrent resource contention"
            insights.append(
                f"Failed ({t['region'] or t['name']}): "
                f"{t['last_log']}{error_hint}. "
                f"Will be retried by the stuck task watchdog."
            )

    if not outstanding:
        insights.append("All tasks completed successfully.")

    progress = {
        "total": total,
        "completed": completed,
        "running": state_counts.get("running", 0),
        "pending": state_counts.get("pending", 0),
        "failed": state_counts.get("failed", 0),
        "pct": round(completed * 100 / total) if total else 0,
        "outstanding": outstanding,
        "insights": insights,
    }

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/workflows/_task_progress.html",
        {"task_progress": progress, "runner": runner},
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
    qualify_step_names(all_steps)

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
    step_logs = []
    names: dict[str, str] = {}
    if step:
        try:
            task = store.get_task_for_step(step_id)
        except Exception:
            pass
        try:
            step_logs = store.get_step_logs_by_step(step_id)
        except Exception:
            pass
        # Resolve hierarchy names
        if step.facet_name:
            names["facet"] = step.facet_name
        if step.container_id:
            try:
                container = store.get_step(step.container_id)
                if container:
                    names["container"] = (
                        container.statement_name or container.facet_name or container.object_type
                    )
            except Exception:
                pass
        if step.block_id:
            try:
                block = store.get_step(step.block_id)
                if block:
                    names["block"] = block.statement_name or block.facet_name or block.object_type
            except Exception:
                pass

    # Compute timeout remaining for running tasks
    timeout_remaining_ms: int | None = None
    if task and task.state == "running":
        import os
        import time

        exec_timeout_ms = int(os.environ.get("AFL_TASK_EXECUTION_TIMEOUT_MS", "900000"))
        if exec_timeout_ms > 0:
            now_ms = int(time.time() * 1000)
            last_activity = task.task_heartbeat or task.updated or task.created
            elapsed_ms = now_ms - last_activity
            timeout_remaining_ms = max(0, exec_timeout_ms - elapsed_ms)

    ctx = {
        "step": step,
        "task": task,
        "runner": runner,
        "step_logs": step_logs,
        "names": names,
        "timeout_remaining_ms": timeout_remaining_ms,
    }

    # HTMX requests get the partial; direct browser visits get a full page
    if request.headers.get("HX-Request"):
        template = "v2/workflows/_step_detail.html"
    else:
        template = "v2/workflows/step_detail_page.html"

    return request.app.state.templates.TemplateResponse(request, template, ctx)


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
    all_tasks: list = []
    for state in ("running", "pending"):
        for t in store.get_tasks_by_state(state):
            sid = getattr(t, "server_id", "") or ""
            if sid:
                tasks_by_server.setdefault(sid, []).append(t)
            all_tasks.append(t)

    # Bulk-resolve step paths for display
    step_ids = [t.step_id for t in all_tasks if t.step_id]
    if step_ids:
        _resolve_task_step_paths(all_tasks, step_ids, store)

    for s in servers:
        s.active_tasks = tasks_by_server.get(s.uuid, [])
        s.active_task_count = len(s.active_tasks)


def _resolve_task_step_paths(tasks: list, step_ids: list[str], store: Any) -> None:
    """Build a display path for each task from its step hierarchy."""
    # Batch-fetch all referenced steps
    steps_cache: dict = {}
    for sid in step_ids:
        try:
            step = store.get_step(sid)
            if step:
                steps_cache[sid] = step
        except Exception:
            pass

    # Fetch container steps (two levels up)
    for _level in range(2):
        new_ids = {
            s.container_id
            for s in steps_cache.values()
            if getattr(s, "container_id", None) and s.container_id not in steps_cache
        }
        for cid in new_ids:
            try:
                step = store.get_step(cid)
                if step:
                    steps_cache[cid] = step
            except Exception:
                pass

    # Build path: grandparent > parent > step
    for t in tasks:
        if not t.step_id or t.step_id not in steps_cache:
            t.step_path = None
            continue
        step = steps_cache[t.step_id]
        # Walk up the container chain collecting names
        chain: list[str] = []
        current = step
        for _depth in range(3):  # self + 2 ancestor levels
            name = getattr(current, "statement_name", None) or getattr(current, "facet_name", None)
            if name:
                chain.append(name)
            cid = getattr(current, "container_id", None)
            if not cid or cid not in steps_cache:
                break
            current = steps_cache[cid]
        chain.reverse()
        t.step_path = " > ".join(chain) if chain else None


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


def _build_handler_stats(
    store: Any,
) -> tuple[set[str], set[str], dict[str, dict[str, int]]]:
    """Build active/busy handler sets and aggregate handled counts.

    Returns:
        (active_handlers, busy_handlers, handler_stats) where:
        - active_handlers: facet names with at least one live runner
        - busy_handlers: facet names currently processing a running task
        - handler_stats: facet_name -> {"handled": N, "not_handled": N}
    """
    now_ms = int(time.time() * 1000)
    active_handlers: set[str] = set()
    handler_stats: dict[str, dict[str, int]] = {}

    for srv in store._db.servers.find():
        is_alive = srv.get("state") == "running" and (now_ms - srv.get("ping_time", 0)) < 60_000
        if is_alive:
            for h_name in srv.get("handlers", []):
                active_handlers.add(h_name)

        for entry in srv.get("handled", []):
            name = entry.get("handler", "")
            if name not in handler_stats:
                handler_stats[name] = {"handled": 0, "not_handled": 0}
            handler_stats[name]["handled"] += entry.get("handled", 0)
            handler_stats[name]["not_handled"] += entry.get("not_handled", 0)

    # Busy handlers: currently processing at least one running task
    busy_handlers: set[str] = set()
    for task in store._db.tasks.find({"state": "running"}, {"name": 1}):
        busy_handlers.add(task.get("name", ""))

    return active_handlers, busy_handlers, handler_stats


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

    active_handlers, busy_handlers, handler_stats = _build_handler_stats(store)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/list.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
            "prefixes": prefixes,
            "active_tab": "handlers",
            "active_handlers": active_handlers,
            "busy_handlers": busy_handlers,
            "handler_stats": handler_stats,
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

    active_handlers, busy_handlers, handler_stats = _build_handler_stats(store)

    return request.app.state.templates.TemplateResponse(
        request,
        "v2/handlers/_handler_groups.html",
        {
            "groups": groups,
            "active_handlers": active_handlers,
            "busy_handlers": busy_handlers,
            "handler_stats": handler_stats,
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
        "AFL_POSTGIS_URL", "postgresql://afl:afl@afl-postgres:5432/afl_gis"
    )
    try:
        conn = psycopg2.connect(postgis_url, gssencmode="disable")
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
