"""Climate trends visualization routes."""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..dependencies import get_store

router = APIRouter(prefix="/climate-trends")


def _get_climate_db(store):
    """Return the raw MongoDB database from the store."""
    return store._db


def _strip_dates(doc: dict[str, Any]) -> dict[str, Any]:
    """Remove datetime fields that aren't JSON-serializable."""
    return {k: v for k, v in doc.items() if not isinstance(v, datetime.datetime)}


def _list_states(db) -> list[dict[str, str]]:
    """Return states that have climate trend data."""
    states = db["climate_trends"].distinct("state")
    return [{"code": s, "name": s} for s in sorted(states)]


def _get_yearly_data(db, state: str) -> list[dict[str, Any]]:
    """Get yearly climate summaries for a state."""
    docs = db["climate_state_years"].find({"state": state}, {"_id": 0}).sort("year", 1)
    return [_strip_dates(d) for d in docs]


def _get_trend(db, state: str) -> dict[str, Any]:
    """Get the climate trend document for a state."""
    doc = db["climate_trends"].find_one({"state": state}, {"_id": 0})
    return _strip_dates(doc) if doc else {}


@router.get("/")
def climate_trends_page(request: Request, store=Depends(get_store)):
    """Render the climate trends dashboard page."""
    db = _get_climate_db(store)
    states = _list_states(db)
    return request.app.state.templates.TemplateResponse(
        request,
        "climate/trends.html",
        {"states": states, "active_tab": "climate_trends"},
    )


@router.get("/api/states")
def climate_trends_states(store=Depends(get_store)):
    """JSON list of states with climate trend data."""
    db = _get_climate_db(store)
    states = _list_states(db)
    return JSONResponse(content={"states": states})


@router.get("/api/data")
def climate_trends_data(state: str = "", store=Depends(get_store)):
    """JSON climate data for a single state."""
    db = _get_climate_db(store)
    yearly = _get_yearly_data(db, state)
    trend = _get_trend(db, state)
    narrative = trend.get("narrative", "")
    return JSONResponse(
        content={
            "state": state,
            "yearly": yearly,
            "trend": trend,
            "narrative": narrative,
        }
    )


@router.get("/api/compare")
def climate_trends_compare(states: str = "", store=Depends(get_store)):
    """JSON multi-state comparison data."""
    db = _get_climate_db(store)
    state_list = [s.strip() for s in states.split(",") if s.strip()]
    result: dict[str, Any] = {}
    for state in state_list:
        yearly = _get_yearly_data(db, state)
        trend = _get_trend(db, state)
        result[state] = {"yearly": yearly, "trend": trend}
    return JSONResponse(content={"states": state_list, "data": result})
