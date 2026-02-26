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

"""Census data map visualization routes."""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..dependencies import get_store

router = APIRouter(prefix="/census")

# FIPS code → state name for display purposes.
_FIPS_TO_STATE: dict[str, str] = {
    "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas",
    "06": "California", "08": "Colorado", "09": "Connecticut", "10": "Delaware",
    "11": "District of Columbia", "12": "Florida", "13": "Georgia", "15": "Hawaii",
    "16": "Idaho", "17": "Illinois", "18": "Indiana", "19": "Iowa",
    "20": "Kansas", "21": "Kentucky", "22": "Louisiana", "23": "Maine",
    "24": "Maryland", "25": "Massachusetts", "26": "Michigan", "27": "Minnesota",
    "28": "Mississippi", "29": "Missouri", "30": "Montana", "31": "Nebraska",
    "32": "Nevada", "33": "New Hampshire", "34": "New Jersey", "35": "New Mexico",
    "36": "New York", "37": "North Carolina", "38": "North Dakota", "39": "Ohio",
    "40": "Oklahoma", "41": "Oregon", "42": "Pennsylvania", "44": "Rhode Island",
    "45": "South Carolina", "46": "South Dakota", "47": "Tennessee", "48": "Texas",
    "49": "Utah", "50": "Vermont", "51": "Virginia", "53": "Washington",
    "54": "West Virginia", "55": "Wisconsin", "56": "Wyoming",
}


def _region_label(dataset_key: str) -> str:
    """Extract a human-readable region name from a dataset key.

    Keys like ``census.tiger.county.01`` or ``census.joined.01`` have the
    state FIPS as the last dotted segment.
    """
    suffix = dataset_key.rsplit(".", 1)[-1] if "." in dataset_key else ""
    return _FIPS_TO_STATE.get(suffix, "")


# Preferred choropleth fields and skip lists (shared by single + combined views).
_PREFERRED_FIELDS = [
    "population", "population_density", "population_density_km2", "median_income",
    "housing_units", "total_households", "family_households", "nonfamily_households",
    "pct_owner_occupied", "pct_renter_occupied",
    "pct_no_vehicle", "pct_drove_alone", "pct_public_transit", "pct_walk", "pct_work_from_home",
    "pct_under_18", "pct_18_34", "pct_35_64", "pct_65_plus",
    "pct_white", "pct_black", "pct_asian", "pct_below_poverty", "unemployment_rate",
    "labor_force_participation", "pct_bachelors_plus", "vehicles_per_household",
]
_FIELD_LABELS: dict[str, str] = {
    "population": "Population",
    "population_density": "Pop. Density",
    "population_density_km2": "Pop. Density (per km\u00b2)",
    "median_income": "Median Income",
    "housing_units": "Housing Units",
    "total_households": "Total Households",
    "family_households": "Family Households",
    "nonfamily_households": "Non-family Households",
    "pct_owner_occupied": "Owner-Occupied (%)",
    "pct_renter_occupied": "Renter-Occupied (%)",
    "pct_no_vehicle": "No Vehicle (%)",
    "pct_drove_alone": "Drove Alone (%)",
    "pct_public_transit": "Public Transit (%)",
    "pct_walk": "Walk (%)",
    "pct_work_from_home": "Work from Home (%)",
    "pct_under_18": "Under 18 (%)",
    "pct_18_34": "Age 18-34 (%)",
    "pct_35_64": "Age 35-64 (%)",
    "pct_65_plus": "Age 65+ (%)",
    "pct_white": "White (%)",
    "pct_black": "Black (%)",
    "pct_asian": "Asian (%)",
    "pct_below_poverty": "Below Poverty (%)",
    "unemployment_rate": "Unemployment Rate (%)",
    "labor_force_participation": "Labor Force Part. (%)",
    "pct_bachelors_plus": "Bachelor's+ (%)",
    "vehicles_per_household": "Vehicles per Household",
    # State-level aggregates
    "total_population": "Total Population",
    "total_housing_units": "Total Housing Units",
    "weighted_median_income": "Median Income (weighted)",
}


def _get_field_label(field: str) -> str:
    """Return a human-readable label for a field name."""
    return _FIELD_LABELS.get(field, field)


_POPUP_FIELDS = [
    "population", "median_income", "population_density_km2",
    "pct_below_poverty", "unemployment_rate", "pct_owner_occupied",
    "pct_white", "pct_bachelors_plus",
]

_SKIP_PREFIXES = ("B0", "B1", "B2", "B3")  # raw ACS variable codes
_SKIP_FIELDS = {"ALAND", "AWATER", "CBSAFP", "CSAFP", "METDIVFP", "STATEFP", "COUNTYFP"}

# Properties to strip from the combined national view for size reduction.
_STRIP_PROPS = {
    "AWATER", "CBSAFP", "CSAFP", "METDIVFP", "COUNTYNS", "GEOIDFQ",
    "CLASSFP", "FUNCSTAT", "MTFCC", "LSAD", "INTPTLAT", "INTPTLON", "COUNTYFP",
}
_STRIP_RE = re.compile(r"^B[0-3]\d")


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


def _decimate_ring(ring: list, max_points: int = 80) -> list:
    """Reduce a coordinate ring to at most *max_points* by uniform sampling."""
    if len(ring) <= max_points:
        return ring
    step = max(1, len(ring) // max_points)
    result = ring[::step]
    if result[-1] != ring[-1]:
        result.append(ring[-1])
    return result


def _simplify_geometry(geom: dict[str, Any], max_points: int = 80) -> dict[str, Any]:
    """Decimate polygon coordinates for the national overview."""
    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])
    if gtype == "Polygon":
        return {"type": gtype, "coordinates": [_decimate_ring(r, max_points) for r in coords]}
    if gtype == "MultiPolygon":
        return {"type": gtype, "coordinates": [[_decimate_ring(r, max_points) for r in poly] for poly in coords]}
    return geom


def _slim_properties(props: dict[str, Any]) -> dict[str, Any]:
    """Strip raw ACS codes and verbose TIGER fields for the combined view."""
    return {k: v for k, v in props.items()
            if k not in _STRIP_PROPS and not _STRIP_RE.match(k)}


def _compute_stats(features: list[dict[str, Any]], numeric_fields: list[str]) -> dict[str, dict[str, float]]:
    """Compute min/max/mean/median for numeric fields. No numpy — sorts for median."""
    stats: dict[str, dict[str, float]] = {}
    for field in numeric_fields:
        values = []
        for f in features:
            v = f.get("properties", {}).get(field)
            if isinstance(v, (int, float)):
                values.append(float(v))
        if not values:
            continue
        values.sort()
        n = len(values)
        if n % 2 == 0:
            median = (values[n // 2 - 1] + values[n // 2]) / 2
        else:
            median = values[n // 2]
        stats[field] = {
            "min": values[0],
            "max": values[-1],
            "mean": round(sum(values) / n, 2),
            "median": round(median, 2),
        }
    return stats


def _aggregate_state_stats(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group features by STATEFP, compute per-state aggregates.

    Returns list of dicts sorted by state_fips:
    - state_fips, state_name, county_count
    - total_population (sum), total_housing_units (sum)
    - weighted_median_income (population-weighted avg)
    - population_density (total_pop / total_area_km2)
    """
    buckets: dict[str, dict[str, Any]] = {}
    for f in features:
        props = f.get("properties", {})
        fips = str(props.get("STATEFP", ""))
        if not fips:
            continue
        if fips not in buckets:
            buckets[fips] = {
                "state_fips": fips,
                "state_name": _FIPS_TO_STATE.get(fips, ""),
                "county_count": 0,
                "total_population": 0,
                "total_housing_units": 0,
                "income_weighted_sum": 0.0,
                "income_pop_sum": 0,
                "total_aland": 0.0,
            }
        b = buckets[fips]
        b["county_count"] += 1
        pop = props.get("population", 0) or 0
        try:
            pop = float(pop)
        except (ValueError, TypeError):
            pop = 0.0
        b["total_population"] += pop
        hu = props.get("housing_units", 0) or 0
        try:
            hu = float(hu)
        except (ValueError, TypeError):
            hu = 0.0
        b["total_housing_units"] += hu
        inc = props.get("median_income", 0) or 0
        try:
            inc = float(inc)
        except (ValueError, TypeError):
            inc = 0.0
        if inc > 0 and pop > 0:
            b["income_weighted_sum"] += inc * pop
            b["income_pop_sum"] += pop
        aland = props.get("ALAND", 0) or 0
        try:
            aland = float(aland)
        except (ValueError, TypeError):
            aland = 0.0
        b["total_aland"] += aland

    result: list[dict[str, Any]] = []
    for fips in sorted(buckets):
        b = buckets[fips]
        total_area_km2 = b["total_aland"] / 1e6 if b["total_aland"] > 0 else 0
        result.append({
            "state_fips": b["state_fips"],
            "state_name": b["state_name"],
            "county_count": b["county_count"],
            "total_population": round(b["total_population"]),
            "total_housing_units": round(b["total_housing_units"]),
            "weighted_median_income": (
                round(b["income_weighted_sum"] / b["income_pop_sum"], 2)
                if b["income_pop_sum"] > 0 else 0.0
            ),
            "population_density": (
                round(b["total_population"] / total_area_km2, 2)
                if total_area_km2 > 0 else 0.0
            ),
        })
    return result


def _build_comparison(
    left_stats: dict[str, dict[str, float]],
    right_stats: dict[str, dict[str, float]],
    fields: list[str],
) -> list[dict[str, Any]]:
    """Build comparison rows: field, left_value, right_value, difference."""
    rows: list[dict[str, Any]] = []
    for field in fields:
        l_data = left_stats.get(field, {})
        r_data = right_stats.get(field, {})
        l_val = l_data.get("mean", 0.0)
        r_val = r_data.get("mean", 0.0)
        rows.append({
            "field": field,
            "left": round(l_val, 2),
            "right": round(r_val, 2),
            "difference": round(r_val - l_val, 2),
        })
    return rows


def _features_to_csv(features: list[dict[str, Any]]) -> str:
    """Flatten GeoJSON features to CSV. Columns: GEOID, NAME, then preferred, then rest alpha."""
    if not features:
        return ""
    # Collect all property keys across features
    all_keys: set[str] = set()
    for f in features:
        all_keys.update(f.get("properties", {}).keys())
    # Order: GEOID, NAME first, then preferred, then rest alphabetically
    ordered: list[str] = []
    for k in ("GEOID", "NAME"):
        if k in all_keys:
            ordered.append(k)
            all_keys.discard(k)
    for k in _PREFERRED_FIELDS:
        if k in all_keys:
            ordered.append(k)
            all_keys.discard(k)
    ordered.extend(sorted(all_keys))

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ordered)
    writer.writeheader()
    for f in features:
        writer.writerow({k: f.get("properties", {}).get(k, "") for k in ordered})
    return buf.getvalue()


@router.get("/maps")
def census_map_list(request: Request, store=Depends(get_store)):
    """List handler_output_meta entries that have GeoJSON geometry."""
    db = store._db
    metas = list(
        db.handler_output_meta.find(
            {"data_type": "geojson_feature"},
        ).sort("dataset_key", 1)
    )
    for m in metas:
        m.pop("_id", None)
        m["region"] = _region_label(m.get("dataset_key", ""))

    return request.app.state.templates.TemplateResponse(
        request,
        "census/maps.html",
        {"datasets": metas, "active_tab": "census_maps"},
    )


@router.get("/maps/_all")
def census_map_all(
    request: Request,
    store=Depends(get_store),
    prefix: str = "census.joined.",
):
    """Render a combined national map of all joined county datasets."""
    db = store._db
    dataset_keys = sorted(
        k for k in db.handler_output.distinct("dataset_key")
        if k.startswith(prefix)
    )
    # Count features and detect numeric fields from first non-empty dataset.
    total_features = 0
    numeric_fields: list[str] = []
    for dk in dataset_keys:
        c = db.handler_output.count_documents({"dataset_key": dk, "geometry": {"$exists": True}})
        total_features += c
        if c > 0 and not numeric_fields:
            sample_doc = db.handler_output.find_one({"dataset_key": dk, "geometry": {"$exists": True}})
            if sample_doc:
                numeric_fields = _filter_numeric_fields(
                    _slim_properties(sample_doc.get("properties", {}))
                )

    return request.app.state.templates.TemplateResponse(
        request,
        "census/map_all.html",
        {
            "dataset_count": len(dataset_keys),
            "feature_count": total_features,
            "numeric_fields": numeric_fields,
            "field_labels": _FIELD_LABELS,
            "popup_fields": _POPUP_FIELDS,
            "active_tab": "census_maps",
        },
    )


@router.get("/api/maps/_all")
def census_map_all_api(
    store=Depends(get_store),
    prefix: str = "census.joined.",
):
    """Return simplified GeoJSON for all joined datasets combined."""
    db = store._db
    dataset_keys = sorted(
        k for k in db.handler_output.distinct("dataset_key")
        if k.startswith(prefix)
    )

    features: list[dict[str, Any]] = []
    for dk in dataset_keys:
        docs = db.handler_output.find({"dataset_key": dk, "geometry": {"$exists": True}})
        state_name = _region_label(dk)
        for doc in docs:
            geom = _simplify_geometry(doc["geometry"])
            props = _slim_properties(doc.get("properties", {}))
            props["_state"] = state_name
            features.append({"type": "Feature", "geometry": geom, "properties": props})

    return JSONResponse({"type": "FeatureCollection", "features": features})


@router.get("/api/maps/_all/download")
def census_map_all_download(
    format: str = "geojson",
    store=Depends(get_store),
    prefix: str = "census.joined.",
) -> Response:
    """Download combined national data as GeoJSON or CSV."""
    if format not in ("geojson", "csv"):
        return JSONResponse({"error": f"Unsupported format: {format}"}, status_code=400)

    db = store._db
    dataset_keys = sorted(
        k for k in db.handler_output.distinct("dataset_key")
        if k.startswith(prefix)
    )

    features: list[dict[str, Any]] = []
    for dk in dataset_keys:
        docs = db.handler_output.find({"dataset_key": dk, "geometry": {"$exists": True}})
        state_name = _region_label(dk)
        for doc in docs:
            props = _slim_properties(doc.get("properties", {}))
            props["_state"] = state_name
            features.append({"type": "Feature", "geometry": doc["geometry"], "properties": props})

    if format == "csv":
        body = _features_to_csv(features)
        return Response(
            content=body,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=census_all_counties.csv"},
        )

    geojson = {"type": "FeatureCollection", "features": features}
    return Response(
        content=json.dumps(geojson),
        media_type="application/geo+json",
        headers={"Content-Disposition": "attachment; filename=census_all_counties.geojson"},
    )


@router.get("/api/maps/{dataset_key:path}/download")
def census_map_download(
    dataset_key: str,
    format: str = "geojson",
    store=Depends(get_store),
) -> Response:
    """Download a single dataset as full-resolution GeoJSON or CSV."""
    if format not in ("geojson", "csv"):
        return JSONResponse({"error": f"Unsupported format: {format}"}, status_code=400)

    db = store._db
    docs = list(db.handler_output.find({"dataset_key": dataset_key}))

    features: list[dict[str, Any]] = []
    for doc in docs:
        geom = doc.get("geometry")
        props = doc.get("properties", {})
        if geom:
            features.append({"type": "Feature", "geometry": geom, "properties": props})

    safe_name = dataset_key.replace("/", "_").replace("..", "_")

    if format == "csv":
        body = _features_to_csv(features)
        return Response(
            content=body,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={safe_name}.csv"},
        )

    geojson = {"type": "FeatureCollection", "features": features}
    return Response(
        content=json.dumps(geojson),
        media_type="application/geo+json",
        headers={"Content-Disposition": f"attachment; filename={safe_name}.geojson"},
    )


@router.get("/maps/states")
def census_map_states(
    request: Request,
    store=Depends(get_store),
):
    """Render a state-level summary with aggregated stats and choropleth map."""
    db = store._db
    prefix = "census.joined."
    dataset_keys = sorted(
        k for k in db.handler_output.distinct("dataset_key")
        if k.startswith(prefix)
    )

    features: list[dict[str, Any]] = []
    for dk in dataset_keys:
        docs = db.handler_output.find({"dataset_key": dk, "geometry": {"$exists": True}})
        for doc in docs:
            features.append({
                "type": "Feature",
                "geometry": doc["geometry"],
                "properties": doc.get("properties", {}),
            })

    state_stats = _aggregate_state_stats(features)

    # Detect numeric fields for the choropleth dropdown from state_stats
    choropleth_fields = ["total_population", "total_housing_units",
                         "weighted_median_income", "population_density"]

    return request.app.state.templates.TemplateResponse(
        request,
        "census/map_states.html",
        {
            "state_stats": state_stats,
            "state_count": len(state_stats),
            "choropleth_fields": choropleth_fields,
            "field_labels": _FIELD_LABELS,
            "popup_fields": _POPUP_FIELDS,
            "active_tab": "census_maps",
        },
    )


@router.get("/api/maps/states")
def census_map_states_api(
    store=Depends(get_store),
    field: str = "total_population",
):
    """Return GeoJSON with _state_value property for state choropleth.

    Each county feature is annotated with its state's aggregate value.
    """
    db = store._db
    prefix = "census.joined."
    dataset_keys = sorted(
        k for k in db.handler_output.distinct("dataset_key")
        if k.startswith(prefix)
    )

    features: list[dict[str, Any]] = []
    for dk in dataset_keys:
        docs = db.handler_output.find({"dataset_key": dk, "geometry": {"$exists": True}})
        for doc in docs:
            features.append({
                "type": "Feature",
                "geometry": doc["geometry"],
                "properties": doc.get("properties", {}),
            })

    # Compute state aggregates and build lookup
    state_stats = _aggregate_state_stats(features)
    state_lookup = {s["state_fips"]: s for s in state_stats}

    # Build output features with simplified geometry and state value
    out_features: list[dict[str, Any]] = []
    for f in features:
        props = f.get("properties", {})
        fips = str(props.get("STATEFP", ""))
        state = state_lookup.get(fips, {})
        slim = _slim_properties(props)
        slim["_state"] = state.get("state_name", "")
        slim["_state_value"] = state.get(field, 0)
        geom = _simplify_geometry(f["geometry"])
        out_features.append({"type": "Feature", "geometry": geom, "properties": slim})

    return JSONResponse({"type": "FeatureCollection", "features": out_features})


@router.get("/maps/{dataset_key:path}/table")
def census_table_view(
    dataset_key: str,
    request: Request,
    store=Depends(get_store),
):
    """Render a sortable data table for a single dataset's features."""
    db = store._db
    docs = list(db.handler_output.find({"dataset_key": dataset_key}))

    features: list[dict[str, Any]] = []
    for doc in docs:
        geom = doc.get("geometry")
        props = doc.get("properties", {})
        if geom:
            features.append({"type": "Feature", "geometry": geom, "properties": props})

    numeric_fields = _filter_numeric_fields(features[0].get("properties", {})) if features else []
    stats = _compute_stats(features, numeric_fields) if features else {}

    # Build column order: GEOID, NAME, preferred numeric, then rest alpha
    all_keys: set[str] = set()
    for f in features:
        all_keys.update(f.get("properties", {}).keys())
    columns: list[str] = []
    for k in ("GEOID", "NAME"):
        if k in all_keys:
            columns.append(k)
            all_keys.discard(k)
    for k in _PREFERRED_FIELDS:
        if k in all_keys:
            columns.append(k)
            all_keys.discard(k)
    columns.extend(sorted(all_keys))

    region = _region_label(dataset_key)

    return request.app.state.templates.TemplateResponse(
        request,
        "census/table_view.html",
        {
            "dataset_key": dataset_key,
            "region": region,
            "features": features,
            "feature_count": len(features),
            "columns": columns,
            "numeric_fields": numeric_fields,
            "stats": stats,
            "field_labels": _FIELD_LABELS,
            "active_tab": "census_maps",
        },
    )


@router.get("/maps/{dataset_key:path}")
def census_map_view(
    dataset_key: str,
    request: Request,
    store=Depends(get_store),
):
    """Render a Leaflet map for a single dataset's GeoJSON features."""
    db = store._db
    docs = list(db.handler_output.find({"dataset_key": dataset_key}))

    features: list[dict[str, Any]] = []
    for doc in docs:
        geom = doc.get("geometry")
        props = doc.get("properties", {})
        if geom:
            features.append({"type": "Feature", "geometry": geom, "properties": props})

    geojson: dict[str, Any] = {"type": "FeatureCollection", "features": features}

    # Identify numeric property fields for the choropleth dropdown.
    numeric_fields: list[str] = []
    if features:
        numeric_fields = _filter_numeric_fields(features[0].get("properties", {}))

    geojson_str = json.dumps(geojson)

    region = _region_label(dataset_key)

    return request.app.state.templates.TemplateResponse(
        request,
        "census/map_view.html",
        {
            "dataset_key": dataset_key,
            "region": region,
            "geojson_str": geojson_str,
            "feature_count": len(features),
            "numeric_fields": numeric_fields,
            "field_labels": _FIELD_LABELS,
            "popup_fields": _POPUP_FIELDS,
            "active_tab": "census_maps",
        },
    )


@router.get("/api/maps/{dataset_key:path}")
def census_map_api(
    dataset_key: str,
    store=Depends(get_store),
):
    """Return raw GeoJSON FeatureCollection as JSON."""
    db = store._db
    docs = list(db.handler_output.find({"dataset_key": dataset_key}))

    features: list[dict[str, Any]] = []
    for doc in docs:
        geom = doc.get("geometry")
        props = doc.get("properties", {})
        if geom:
            features.append({"type": "Feature", "geometry": geom, "properties": props})

    return JSONResponse({"type": "FeatureCollection", "features": features})


@router.get("/compare")
def census_compare(
    request: Request,
    left: str = "",
    right: str = "",
    store=Depends(get_store),
):
    """Compare two state datasets side-by-side with maps and a diff table."""
    db = store._db
    prefix = "census.joined."
    available = sorted(
        k for k in db.handler_output.distinct("dataset_key")
        if k.startswith(prefix)
    )
    # Enrich with region names
    datasets = [{"key": k, "label": f"{_region_label(k)} ({k})" if _region_label(k) else k}
                for k in available]

    left_features: list[dict[str, Any]] = []
    right_features: list[dict[str, Any]] = []
    comparison: list[dict[str, Any]] = []
    numeric_fields: list[str] = []
    left_geojson_str = "null"
    right_geojson_str = "null"

    if left and right:
        # Query left features
        for doc in db.handler_output.find({"dataset_key": left, "geometry": {"$exists": True}}):
            left_features.append({
                "type": "Feature",
                "geometry": doc["geometry"],
                "properties": doc.get("properties", {}),
            })
        # Query right features
        for doc in db.handler_output.find({"dataset_key": right, "geometry": {"$exists": True}}):
            right_features.append({
                "type": "Feature",
                "geometry": doc["geometry"],
                "properties": doc.get("properties", {}),
            })
        # Compute stats and comparison
        all_features = left_features + right_features
        if all_features:
            numeric_fields = _filter_numeric_fields(all_features[0].get("properties", {}))
        left_stats = _compute_stats(left_features, numeric_fields)
        right_stats = _compute_stats(right_features, numeric_fields)
        comparison = _build_comparison(left_stats, right_stats, numeric_fields)
        left_geojson_str = json.dumps({"type": "FeatureCollection", "features": left_features})
        right_geojson_str = json.dumps({"type": "FeatureCollection", "features": right_features})

    return request.app.state.templates.TemplateResponse(
        request,
        "census/compare.html",
        {
            "datasets": datasets,
            "left": left,
            "right": right,
            "left_region": _region_label(left),
            "right_region": _region_label(right),
            "left_geojson_str": left_geojson_str,
            "right_geojson_str": right_geojson_str,
            "left_count": len(left_features),
            "right_count": len(right_features),
            "comparison": comparison,
            "numeric_fields": numeric_fields,
            "field_labels": _FIELD_LABELS,
            "popup_fields": _POPUP_FIELDS,
            "active_tab": "census_maps",
        },
    )
