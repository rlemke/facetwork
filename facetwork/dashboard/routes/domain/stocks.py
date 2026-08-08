"""Stock-group tracker — domain dashboard app.

Read paths query the ``stocks_*`` collections directly (the same
``FW_EXAMPLES_DATABASE`` convention the climate/census apps use). Write paths do
NOT mutate those collections: they submit the corresponding FFL workflow through
:func:`create_flow_run`, so every start/refresh/close is a first-class run with a
step graph, logs, retries and an audit trail — exactly like a run launched from
the platform UI.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ...dependencies import get_current_user, get_store

router = APIRouter(prefix="/stocks")

GROUPS = "stock_groups"
POSITIONS = "stock_positions"
SNAPSHOTS = "stock_snapshots"
CANDIDATES = "stock_candidates"

WF_OPEN = "stocks.workflows.OpenStockGroup"
WF_SNAPSHOT = "stocks.workflows.SnapshotStockGroups"
WF_CLOSE = "stocks.workflows.CloseStockGroup"

UNIVERSES = [
    ("sp500", "S&P 500"),
    ("nasdaq100", "Nasdaq 100"),
    ("dow30", "Dow 30"),
    ("manual", "Manual (pinned tickers only)"),
]

# The eleven GICS sectors, with the everyday word people actually use. Offered
# as checkboxes so the ambiguous free-text cases ("services") never arise in the
# UI; the domain still resolves typed names for API and FFL callers.
SECTORS = [
    ("Information Technology", "Tech"),
    ("Health Care", "Medical / health"),
    ("Financials", "Financial"),
    ("Consumer Discretionary", "Consumer discretionary"),
    ("Consumer Staples", "Consumer staples"),
    ("Communication Services", "Communications / media"),
    ("Industrials", "Industrial"),
    ("Energy", "Energy"),
    ("Utilities", "Utilities"),
    ("Real Estate", "Real estate"),
    ("Materials", "Materials"),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _stocks_db(store):
    """The domain database, separate from the FFL runtime database."""
    db_name = os.environ.get("FW_EXAMPLES_DATABASE", "facetwork_examples")
    return store._db.client[db_name]


def _jsonable(value: Any) -> Any:
    """Recursively make a Mongo document JSON-serialisable.

    Datetimes become ISO strings rather than being dropped: the UI shows "opened"
    and "last snapshot" times, so discarding them would lose real information.
    """
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _find_workflow(store, qualified_name: str):
    """Locate a seeded workflow by qualified name. Returns ``(flow, workflow)``.

    ``FlowDefinition.workflows`` is empty on the objects ``get_all_flows``
    returns, so the workflows must be fetched per flow — relying on the embedded
    list makes every lookup miss and the app report "not seeded". The flow name
    is checked first so the common case is a single extra query.
    """
    flows = list(store.get_all_flows())
    namespace_root = qualified_name.split(".", 1)[0]
    # NEWEST first, then name match. Re-seeding can leave more than one flow with
    # the same name (it happened here: two `stocks` flows, and the older one had
    # no `sectors` parameter). Picking arbitrarily meant the run was built from a
    # stale AST and silently dropped parameters the caller had supplied -- the
    # button appeared to work and the setting just vanished.
    flows.sort(key=lambda f: (f.name.name != namespace_root, -(getattr(f, "created_at", 0) or 0)))
    for flow in flows:
        for wf in store.get_workflows_by_flow(flow.uuid):
            if wf.name == qualified_name:
                return flow, wf
    return None, None


def _submit(store, current_user, qualified_name: str, inputs: dict) -> str | None:
    """Submit a workflow run; returns the runner id, or ``None`` if not seeded."""
    from ..execution.flows import create_flow_run

    flow, wf = _find_workflow(store, qualified_name)
    if not flow or not wf:
        return None
    return create_flow_run(flow, wf, json.dumps(inputs), "none", [], store, current_user)


def _group_view(db, group: dict) -> dict:
    """Augment a group document with the derived numbers the list page shows."""
    out = _jsonable(dict(group))
    final = out.get("final") or {}
    # A group that has never been snapshotted has no current_* keys at all, so
    # every derived field is filled in here rather than defaulted in a template.
    out["display_value"] = (
        final.get("total_value")
        if out.get("status") == "closed"
        else out.get("current_value", out.get("initial_value"))
    )
    if out.get("status") == "closed":
        out["display_pl_pct"] = final.get("pl_pct")
        out["display_pl_abs"] = final.get("pl_abs")
        out["display_benchmark_pct"] = final.get("benchmark_pl_pct")
        out["alpha"] = final.get("alpha")
    else:
        out["display_pl_pct"] = out.get("current_pl_pct")
        out["display_pl_abs"] = out.get("current_pl_abs")
        latest = db[SNAPSHOTS].find_one(
            {"group_id": out["group_id"]},
            {"_id": 0, "benchmark_pl_pct": 1},
            sort=[("snapshot_date", -1)],
        )
        out["display_benchmark_pct"] = (latest or {}).get("benchmark_pl_pct")
        if out["display_pl_pct"] is not None and out["display_benchmark_pct"] is not None:
            out["alpha"] = round(out["display_pl_pct"] - out["display_benchmark_pct"], 4)
        else:
            out["alpha"] = None
    return out


def _days_left(group: dict) -> int | None:
    target = group.get("target_close")
    if not isinstance(target, datetime.datetime):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=datetime.UTC)
    delta = target - datetime.datetime.now(datetime.UTC)
    return delta.days


# --------------------------------------------------------------------------
# JSON APIs (declared before /{group_id} so they are not shadowed)
# --------------------------------------------------------------------------


@router.get("/api/groups")
def api_groups(status: str = "", store=Depends(get_store)):
    db = _stocks_db(store)
    query = {"status": status} if status else {}
    groups = list(db[GROUPS].find(query, {"_id": 0}).sort("created_at", -1).limit(200))
    return JSONResponse(content={"groups": [_group_view(db, g) for g in groups]})


@router.get("/api/groups/{group_id}")
def api_group(group_id: str, store=Depends(get_store)):
    db = _stocks_db(store)
    group = db[GROUPS].find_one({"group_id": group_id}, {"_id": 0})
    if not group:
        return JSONResponse(status_code=404, content={"error": "unknown group"})
    return JSONResponse(
        content={
            "group": _group_view(db, group),
            "positions": _jsonable(
                list(db[POSITIONS].find({"group_id": group_id}, {"_id": 0}).sort("rank", 1))
            ),
            "snapshots": _jsonable(
                list(
                    db[SNAPSHOTS].find({"group_id": group_id}, {"_id": 0}).sort("snapshot_date", 1)
                )
            ),
        }
    )


@router.get("/api/groups/{group_id}/series")
def api_group_series(group_id: str, store=Depends(get_store)):
    """The P/L time series the progress chart draws.

    Both series are percentages off the same baseline, so they share one axis —
    a dual-axis comparison here would be actively misleading.
    """
    db = _stocks_db(store)
    snaps = list(
        db[SNAPSHOTS]
        .find(
            {"group_id": group_id},
            {"_id": 0, "snapshot_date": 1, "pl_pct": 1, "total_value": 1, "benchmark_pl_pct": 1},
        )
        .sort("snapshot_date", 1)
    )
    group = db[GROUPS].find_one({"group_id": group_id}, {"_id": 0, "benchmark": 1, "name": 1})
    return JSONResponse(
        content={
            "group_id": group_id,
            "name": (group or {}).get("name", ""),
            "benchmark": (group or {}).get("benchmark", "SPY"),
            "dates": [s["snapshot_date"] for s in snaps],
            # `+ 0.0` normalises IEEE negative zero, which would otherwise
            # render as "-0.00%".
            "pl_pct": [round((s.get("pl_pct") or 0.0) * 100, 4) + 0.0 for s in snaps],
            "benchmark_pct": [
                None
                if s.get("benchmark_pl_pct") is None
                else round(s["benchmark_pl_pct"] * 100, 4) + 0.0
                for s in snaps
            ],
            "total_value": [s.get("total_value") for s in snaps],
        }
    )


@router.get("/api/groups/{group_id}/candidates")
def api_group_candidates(group_id: str, limit: int = 100, store=Depends(get_store)):
    """The full ranked candidate list — including the names that were not picked."""
    db = _stocks_db(store)
    rows = list(
        db[CANDIDATES].find({"group_id": group_id}, {"_id": 0}).sort("rank", 1).limit(limit)
    )
    return JSONResponse(content={"candidates": _jsonable(rows)})


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


@router.get("/")
def stocks_index(request: Request, store=Depends(get_store)):
    db = _stocks_db(store)
    groups = [
        _group_view(db, g)
        for g in db[GROUPS].find({}, {"_id": 0}).sort("created_at", -1).limit(200)
    ]
    open_groups = [g for g in groups if g.get("status") == "open"]
    closed_groups = [g for g in groups if g.get("status") != "open"]

    beat = sum(1 for g in closed_groups if (g.get("alpha") or 0) > 0)
    return request.app.state.templates.TemplateResponse(
        request,
        "stocks/index.html",
        {
            "open_groups": open_groups,
            "closed_groups": closed_groups,
            "closed_count": len(closed_groups),
            "beat_benchmark": beat,
            "workflow_ready": _find_workflow(store, WF_OPEN)[0] is not None,
            "active_tab": "stocks",
            "active_app": "Stocks",
        },
    )


@router.get("/new")
def stocks_new(request: Request, store=Depends(get_store)):
    return request.app.state.templates.TemplateResponse(
        request,
        "stocks/new.html",
        {
            "universes": UNIVERSES,
            "sectors": SECTORS,
            "workflow_ready": _find_workflow(store, WF_OPEN)[0] is not None,
            "active_tab": "stocks",
            "active_app": "Stocks",
        },
    )


@router.get("/{group_id}")
def stocks_detail(request: Request, group_id: str, store=Depends(get_store)):
    db = _stocks_db(store)
    group = db[GROUPS].find_one({"group_id": group_id}, {"_id": 0})
    if not group:
        return RedirectResponse(url="/stocks/", status_code=303)

    view = _group_view(db, group)
    positions = _jsonable(
        list(db[POSITIONS].find({"group_id": group_id}, {"_id": 0}).sort("rank", 1))
    )
    # Biggest contributors first — that is the question the table answers.
    positions.sort(key=lambda p: -(p.get("pl_abs") or 0.0))
    snapshots = list(
        db[SNAPSHOTS].find({"group_id": group_id}, {"_id": 0}).sort("snapshot_date", 1)
    )

    return request.app.state.templates.TemplateResponse(
        request,
        "stocks/detail.html",
        {
            "group": view,
            "positions": positions,
            "snapshot_count": len(snapshots),
            "days_left": _days_left(group),
            "is_open": group.get("status") == "open",
            "active_tab": "stocks",
            "active_app": "Stocks",
        },
    )


# --------------------------------------------------------------------------
# Actions — each submits an FFL workflow run
# --------------------------------------------------------------------------


@router.post("/groups")
def stocks_create_group(
    name: str = Form("Untitled group"),
    universe: str = Form("sp500"),
    size: int = Form(10),
    capital: float = Form(100000.0),
    horizon_days: int = Form(7),
    benchmark: str = Form("SPY"),
    pinned: str = Form(""),
    use_llm: str = Form(""),
    sectors: list[str] = Form(default=[]),
    max_per_sector: int = Form(0),
    store=Depends(get_store),
    current_user=Depends(get_current_user),
):
    """START — submit OpenStockGroup and land on its run page."""
    runner_id = _submit(
        store,
        current_user,
        WF_OPEN,
        {
            "name": name.strip() or "Untitled group",
            "universe": universe,
            "size": max(1, int(size)),
            "capital": float(capital),
            "horizon_days": max(1, int(horizon_days)),
            "benchmark": (benchmark or "SPY").strip().upper(),
            "pinned": pinned.strip(),
            "use_llm": bool(use_llm),
            # Checkbox values are already canonical GICS names, so the domain's
            # alias resolution is a no-op here and the ambiguous free-text cases
            # ("services") cannot arise from the UI at all.
            "sectors": ", ".join(s for s in sectors if s),
            "max_per_sector": max(0, int(max_per_sector)),
        },
    )
    if runner_id is None:
        return RedirectResponse(url="/stocks/?error=not_seeded", status_code=303)
    return RedirectResponse(url=f"/v3/workflows/{runner_id}", status_code=303)


@router.post("/{group_id}/snapshot")
def stocks_snapshot(
    group_id: str, store=Depends(get_store), current_user=Depends(get_current_user)
):
    """REFRESH NOW — submit a snapshot run for this group."""
    runner_id = _submit(store, current_user, WF_SNAPSHOT, {"group_id": group_id})
    if runner_id is None:
        return RedirectResponse(url=f"/stocks/{group_id}?error=not_seeded", status_code=303)
    return RedirectResponse(url=f"/v3/workflows/{runner_id}", status_code=303)


@router.post("/{group_id}/close")
def stocks_close(group_id: str, store=Depends(get_store), current_user=Depends(get_current_user)):
    """END/RECORD — final mark, realised P/L, group closed."""
    runner_id = _submit(store, current_user, WF_CLOSE, {"group_id": group_id})
    if runner_id is None:
        return RedirectResponse(url=f"/stocks/{group_id}?error=not_seeded", status_code=303)
    return RedirectResponse(url=f"/v3/workflows/{runner_id}", status_code=303)
