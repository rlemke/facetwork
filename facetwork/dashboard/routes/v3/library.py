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

"""v3 Library side — Catalog (reusable workflows/libraries) + Flows (compiled FFL)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ...dependencies import get_current_user, get_store

router = APIRouter(prefix="/v3")

_PURPOSES = ["production", "beta", "test", "personal", "none"]


@router.get("/catalog/{slug}")
def catalog_detail_v3(
    request: Request, slug: str, version: str | None = None, store=Depends(get_store)
):
    """Redesigned catalog entry detail — FFL source, params, versions, run."""
    from ..execution.catalog import _parse_version, _service

    svc = _service(store)
    d = svc.get(slug, _parse_version(version)) if svc else None
    default_inputs = {}
    if d:
        for p in d.get("param_schema", []) or []:
            default_inputs[p["name"]] = p.get("default")

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/catalog/detail.html",
        {
            "d": d,
            "slug": slug,
            "default_inputs_json": json.dumps(default_inputs, indent=2, default=str),
            "unavailable": svc is None,
            "active_nav": "catalog",
        },
    )


@router.get("/flows/{flow_id}/source")
def flow_source_v3(request: Request, flow_id: str, store=Depends(get_store)):
    """Flow FFL source on the v3 code view (one block per compiled source)."""
    flow = store.get_flow(flow_id)
    blocks = [
        {"name": s.name or "source.ffl", "content": s.content}
        for s in (flow.compiled_sources if flow else [])
    ]
    return request.app.state.templates.TemplateResponse(
        request,
        "v3/codeview.html",
        {
            "title": flow.name.name if flow else "Flow",
            "subtitle": "FFL source",
            "crumb": "Source",
            "back_href": f"/v3/flows/{flow_id}",
            "back_label": flow.name.name if flow else "Flow",
            "blocks": blocks,
            "error": None if flow else "Flow not found.",
            "active_nav": "library",
        },
    )


@router.get("/flows/{flow_id}/json")
def flow_json_v3(request: Request, flow_id: str, store=Depends(get_store)):
    """Flow compiled JSON (AST) on the v3 code view."""
    flow = store.get_flow(flow_id)
    out = None
    error = None if flow else "Flow not found."
    if flow:
        try:
            if flow.compiled_ast:
                out = json.dumps(flow.compiled_ast, indent=2)
            elif flow.compiled_sources:
                from facetwork.emitter import JSONEmitter
                from facetwork.parser import FFLParser

                ast = FFLParser().parse(flow.compiled_sources[0].content)
                out = JSONEmitter(indent=2).emit(ast)
            else:
                error = "No compiled AST or sources for this flow."
        except Exception as exc:  # pragma: no cover - defensive
            error = f"Failed to emit JSON: {exc}"
    return request.app.state.templates.TemplateResponse(
        request,
        "v3/codeview.html",
        {
            "title": flow.name.name if flow else "Flow",
            "subtitle": "compiled JSON (AST)",
            "crumb": "JSON",
            "back_href": f"/v3/flows/{flow_id}",
            "back_label": flow.name.name if flow else "Flow",
            "blocks": [{"name": "program.json", "content": out}] if out else [],
            "error": error,
            "active_nav": "library",
        },
    )


@router.get("/flows/{flow_id}/run/{workflow_id}")
def flow_run_form_v3(
    request: Request,
    flow_id: str,
    workflow_id: str,
    store=Depends(get_store),
    current_user=Depends(get_current_user),
):
    """Redesigned run form — per-param inputs + purpose/teams, then run."""
    from ..execution.flows import build_run_params

    flow = store.get_flow(flow_id)
    wf = store.get_workflow(workflow_id) if flow else None
    if not flow or not wf:
        return request.app.state.templates.TemplateResponse(
            request, "v3/flows/run.html", {"flow": None, "active_nav": "library"}
        )
    params, parse_error, workflow_doc = build_run_params(flow, wf.name)
    return request.app.state.templates.TemplateResponse(
        request,
        "v3/flows/run.html",
        {
            "flow": flow,
            "workflow_def": wf,
            "workflow_name": wf.name,
            "params": params,
            "parse_error": parse_error,
            "workflow_doc": workflow_doc,
            "purposes": _PURPOSES,
            "all_teams": [t.name for t in store.list_teams()],
            "active_nav": "library",
        },
    )


@router.post("/flows/{flow_id}/run/{workflow_id}")
def flow_run_execute_v3(
    flow_id: str,
    workflow_id: str,
    inputs_json: str = Form("{}"),
    purpose: str = Form("none"),
    teams: list[str] = Form(default=[]),
    store=Depends(get_store),
    current_user=Depends(get_current_user),
):
    """Submit the run (reusing the shared creator), then land on the v3 detail."""
    from ..execution.flows import create_flow_run

    flow = store.get_flow(flow_id)
    wf = store.get_workflow(workflow_id) if flow else None
    if not flow or not wf:
        return RedirectResponse(url="/v3/flows", status_code=303)
    runner_id = create_flow_run(flow, wf, inputs_json, purpose, teams, store, current_user)
    return RedirectResponse(url=f"/v3/workflows/{runner_id}", status_code=303)


@router.get("/flows/{flow_id}")
def flow_detail_v3(request: Request, flow_id: str, store=Depends(get_store)):
    """Redesigned flow detail — namespace-grouped workflows + recent runs."""
    flow = store.get_flow(flow_id)
    workflows = list(store.get_workflows_by_flow(flow_id)) if flow else []

    ns_groups: dict[str, list] = {}
    for wf in workflows:
        try:
            ns, _ = wf.name.rsplit(".", 1)
        except ValueError:
            ns = ""
        ns_groups.setdefault(ns, []).append(wf)
    namespace_list = sorted(
        (
            {"name": ns or "system.unnamespaced", "workflows": sorted(wfs, key=lambda w: w.name)}
            for ns, wfs in ns_groups.items()
        ),
        key=lambda x: str(x["name"]),
    )

    runners = []
    if flow:
        for wf in flow.workflows:
            runners.extend(store.get_runners_by_workflow(wf.uuid))
        runners.sort(key=lambda r: r.start_time, reverse=True)
        runners = runners[:10]

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/flows/detail.html",
        {
            "flow": flow,
            "namespace_list": namespace_list,
            "total_wf": len(workflows),
            "runners": runners,
            "active_nav": "library",
        },
    )


@router.get("/catalog")
def catalog_v3(request: Request, q: str = "", tab: str = "all", store=Depends(get_store)):
    """Redesigned Catalog — search + workflows/libraries split."""
    from ..execution.catalog import _service

    svc = _service(store)
    rows = []
    if svc is not None:
        rows = svc.search(q) if q else svc.list_all()

    workflows = [r for r in rows if r.get("kind") == "workflow"]
    libraries = [r for r in rows if r.get("kind") == "library"]
    counts = {"all": len(rows), "workflows": len(workflows), "libraries": len(libraries)}
    if tab == "workflows":
        shown = workflows
    elif tab == "libraries":
        shown = libraries
    else:
        shown = rows

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/catalog/list.html",
        {
            "entries": shown,
            "counts": counts,
            "tab": tab,
            "q": q,
            "unavailable": svc is None,
            "active_nav": "catalog",
        },
    )


@router.get("/flows")
def flows_v3(request: Request, q: str = "", store=Depends(get_store)):
    """Redesigned Flows — compiled FFL programs (namespaces/facets/workflows)."""
    from ...viewfilters import flow_matches, flows_active, read_filters

    flows = list(store.get_all_flows())
    # Drop auto-generated CLI submissions (path=cli:submit); keep seeded/authored.
    flows = [f for f in flows if getattr(f.name, "path", "") != "cli:submit"]
    total_before = len(flows)
    if q:
        flows = [f for f in flows if q.lower() in f.name.name.lower()]
    # Apply the persisted global filters.
    gf = read_filters(request)
    flows = [f for f in flows if flow_matches(f, gf)]
    flows.sort(key=lambda f: f.name.name.lower())

    rows = [
        {
            "uuid": f.uuid,
            "name": f.name.name,
            "path": getattr(f.name, "path", "") or "",
            "workflows": len(f.workflows or []),
            "facets": len(f.facets or []),
            "namespaces": len(f.namespaces or []),
            "created_at": getattr(f, "created_at", 0) or 0,
        }
        for f in flows
    ]

    active = flows_active(gf)
    return request.app.state.templates.TemplateResponse(
        request,
        "v3/flows/list.html",
        {
            "flows": rows,
            "q": q,
            "total": len(rows),
            "active_nav": "library",
            "filter_active": active,
            "filter_hidden": max(0, total_before - len(rows)) if active else 0,
        },
    )
