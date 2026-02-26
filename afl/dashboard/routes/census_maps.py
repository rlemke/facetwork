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

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

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
    # Preferred fields are shown first (friendly derived names), then any
    # remaining numeric fields that don't match raw ACS codes or TIGER IDs.
    _PREFERRED = [
        "population", "population_density", "median_income",
        "housing_units", "total_households", "family_households", "nonfamily_households",
        "pct_owner_occupied", "pct_renter_occupied",
        "pct_no_vehicle", "pct_drive_alone", "pct_public_transit", "pct_walk", "pct_work_from_home",
        "pct_under_18", "pct_18_34", "pct_35_64", "pct_65_plus",
    ]
    _SKIP_PREFIXES = ("B0", "B1", "B2", "B3")  # raw ACS variable codes
    _SKIP_FIELDS = {"ALAND", "AWATER", "CBSAFP", "CSAFP", "METDIVFP", "STATEFP", "COUNTYFP"}

    numeric_fields: list[str] = []
    if features:
        sample = features[0].get("properties", {})
        all_numeric = {k for k, v in sample.items() if isinstance(v, (int, float))}
        # Add preferred fields first (in order), then remaining non-skipped fields.
        for key in _PREFERRED:
            if key in all_numeric:
                numeric_fields.append(key)
                all_numeric.discard(key)
        for key in sorted(all_numeric):
            if key in _SKIP_FIELDS or any(key.startswith(p) for p in _SKIP_PREFIXES):
                continue
            numeric_fields.append(key)

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
