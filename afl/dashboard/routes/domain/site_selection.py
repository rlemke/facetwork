"""Site-selection visualization routes."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ...dependencies import get_store

router = APIRouter(prefix="/site-selection")

# FIPS code -> state name for display purposes.
_FIPS_TO_STATE: dict[str, str] = {
    "01": "Alabama",
    "02": "Alaska",
    "04": "Arizona",
    "05": "Arkansas",
    "06": "California",
    "08": "Colorado",
    "09": "Connecticut",
    "10": "Delaware",
    "11": "District of Columbia",
    "12": "Florida",
    "13": "Georgia",
    "15": "Hawaii",
    "16": "Idaho",
    "17": "Illinois",
    "18": "Indiana",
    "19": "Iowa",
    "20": "Kansas",
    "21": "Kentucky",
    "22": "Louisiana",
    "23": "Maine",
    "24": "Maryland",
    "25": "Massachusetts",
    "26": "Michigan",
    "27": "Minnesota",
    "28": "Mississippi",
    "29": "Missouri",
    "30": "Montana",
    "31": "Nebraska",
    "32": "Nevada",
    "33": "New Hampshire",
    "34": "New Jersey",
    "35": "New Mexico",
    "36": "New York",
    "37": "North Carolina",
    "38": "North Dakota",
    "39": "Ohio",
    "40": "Oklahoma",
    "41": "Oregon",
    "42": "Pennsylvania",
    "44": "Rhode Island",
    "45": "South Carolina",
    "46": "South Dakota",
    "47": "Tennessee",
    "48": "Texas",
    "49": "Utah",
    "50": "Vermont",
    "51": "Virginia",
    "53": "Washington",
    "54": "West Virginia",
    "55": "Wisconsin",
    "56": "Wyoming",
}

_FIELD_LABELS: dict[str, str] = {
    "suitability_score": "Suitability Score",
    "demand_index": "Demand Index",
    "restaurant_count": "Restaurant Count",
    "restaurants_per_1000": "Restaurants per 1,000",
    "population": "Population",
    "median_income": "Median Income",
    "population_density_km2": "Pop. Density (per km\u00b2)",
    "pct_below_poverty": "Below Poverty %",
    "unemployment_rate": "Unemployment Rate",
    "pct_bachelors_plus": "Bachelor's Degree+ %",
    "pct_owner_occupied": "Owner-Occupied %",
    "labor_force_participation": "Labor Force Part. %",
}

_PREFERRED_FIELDS = [
    "suitability_score",
    "demand_index",
    "restaurant_count",
    "restaurants_per_1000",
    "population",
    "median_income",
    "population_density_km2",
    "pct_below_poverty",
    "unemployment_rate",
    "pct_bachelors_plus",
    "pct_owner_occupied",
    "labor_force_participation",
]

_POPUP_FIELDS = [
    "suitability_score",
    "demand_index",
    "restaurant_count",
    "restaurants_per_1000",
    "population",
    "median_income",
    "population_density_km2",
]

_SKIP_PREFIXES = ("B0", "B1", "B2", "B3")
_SKIP_FIELDS = {"ALAND", "AWATER", "CBSAFP", "CSAFP", "METDIVFP", "STATEFP", "COUNTYFP"}


def _get_field_label(field: str) -> str:
    """Return a human-readable label for a field name."""
    return _FIELD_LABELS.get(field, field)


def _filter_numeric_fields(sample_props: dict[str, Any]) -> list[str]:
    """Return ordered numeric field names suitable for the choropleth dropdown."""
    all_numeric = {k for k, v in sample_props.items() if isinstance(v, (int, float))}
    result: list[str] = []
    for key in _PREFERRED_FIELDS:
        if key in all_numeric:
            result.append(key)
            all_numeric.discard(key)
    for key in sorted(all_numeric):
        if key in _SKIP_FIELDS or any(key.startswith(p) for p in _SKIP_PREFIXES):
            continue
        result.append(key)
    return result


def _load_scored_geojson(db, state_fips: str) -> dict[str, Any]:
    """Load scored GeoJSON for a state from handler_output."""
    dataset_key = f"sitesel.scored.{state_fips}"
    docs = list(
        db.handler_output.find(
            {"dataset_key": dataset_key},
            {"_id": 0},
        )
    )
    features = []
    for doc in docs:
        geom = doc.get("geometry")
        props = doc.get("properties", {})
        if geom:
            features.append(
                {
                    "type": "Feature",
                    "properties": props,
                    "geometry": geom,
                }
            )
    return {"type": "FeatureCollection", "features": features}


def _list_scored_states(db) -> list[dict[str, Any]]:
    """List states that have scored data in the output store."""
    metas = list(
        db.handler_output_meta.find(
            {"dataset_key": {"$regex": "^sitesel\\.scored\\."}},
        ).sort("dataset_key", 1)
    )
    states = []
    for m in metas:
        m.pop("_id", None)
        dk = m.get("dataset_key", "")
        fips = dk.rsplit(".", 1)[-1] if "." in dk else ""
        states.append(
            {
                "state_fips": fips,
                "state_name": _FIPS_TO_STATE.get(fips, fips),
                "dataset_key": dk,
                "record_count": m.get("record_count", 0),
                "facet_name": m.get("facet_name", ""),
                "imported_at": m.get("imported_at"),
            }
        )
    return states


@router.get("/")
def site_selection_list(request: Request, store=Depends(get_store)):
    """List analyzed states with scored data."""
    db = store._db
    states = _list_scored_states(db)
    return request.app.state.templates.TemplateResponse(
        request,
        "site_selection/list.html",
        {"states": states, "active_tab": "site_selection"},
    )


@router.get("/{state_fips}")
def site_selection_map(request: Request, state_fips: str, store=Depends(get_store)):
    """Suitability choropleth map for one state."""
    db = store._db
    geojson = _load_scored_geojson(db, state_fips)
    features = geojson.get("features", [])

    numeric_fields: list[str] = []
    if features:
        sample = features[0].get("properties", {})
        numeric_fields = _filter_numeric_fields(sample)

    state_name = _FIPS_TO_STATE.get(state_fips, state_fips)

    return request.app.state.templates.TemplateResponse(
        request,
        "site_selection/map_view.html",
        {
            "state_fips": state_fips,
            "state_name": state_name,
            "feature_count": len(features),
            "numeric_fields": numeric_fields,
            "field_labels": _FIELD_LABELS,
            "popup_fields": _POPUP_FIELDS,
            "geojson_str": json.dumps(geojson),
            "active_tab": "site_selection",
        },
    )


@router.get("/{state_fips}/table")
def site_selection_table(request: Request, state_fips: str, store=Depends(get_store)):
    """Ranked table of counties by suitability score."""
    db = store._db
    geojson = _load_scored_geojson(db, state_fips)
    features = geojson.get("features", [])

    # Already sorted by score desc from scoring_builder
    rows = []
    for f in features:
        props = f.get("properties", {})
        rows.append(
            {
                "name": props.get("NAME", ""),
                "geoid": props.get("GEOID", ""),
                "suitability_score": props.get("suitability_score", 0),
                "demand_index": props.get("demand_index", 0),
                "restaurant_count": props.get("restaurant_count", 0),
                "restaurants_per_1000": props.get("restaurants_per_1000", 0),
                "population": props.get("population", 0),
                "median_income": props.get("median_income", 0),
            }
        )

    state_name = _FIPS_TO_STATE.get(state_fips, state_fips)

    return request.app.state.templates.TemplateResponse(
        request,
        "site_selection/table_view.html",
        {
            "state_fips": state_fips,
            "state_name": state_name,
            "rows": rows,
            "field_labels": _FIELD_LABELS,
            "active_tab": "site_selection",
        },
    )


@router.get("/api/{state_fips}")
def site_selection_geojson(state_fips: str, store=Depends(get_store)):
    """GeoJSON API endpoint for a state's scored data."""
    db = store._db
    geojson = _load_scored_geojson(db, state_fips)
    return JSONResponse(content=geojson)


@router.get("/api/{state_fips}/download")
def site_selection_download(state_fips: str, format: str = "geojson", store=Depends(get_store)):
    """Download scored data as GeoJSON or CSV."""
    db = store._db
    geojson = _load_scored_geojson(db, state_fips)
    features = geojson.get("features", [])
    state_name = _FIPS_TO_STATE.get(state_fips, state_fips)

    if format == "csv":
        buf = io.StringIO()
        if features:
            all_keys: set[str] = set()
            for f in features:
                all_keys.update(f.get("properties", {}).keys())
            ordered = []
            for k in ("GEOID", "NAME"):
                if k in all_keys:
                    ordered.append(k)
                    all_keys.discard(k)
            for k in _PREFERRED_FIELDS:
                if k in all_keys:
                    ordered.append(k)
                    all_keys.discard(k)
            ordered.extend(sorted(all_keys))

            writer = csv.DictWriter(buf, fieldnames=ordered)
            writer.writeheader()
            for f in features:
                writer.writerow({k: f.get("properties", {}).get(k, "") for k in ordered})
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={state_fips}_{state_name}_scored.csv"
            },
        )

    return Response(
        content=json.dumps(geojson, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename={state_fips}_{state_name}_scored.geojson"
        },
    )
