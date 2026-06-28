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

"""v3 redesigned workflow-detail view, wired to real runner data."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from facetwork.runtime.persistence import PersistenceAPI  # noqa: F401

# Runner states in which the graph is still changing and worth polling.
_ACTIVE_STATES = {"created", "running", "paused"}

from ...dependencies import get_store
from ...graph import compute_dag_layout
from ...helpers import (
    categorize_step_state,
    group_runners_by_namespace,
    qualify_step_names,
)

router = APIRouter(prefix="/v3")

# state → pill colour var, shared by the list rows and detail header
_PILL = {
    "running": "var(--st-running)",
    "created": "var(--st-running)",
    "paused": "var(--st-paused)",
    "completed": "var(--st-complete)",
    "failed": "var(--st-error)",
    "cancelled": "var(--st-error)",
}


@router.get("/workflows")
def workflow_list_v3(
    request: Request,
    tab: str = "running",
    store=Depends(get_store),
):
    """Redesigned Runs list — same data pipeline as ``/v2/workflows``."""
    # Reuse the v2 helpers so the two lists never diverge on filtering/counting.
    from ...viewdata import (
        _count_by_tab,
        _enrich_runners_with_progress,
        _filter_runners,
    )
    from ...viewfilters import read_filters, runner_matches, workflows_active

    all_runners = store.get_all_runners(limit=1000)
    # Narrow the universe by the persisted global filters, then tab on top.
    gf = read_filters(request)
    all_runners = [r for r in all_runners if runner_matches(r, gf)]
    tab_counts = _count_by_tab(all_runners)
    filtered = _filter_runners(all_runners, tab)
    groups = group_runners_by_namespace(filtered)
    progress = _enrich_runners_with_progress(filtered, store)

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/workflows/list.html",
        {
            "groups": groups,
            "tab": tab,
            "tab_counts": tab_counts,
            "progress": progress,
            "pill": _PILL,
            "active_nav": "runs",
            "filter_active": workflows_active(gf),
        },
    )


@router.get("/workflows/new")
def workflow_new_v3(request: Request, store=Depends(get_store)):
    """Redesigned new-run browser — workflows grouped by namespace.

    Declared BEFORE /workflows/{runner_id} so the static 'new' path wins.
    Each workflow links to the existing run form (/flows/{flow}/run/{wf}).
    """
    from ..execution.workflows import _build_afl_snippet, _collect_workflows_with_ns

    ns_map: dict[str, list[dict]] = {}
    for flow in store.get_all_flows():
        if not flow.compiled_ast:
            continue
        wf_records = store.get_workflows_by_flow(flow.uuid)
        name_to_uuid = {wr.name: wr.uuid for wr in wf_records}
        entries: list[dict] = []
        _collect_workflows_with_ns(flow.compiled_ast, "", entries)
        for entry in entries:
            ns, wf = entry["ns"], entry["wf"]
            qualified = f"{ns}.{wf['name']}" if ns else wf["name"]
            wf_uuid = name_to_uuid.get(qualified, "")
            item = {
                "ns": ns or "system.unnamespaced",
                "name": wf.get("name", ""),
                "qualified_name": qualified,
                "afl_source": _build_afl_snippet(ns, wf),
                "run_url": f"/v3/flows/{flow.uuid}/run/{wf_uuid}" if wf_uuid else "",
            }
            ns_map.setdefault(item["ns"], []).append(item)

    ns_groups = sorted(
        (
            {"name": ns, "workflows": sorted(wfs, key=lambda w: w["name"])}
            for ns, wfs in ns_map.items()
        ),
        key=lambda g: str(g["name"]),
    )
    total = sum(len(g["workflows"]) for g in ns_groups)

    return request.app.state.templates.TemplateResponse(
        request,
        "v3/workflows/new.html",
        {"ns_groups": ns_groups, "total": total, "active_nav": "runs"},
    )


def _short_state(state: str) -> str:
    """Last dotted segment of a step state, e.g. ``EventTransmit``."""
    if not state:
        return ""
    return state.rsplit(".", 1)[-1]


def _build_nodes(dag, step_by_id: dict, server_of: dict, now_ms: int) -> list[dict]:
    """Enrich the DAG layout nodes with the per-step detail the inspector needs.

    ``compute_dag_layout`` only carries (label, state, x, y); the inspector
    panel wants the qualified name, facet, server, age and any error, so we
    join each laid-out node back to its ``StepDefinition`` here.
    """
    nodes: list[dict] = []
    for n in dag.nodes:
        step = step_by_id.get(n.step_id)
        cat = categorize_step_state(n.state)
        display = (getattr(step, "display_name", "") if step else "") or n.label
        facet = getattr(step, "facet_name", "") if step else ""
        start = getattr(step, "start_time", 0) if step else 0
        age_s = ((now_ms - start) / 1000) if start else 0
        error = getattr(step, "error", None) if step else None
        server = server_of.get(n.step_id, "")

        # one-line status sub-label shown under the node title
        if cat == "complete":
            sub = "complete"
        elif cat == "error":
            sub = str(error)[:24] if error else "error"
        elif cat == "running":
            sub = f"{_short_state(n.state)}" + (f" · {server}" if server else "")
        else:
            sub = _short_state(n.state).lower() or "pending"

        nodes.append(
            {
                "id": n.step_id,
                "x": n.x,
                "y": n.y,
                "w": n.w,
                "h": n.h,
                "label": display,
                "facet": facet,
                "cat": cat,
                "state": _short_state(n.state),
                "sub": sub,
                "server": server,
                "age_s": round(age_s),
                "error": (str(error) if error else ""),
            }
        )
    return nodes


def _compute_graph(runner, store) -> dict[str, Any]:
    """Compute the live graph payload — nodes, edges, counts — for a runner.

    Shared by the full-page render and the ``/graph`` polling endpoint so the
    initial paint and every subsequent update come from the exact same shape.
    """
    all_steps = list(store.get_steps_by_workflow(runner.workflow_id))
    qualify_step_names(all_steps)

    step_counts = {"running": 0, "error": 0, "complete": 0, "other": 0}
    for s in all_steps:
        step_counts[categorize_step_state(s.state)] += 1

    step_by_id = {s.id: s for s in all_steps}

    # step_id -> server name, and the set of distinct servers in play
    server_of: dict[str, str] = {}
    server_names: dict[str, str] = {}
    distinct_servers: set[str] = set()
    for t in store.get_tasks_by_workflow(runner.workflow_id):
        sid = getattr(t, "server_id", None)
        if not sid:
            continue
        distinct_servers.add(sid)
        if sid not in server_names:
            srv = store.get_server(sid)
            server_names[sid] = srv.server_name if srv else sid[:8]
        if getattr(t, "step_id", None):
            server_of[t.step_id] = server_names[sid]

    now_ms = int(time.time() * 1000)
    dag = compute_dag_layout(all_steps)
    nodes = _build_nodes(dag, step_by_id, server_of, now_ms) if dag else []

    # edges: keyed by source/target so the client can patch them in place;
    # marked "live" when the target node is currently running
    node_cat = {n["id"]: n["cat"] for n in nodes}
    edges: list[dict] = []
    if dag:
        for e in dag.edges:
            edges.append(
                {
                    "s": e.source_id,
                    "t": e.target_id,
                    "x1": e.x1,
                    "y1": e.y1,
                    "x2": e.x2,
                    "y2": e.y2,
                    "live": node_cat.get(e.target_id) == "running",
                }
            )

    total = sum(step_counts.values())
    pct = round(step_counts["complete"] * 100 / total) if total else 0

    return {
        "nodes": nodes,
        "edges": edges,
        "dag_w": dag.width if dag else 0,
        "dag_h": dag.height if dag else 0,
        "step_counts": step_counts,
        "total": total,
        "pct": pct,
        "server_count": len(distinct_servers),
        "now_ms": now_ms,
    }


@router.get("/workflows/{runner_id}")
def workflow_detail_v3(
    runner_id: str,
    request: Request,
    store=Depends(get_store),
):
    """Redesigned workflow detail — same data as ``/v2/workflows/{id}``."""
    runner = store.get_runner(runner_id)
    if not runner:
        return request.app.state.templates.TemplateResponse(
            request, "v3/workflows/detail.html", {"runner": None, "active_nav": "runs"}
        )

    graph = _compute_graph(runner, store)
    nodes = graph["nodes"]

    # default inspector selection: a running node, else an errored one, else first
    selected = next(
        (n for n in nodes if n["cat"] == "running"),
        next((n for n in nodes if n["cat"] == "error"), nodes[0] if nodes else None),
    )

    # Surface a viewable HTML output (e.g. a rendered map) straight on the run
    # header, so you don't have to drill into the step that produced it.
    from ..monitoring.output import artifact_url

    run_artifact = None
    for s in store.get_steps_by_workflow(runner.workflow_id):
        attrs = getattr(s, "attributes", None)
        if not (attrs and attrs.returns):
            continue
        for _n, attr in attrs.returns.items():
            val = attr.get("value") if isinstance(attr, dict) else getattr(attr, "value", None)
            u = artifact_url(val)
            if u:
                run_artifact = u
                break
        if run_artifact:
            break

    ctx: dict[str, Any] = {
        "runner": runner,
        "selected": selected,
        "active": runner.state in _ACTIVE_STATES,
        "active_nav": "runs",
        "artifact_url": run_artifact,
        **graph,
    }
    return request.app.state.templates.TemplateResponse(request, "v3/workflows/detail.html", ctx)


@router.get("/workflows/{runner_id}/source")
def workflow_source_v3(runner_id: str, request: Request, store=Depends(get_store)):
    """Reconstructed FFL source for a run, on the v3 code view."""
    from facetwork.workflow_source import (
        WorkflowNotFoundError,
        reconstruct_workflow_source,
    )

    runner = store.get_runner(runner_id)
    source = None
    error = None
    if runner is None:
        error = "Workflow run not found."
    elif not runner.compiled_ast:
        error = "No compiled AST was snapshotted for this run — source can't be reconstructed."
    else:
        try:
            source = reconstruct_workflow_source(runner.compiled_ast, runner.workflow.name)
        except WorkflowNotFoundError as exc:
            error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            error = f"Failed to reconstruct source: {exc}"

    short = runner.workflow.name.rsplit(".", 1)[-1] if runner else ""
    return request.app.state.templates.TemplateResponse(
        request,
        "v3/codeview.html",
        {
            "title": short,
            "subtitle": (runner.workflow.name + " · reconstructed FFL") if runner else "",
            "crumb": "Source",
            "back_href": f"/v3/workflows/{runner_id}",
            "back_label": short or "Run",
            "blocks": [{"name": runner.workflow.name, "content": source}] if source else [],
            "error": error,
            "active_nav": "runs",
        },
    )


@router.get("/workflows/{runner_id}/graph")
def workflow_graph_v3(
    runner_id: str,
    store=Depends(get_store),
):
    """JSON snapshot of the current graph + run state, for live polling.

    The client patches the SVG and header in place from this so node colours
    animate rather than blinking, and the inspector selection / scroll survive.
    """
    runner = store.get_runner(runner_id)
    if not runner:
        return JSONResponse(status_code=404, content={"error": "not_found"})

    graph = _compute_graph(runner, store)
    graph["state"] = runner.state
    graph["active"] = runner.state in _ACTIVE_STATES
    graph["duration_ms"] = runner.duration
    return JSONResponse(content=graph)
