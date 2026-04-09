"""Demographics extractor for site-selection.

Joins ACS demographic data with TIGER geographic boundaries and
produces a combined GeoJSON with derived metrics for scoring.
"""

import csv
import json
import logging
import os
from pathlib import Path
from typing import Any

from facetwork.config import get_output_base

logger = logging.getLogger(__name__)

_LOCAL_OUTPUT = get_output_base()
_OUTPUT_DIR = os.environ.get(
    "AFL_SITESEL_OUTPUT_DIR", os.path.join(_LOCAL_OUTPUT, "sitesel-output")
)


def _safe_pct(
    num_key: str, den_key: str, props: dict[str, Any], *, scale: float = 100.0
) -> float | None:
    """Compute num/den * scale, returning None if missing/zero."""
    num = props.get(num_key)
    den = props.get(den_key)
    if num is None or den is None:
        return None
    try:
        n = float(num)
        d = float(den)
    except (ValueError, TypeError):
        return None
    if d == 0:
        return None
    return round(n / d * scale, 2)


def _compute_derived_metrics(props: dict[str, Any]) -> dict[str, Any]:
    """Compute derived fields from raw ACS columns."""
    derived: dict[str, Any] = {}

    # Friendly aliases
    for raw, friendly in [
        ("B01003_001E", "population"),
        ("B19013_001E", "median_income"),
    ]:
        v = props.get(raw)
        if v is not None:
            try:
                derived[friendly] = float(v)
            except (ValueError, TypeError):
                pass

    # Percentage metrics
    pct_metrics = [
        ("pct_below_poverty", "B17001_002E", "B17001_001E"),
        ("unemployment_rate", "B23025_005E", "B23025_003E"),
        ("labor_force_participation", "B23025_002E", "B23025_001E"),
        ("pct_owner_occupied", "B25003_002E", "B25003_001E"),
    ]
    for name, num_key, den_key in pct_metrics:
        val = _safe_pct(num_key, den_key, props)
        if val is not None:
            derived[name] = val

    # Bachelor's degree or higher: sum of B15003_022E..025E / B15003_001E
    edu_cols = ["B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E"]
    edu_den = props.get("B15003_001E")
    if edu_den is not None:
        try:
            den = float(edu_den)
            if den > 0:
                num_vals = []
                for c in edu_cols:
                    v = props.get(c)
                    if v is not None:
                        num_vals.append(float(v))
                if num_vals:
                    derived["pct_bachelors_plus"] = round(sum(num_vals) / den * 100.0, 2)
        except (ValueError, TypeError):
            pass

    return derived


def _load_acs_csv(path: str) -> dict[str, dict[str, str]]:
    """Load an ACS CSV file, returning a dict keyed by GEOID."""
    data: dict[str, dict[str, str]] = {}
    if path and os.path.exists(path):
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row.get("GEOID", "")
                if key:
                    data[key] = dict(row)
    return data


def join_demographics(acs_path: str, tiger_path: str, state_fips: str) -> dict[str, Any]:
    """Join ACS CSV data with TIGER GeoJSON features.

    Reads county-level ACS data and TIGER boundaries, filters by
    state FIPS, computes derived metrics, and writes combined GeoJSON.

    Args:
        acs_path: Path to ACS CSV file.
        tiger_path: Path to TIGER GeoJSON file.
        state_fips: Two-digit state FIPS code for filtering.

    Returns:
        Dict with output_path, feature_count, state_fips.
    """
    output_dir = os.path.join(_OUTPUT_DIR, "demographics")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Load ACS data keyed by GEOID
    acs_data = _load_acs_csv(acs_path)

    # Load TIGER GeoJSON
    features: list[dict[str, Any]] = []
    if tiger_path and os.path.exists(tiger_path):
        with open(tiger_path) as f:
            geojson = json.load(f)
            features = geojson.get("features", [])

    # Filter features to target state and join with ACS
    joined_features: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties", {})
        feat_statefp = props.get("STATEFP", "")
        if feat_statefp != state_fips:
            continue

        geoid = props.get("GEOID", "")
        full_geoid = f"0500000US{geoid}" if len(geoid) == 5 else geoid
        if full_geoid in acs_data:
            props.update(acs_data[full_geoid])
        elif geoid in acs_data:
            props.update(acs_data[geoid])

        # Compute population density from TIGER ALAND
        aland = props.get("ALAND")
        pop_est = props.get("B01003_001E")
        if aland is not None and pop_est is not None:
            try:
                area_km2 = float(aland) / 1e6
                pop = float(pop_est)
                props["population_density_km2"] = round(pop / area_km2, 2) if area_km2 > 0 else 0.0
            except (ValueError, TypeError):
                pass

        # Compute derived metrics
        props.update(_compute_derived_metrics(props))

        joined_features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": feat.get("geometry"),
            }
        )

    output_path = os.path.join(output_dir, f"{state_fips}_demographics.geojson")
    with open(output_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": joined_features}, f)

    logger.info("Joined demographics: %d features for state %s", len(joined_features), state_fips)

    return {
        "output_path": output_path,
        "feature_count": len(joined_features),
        "state_fips": state_fips,
    }
