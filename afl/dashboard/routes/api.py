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

"""JSON API endpoints — used by htmx partials and for programmatic access."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from ..dependencies import get_store
from ..tree import build_step_tree

router = APIRouter(prefix="/api")


# -- Runners -----------------------------------------------------------------


@router.get("/runners")
def api_runners(
    request: Request,
    state: str | None = None,
    partial: bool = False,
    store=Depends(get_store),
):
    """Return runners as JSON or htmx partial rows."""
    if state:
        runners = store.get_runners_by_state(state)
    else:
        runners = store.get_all_runners()

    if partial:
        templates = request.app.state.templates
        html = ""
        for runner in runners:
            html += templates.get_template("partials/runner_row.html").render(
                runner=runner, request=request
            )
        return HTMLResponse(html)

    return JSONResponse([_runner_dict(r) for r in runners])


@router.get("/runners/{runner_id}")
def api_runner_detail(runner_id: str, store=Depends(get_store)):
    """Return a single runner as JSON."""
    runner = store.get_runner(runner_id)
    if not runner:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_runner_dict(runner))


@router.get("/runners/{runner_id}/steps")
def api_runner_steps(
    runner_id: str,
    request: Request,
    partial: bool = False,
    view: str = "flat",
    store=Depends(get_store),
):
    """Return steps for a runner's workflow."""
    runner = store.get_runner(runner_id)
    if not runner:
        return JSONResponse({"error": "not found"}, status_code=404)

    steps = store.get_steps_by_workflow(runner.workflow_id)

    if partial:
        templates = request.app.state.templates
        step_log_counts = _build_step_log_counts(store, runner.workflow_id)
        if view == "tree":
            tree = build_step_tree(list(steps))
            html = templates.get_template("partials/step_tree.html").render(
                tree=tree, request=request, step_log_counts=step_log_counts
            )
            return HTMLResponse(html)
        html = ""
        for step in steps:
            html += templates.get_template("partials/step_row.html").render(
                step=step, request=request, step_log_counts=step_log_counts
            )
        return HTMLResponse(html)

    return JSONResponse([_step_dict(s) for s in steps])


# -- Steps -------------------------------------------------------------------


@router.get("/steps/{step_id}/logs")
def api_step_logs(step_id: str, store=Depends(get_store)):
    """Return step log entries as JSON array."""
    logs = store.get_step_logs_by_step(step_id)
    return JSONResponse(
        [
            {
                "uuid": log.uuid,
                "step_id": log.step_id,
                "workflow_id": log.workflow_id,
                "runner_id": log.runner_id,
                "facet_name": log.facet_name,
                "source": log.source,
                "level": log.level,
                "message": log.message,
                "details": log.details,
                "time": log.time,
            }
            for log in logs
        ]
    )


def _log_to_sse_dict(log) -> dict:
    """Convert a StepLogEntry to a JSON-serializable dict for SSE."""
    return {
        "uuid": log.uuid,
        "step_id": log.step_id,
        "workflow_id": log.workflow_id,
        "runner_id": log.runner_id,
        "facet_name": log.facet_name,
        "source": log.source,
        "level": log.level,
        "message": log.message,
        "details": log.details,
        "time": log.time,
    }


@router.get("/steps/{step_id}/logs/stream")
async def api_step_log_stream(step_id: str, store=Depends(get_store)):
    """SSE endpoint for streaming step log updates."""

    async def generate():
        last_time = 0
        while True:
            logs = store.get_step_logs_since(step_id, last_time)
            for log in logs:
                yield f"data: {json.dumps(_log_to_sse_dict(log))}\n\n"
                last_time = max(last_time, log.time)
            await asyncio.sleep(1.5)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/runners/{runner_id}/logs/stream")
async def api_workflow_log_stream(runner_id: str, store=Depends(get_store)):
    """SSE endpoint for streaming workflow-level log updates."""
    runner = store.get_runner(runner_id)
    if not runner:
        return JSONResponse({"error": "not found"}, status_code=404)

    workflow_id = runner.workflow_id

    async def generate():
        last_time = 0
        while True:
            logs = store.get_workflow_logs_since(workflow_id, last_time)
            for log in logs:
                yield f"data: {json.dumps(_log_to_sse_dict(log))}\n\n"
                last_time = max(last_time, log.time)
            await asyncio.sleep(1.5)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/steps/{step_id}")
def api_step_detail(step_id: str, store=Depends(get_store)):
    """Return a single step as JSON."""
    step = store.get_step(step_id)
    if not step:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_step_dict(step))


# -- Tasks -------------------------------------------------------------------


@router.get("/tasks")
def api_tasks(state: str | None = None, store=Depends(get_store)):
    """Return all tasks as JSON, optionally filtered by state."""
    if state:
        tasks = store.get_tasks_by_state(state)
    else:
        tasks = store.get_all_tasks()
    return JSONResponse([_task_dict(t) for t in tasks])


@router.get("/tasks/{task_id}")
def api_task_detail(task_id: str, store=Depends(get_store)):
    """Return a single task as JSON."""
    task = store.get_task(task_id)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_task_dict(task))


# -- Flows -------------------------------------------------------------------


@router.get("/flows")
def api_flows(q: str | None = None, store=Depends(get_store)):
    """Return all flows as JSON, optionally filtered by name search."""
    flows = store.get_all_flows()
    if q:
        flows = [f for f in flows if q.lower() in f.name.name.lower()]
    return JSONResponse(
        [
            {
                "uuid": f.uuid,
                "name": f.name.name,
                "path": f.name.path,
                "workflows": len(f.workflows),
                "sources": len(f.compiled_sources),
            }
            for f in flows
        ]
    )


@router.get("/flows/{flow_id}")
def api_flow_detail(flow_id: str, store=Depends(get_store)):
    """Return a single flow as JSON."""
    flow = store.get_flow(flow_id)
    if not flow:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(
        {
            "uuid": flow.uuid,
            "name": flow.name.name,
            "path": flow.name.path,
            "namespaces": [{"uuid": n.uuid, "name": n.name} for n in flow.namespaces],
            "facets": [
                {"uuid": f.uuid, "name": f.name, "namespace_id": f.namespace_id}
                for f in flow.facets
            ],
            "workflows": len(flow.workflows),
            "sources": len(flow.compiled_sources),
        }
    )


# -- Namespaces --------------------------------------------------------------


@router.get("/namespaces")
def api_namespaces(store=Depends(get_store)):
    """Return namespaces aggregated across all flows."""
    flows = store.get_all_flows()
    namespaces = []
    for flow in flows:
        for ns in flow.namespaces:
            namespaces.append(
                {
                    "uuid": ns.uuid,
                    "name": ns.name,
                    "flow_id": flow.uuid,
                    "flow_name": flow.name.name,
                }
            )
    return JSONResponse(namespaces)


# -- Servers -----------------------------------------------------------------


@router.get("/servers")
def api_servers(state: str | None = None, store=Depends(get_store)):
    """Return all servers as JSON, optionally filtered by state."""
    if state:
        servers = store.get_servers_by_state(state)
    else:
        servers = store.get_all_servers()
    return JSONResponse(
        [
            {
                "uuid": s.uuid,
                "server_name": s.server_name,
                "server_group": s.server_group,
                "service_name": s.service_name,
                "state": s.state,
                "ping_time": s.ping_time,
                "handlers": s.handlers,
            }
            for s in servers
        ]
    )


# -- Handlers ----------------------------------------------------------------


@router.get("/handlers")
def api_handlers(q: str | None = None, store=Depends(get_store)):
    """Return all handler registrations as JSON, optionally filtered by facet name."""
    handlers = store.list_handler_registrations()
    if q:
        handlers = [h for h in handlers if q.lower() in h.facet_name.lower()]
    return JSONResponse(
        [
            {
                "facet_name": h.facet_name,
                "module_uri": h.module_uri,
                "entrypoint": h.entrypoint,
                "version": h.version,
                "timeout_ms": h.timeout_ms,
                "created": h.created,
                "updated": h.updated,
            }
            for h in handlers
        ]
    )


@router.post("/handlers")
async def api_handler_create(request: Request, store=Depends(get_store)):
    """Create a new handler registration from JSON body."""
    import time

    from afl.runtime.entities import HandlerRegistration

    body = await request.json()
    facet_name = body.get("facet_name", "").strip()
    if not facet_name:
        return JSONResponse({"error": "facet_name is required"}, status_code=400)

    existing = store.get_handler_registration(facet_name)
    if existing:
        return JSONResponse({"error": f"Handler '{facet_name}' already exists"}, status_code=409)

    now_ms = int(time.time() * 1000)
    reg = HandlerRegistration(
        facet_name=facet_name,
        module_uri=body.get("module_uri", "").strip(),
        entrypoint=body.get("entrypoint", "handle").strip(),
        metadata=body.get("metadata", {}),
        created=now_ms,
        updated=now_ms,
    )
    store.save_handler_registration(reg)
    return JSONResponse({"facet_name": reg.facet_name, "created": True}, status_code=201)


@router.put("/handlers/{facet_name:path}")
async def api_handler_update(facet_name: str, request: Request, store=Depends(get_store)):
    """Update an existing handler registration from JSON body."""
    import time

    from afl.runtime.entities import HandlerRegistration

    handler = store.get_handler_registration(facet_name)
    if not handler:
        return JSONResponse({"error": "not found"}, status_code=404)

    body = await request.json()
    now_ms = int(time.time() * 1000)
    updated = HandlerRegistration(
        facet_name=handler.facet_name,
        module_uri=body.get("module_uri", handler.module_uri).strip(),
        entrypoint=body.get("entrypoint", handler.entrypoint).strip(),
        version=handler.version,
        checksum=handler.checksum,
        timeout_ms=body.get("timeout_ms", handler.timeout_ms),
        requirements=handler.requirements,
        metadata={**handler.metadata, **body.get("metadata", {})},
        created=handler.created,
        updated=now_ms,
    )
    store.save_handler_registration(updated)
    return JSONResponse({"facet_name": updated.facet_name, "updated": True})


@router.get("/servers/{server_id}")
def api_server_detail(server_id: str, store=Depends(get_store)):
    """Return a single server as JSON."""
    server = store.get_server(server_id)
    if not server:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(
        {
            "uuid": server.uuid,
            "server_name": server.server_name,
            "server_group": server.server_group,
            "service_name": server.service_name,
            "state": server.state,
            "ping_time": server.ping_time,
            "handlers": server.handlers,
            "topics": server.topics,
        }
    )


# -- Locks -------------------------------------------------------------------


@router.get("/locks")
def api_locks(store=Depends(get_store)):
    """Return all locks as JSON."""
    locks = store.get_all_locks()
    return JSONResponse(
        [
            {
                "key": lock.key,
                "acquired_at": lock.acquired_at,
                "expires_at": lock.expires_at,
                "meta": {
                    "topic": lock.meta.topic,
                    "handler": lock.meta.handler,
                    "step_name": lock.meta.step_name,
                    "step_id": lock.meta.step_id,
                }
                if lock.meta
                else None,
            }
            for lock in locks
        ]
    )


# -- Sources -----------------------------------------------------------------


@router.get("/sources")
def api_sources(q: str | None = None, store=Depends(get_store)):
    """Return all published sources as JSON, optionally filtered by namespace."""
    sources = store.list_published_sources()
    if q:
        sources = [s for s in sources if q.lower() in s.namespace_name.lower()]
    return JSONResponse(
        [
            {
                "uuid": s.uuid,
                "namespace_name": s.namespace_name,
                "version": s.version,
                "origin": s.origin,
                "published_at": s.published_at,
                "checksum": s.checksum,
                "namespaces_defined": s.namespaces_defined,
            }
            for s in sources
        ]
    )


# -- Events ------------------------------------------------------------------


@router.get("/events")
def api_events(state: str | None = None, store=Depends(get_store)):
    """Return all events (tasks) as JSON, optionally filtered by state."""
    if state:
        tasks = store.get_tasks_by_state(state)
    else:
        tasks = store.get_all_tasks()

    step_names: dict[str, str] = {}
    for t in tasks:
        if t.step_id and t.step_id not in step_names:
            step = store.get_step(t.step_id)
            if step:
                step_names[t.step_id] = step.statement_name or step.facet_name or ""

    return JSONResponse(
        [
            {
                "id": t.uuid,
                "step_id": t.step_id,
                "step_name": step_names.get(t.step_id, ""),
                "workflow_id": t.workflow_id,
                "state": t.state,
                "event_type": t.name,
                "payload": t.data,
            }
            for t in tasks
        ]
    )


# -- Helpers -----------------------------------------------------------------


def _build_step_log_counts(store, workflow_id: str) -> dict[str, int]:
    """Build a dict mapping step_id -> log count for a workflow."""
    counts: dict[str, int] = {}
    try:
        step_logs = store.get_step_logs_by_workflow(workflow_id)
        for log in step_logs:
            counts[log.step_id] = counts.get(log.step_id, 0) + 1
    except Exception:
        pass
    return counts


def _runner_dict(runner) -> dict:
    return {
        "uuid": runner.uuid,
        "workflow_id": runner.workflow_id,
        "workflow_name": runner.workflow.name,
        "state": runner.state,
        "start_time": runner.start_time,
        "end_time": runner.end_time,
        "duration": runner.duration,
    }


def _step_dict(step) -> dict:
    d = {
        "id": step.id,
        "workflow_id": step.workflow_id,
        "object_type": step.object_type,
        "facet_name": step.facet_name,
        "state": step.state,
        "statement_id": step.statement_id,
        "statement_name": step.statement_name,
        "container_id": step.container_id,
        "block_id": step.block_id,
        "start_time": step.start_time,
        "last_modified": step.last_modified,
    }
    if step.attributes:
        d["params"] = {
            k: {"value": v.value, "type": v.type_hint} for k, v in step.attributes.params.items()
        }
        d["returns"] = {
            k: {"value": v.value, "type": v.type_hint} for k, v in step.attributes.returns.items()
        }
    return d


def _task_dict(task) -> dict:
    return {
        "uuid": task.uuid,
        "name": task.name,
        "state": task.state,
        "runner_id": task.runner_id,
        "workflow_id": task.workflow_id,
        "flow_id": task.flow_id,
        "step_id": task.step_id,
        "task_list_name": task.task_list_name,
        "data_type": task.data_type,
        "created": task.created,
        "updated": task.updated,
        "duration": task.updated - task.created if task.updated and task.created else 0,
    }
