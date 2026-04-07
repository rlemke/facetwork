"""Climate trends visualization routes."""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...dependencies import get_store

router = APIRouter(prefix="/climate-trends")

_STATE_NAMES: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}


def _get_climate_db(store):
    """Return the examples MongoDB database for climate data.

    Climate data lives in ``AFL_EXAMPLES_DATABASE`` (default ``afl_examples``),
    separate from the AFL runtime database.
    """
    import os

    db_name = os.environ.get("AFL_EXAMPLES_DATABASE", "afl_examples")
    return store._db.client[db_name]


def _strip_dates(doc: dict[str, Any]) -> dict[str, Any]:
    """Remove datetime fields that aren't JSON-serializable."""
    return {k: v for k, v in doc.items() if not isinstance(v, datetime.datetime)}


def _list_states(db) -> list[dict[str, str]]:
    """Return states that have climate trend data."""
    states = db["climate_trends"].distinct("state")
    return [{"code": s, "name": _STATE_NAMES.get(s, s)} for s in sorted(states)]


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
