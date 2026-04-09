"""Census summary builder.

Joins ACS demographic data with TIGER geographic boundaries and
produces combined GeoJSON with demographic attributes, or a
state-level summary from multiple ACS extraction results.
"""

import csv
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from facetwork.config import get_output_base

logger = logging.getLogger(__name__)

_LOCAL_OUTPUT = get_output_base()
_OUTPUT_DIR = os.environ.get("AFL_CENSUS_OUTPUT_DIR", os.path.join(_LOCAL_OUTPUT, "census-output"))


@dataclass
class JoinResult:
    """Result of a geographic join operation."""

    output_path: str
    feature_count: int
    join_field: str
    extraction_date: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class SummaryResult:
    """Result of a state summary operation."""

    state_fips: str
    state_name: str
    output_path: str
    tables_joined: int
    record_count: int
    extraction_date: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


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
    """Compute derived fields from raw ACS columns. Returns dict of non-None values."""
    derived: dict[str, Any] = {}

    # Friendly aliases
    for raw, friendly in [
        ("B01003_001E", "population"),
        ("B19013_001E", "median_income"),
        ("B25001_001E", "housing_units"),
    ]:
        v = props.get(raw)
        if v is not None:
            try:
                derived[friendly] = float(v)
            except (ValueError, TypeError):
                pass

    # Percentage metrics
    pct_metrics = [
        ("pct_owner_occupied", "B25003_002E", "B25003_001E"),
        ("pct_below_poverty", "B17001_002E", "B17001_001E"),
        ("unemployment_rate", "B23025_005E", "B23025_003E"),
        ("labor_force_participation", "B23025_002E", "B23025_001E"),
        ("pct_white", "B02001_002E", "B02001_001E"),
        ("pct_black", "B02001_003E", "B02001_001E"),
        ("pct_asian", "B02001_005E", "B02001_001E"),
        ("pct_drove_alone", "B08301_003E", "B08301_001E"),
        ("pct_public_transit", "B08301_010E", "B08301_001E"),
    ]
    for name, num_key, den_key in pct_metrics:
        val = _safe_pct(num_key, den_key, props)
        if val is not None:
            derived[name] = val

    # Renter occupied = complement of owner occupied
    if "pct_owner_occupied" in derived:
        derived["pct_renter_occupied"] = round(100.0 - derived["pct_owner_occupied"], 2)

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

    # Vehicles per household: weighted average from B25044
    # B25044_003-006 = owner 1-4+ vehicles, B25044_010-013 = renter 1-4+ vehicles
    # B25044_001E = total occupied units
    total_occ = props.get("B25044_001E")
    if total_occ is not None:
        try:
            tot = float(total_occ)
            if tot > 0:
                weighted_sum = 0.0
                vehicle_pairs = [
                    ("B25044_003E", 1),
                    ("B25044_004E", 2),
                    ("B25044_005E", 3),
                    ("B25044_006E", 4),
                    ("B25044_010E", 1),
                    ("B25044_011E", 2),
                    ("B25044_012E", 3),
                    ("B25044_013E", 4),
                ]
                has_data = False
                for col, weight in vehicle_pairs:
                    v = props.get(col)
                    if v is not None:
                        try:
                            weighted_sum += float(v) * weight
                            has_data = True
                        except (ValueError, TypeError):
                            pass
                if has_data:
                    derived["vehicles_per_household"] = round(weighted_sum / tot, 2)
        except (ValueError, TypeError):
            pass

    return derived


def _load_acs_csv(path: str, join_field: str) -> dict[str, dict[str, str]]:
    """Load an ACS CSV file, returning a dict keyed by join_field."""
    data: dict[str, dict[str, str]] = {}
    if path and os.path.exists(path):
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row.get(join_field, "")
                if key:
                    data[key] = dict(row)
    return data


def join_geo(
    acs_path: str,
    tiger_path: str,
    join_field: str = "GEOID",
    extra_acs_paths: list[str] | None = None,
) -> JoinResult:
    """Join ACS CSV data with TIGER GeoJSON features.

    Args:
        acs_path: Path to primary ACS CSV file (population).
        tiger_path: Path to TIGER GeoJSON file.
        join_field: Field to join on (default GEOID).
        extra_acs_paths: Additional ACS CSV paths to merge.

    Returns:
        JoinResult with output path and feature count.
    """
    output_dir = os.path.join(_OUTPUT_DIR, "joined")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Load primary ACS data keyed by join field
    acs_data = _load_acs_csv(acs_path, join_field)

    # Merge extra ACS CSVs
    for extra_path in extra_acs_paths or []:
        extra_data = _load_acs_csv(extra_path, join_field)
        for key, row in extra_data.items():
            if key in acs_data:
                acs_data[key].update(row)
            else:
                acs_data[key] = row

    # Load TIGER GeoJSON
    features: list[dict[str, Any]] = []
    if os.path.exists(tiger_path):
        with open(tiger_path) as f:
            geojson = json.load(f)
            features = geojson.get("features", [])

    # Join: enrich TIGER features with ACS attributes + density + derived metrics
    joined_features: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties", {})
        key = props.get(join_field, "")
        if key in acs_data:
            props.update(acs_data[key])
        # Compute population density (people per km²) from TIGER ALAND
        # and ACS population estimate (B01003_001E)
        aland = props.get("ALAND")
        pop_est = props.get("B01003_001E")
        if aland is not None and pop_est is not None:
            try:
                area_km2 = float(aland) / 1e6
                pop = float(pop_est)
                props["population_density_km2"] = round(pop / area_km2, 2) if area_km2 > 0 else 0.0
            except (ValueError, TypeError):
                pass
        # Compute derived percentage/rate metrics
        props.update(_compute_derived_metrics(props))
        joined_features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": feat.get("geometry"),
            }
        )

    acs_stem = Path(acs_path).stem if acs_path else "unknown"
    tiger_stem = Path(tiger_path).stem if tiger_path else "unknown"
    output_path = os.path.join(output_dir, f"{acs_stem}_{tiger_stem}_joined.geojson")

    with open(output_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": joined_features}, f)

    logger.info("Joined %d features (%s + %s)", len(joined_features), acs_path, tiger_path)

    return JoinResult(
        output_path=output_path,
        feature_count=len(joined_features),
        join_field=join_field,
    )


def summarize_state(
    population: dict[str, Any],
    income: dict[str, Any],
    housing: dict[str, Any],
    education: dict[str, Any],
    commuting: dict[str, Any],
    *,
    race: dict[str, Any] | None = None,
    poverty: dict[str, Any] | None = None,
    employment: dict[str, Any] | None = None,
) -> SummaryResult:
    """Build a state-level summary from multiple ACS extraction results.

    Args:
        population: ACSResult dict from ExtractPopulation.
        income: ACSResult dict from ExtractIncome.
        housing: ACSResult dict from ExtractHousing.
        education: ACSResult dict from ExtractEducation.
        commuting: ACSResult dict from ExtractCommuting.
        race: ACSResult dict from ExtractRace (optional).
        poverty: ACSResult dict from ExtractPoverty (optional).
        employment: ACSResult dict from ExtractEmployment (optional).

    Returns:
        SummaryResult with output path, tables joined, and record count.
    """
    output_dir = os.path.join(_OUTPUT_DIR, "summary")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Collect all input results
    inputs: dict[str, dict[str, Any]] = {
        "population": population,
        "income": income,
        "housing": housing,
        "education": education,
        "commuting": commuting,
    }
    if race is not None:
        inputs["race"] = race
    if poverty is not None:
        inputs["poverty"] = poverty
    if employment is not None:
        inputs["employment"] = employment

    # Derive state FIPS from first available result
    state_fips = ""
    for result in inputs.values():
        if result.get("output_path", ""):
            stem = Path(result["output_path"]).stem
            state_fips = stem.split("_")[0] if "_" in stem else ""
            if state_fips:
                break

    total_records = sum(r.get("record_count", 0) for r in inputs.values())
    tables_joined = sum(1 for r in inputs.values() if r.get("output_path"))

    summary = {
        "state_fips": state_fips,
        "tables": {
            name: {
                "table_id": r.get("table_id", ""),
                "record_count": r.get("record_count", 0),
                "output_path": r.get("output_path", ""),
            }
            for name, r in inputs.items()
        },
        "total_records": total_records,
        "tables_joined": tables_joined,
    }

    output_path = os.path.join(output_dir, f"{state_fips}_summary.json")
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info(
        "State summary for %s: %d tables, %d total records",
        state_fips,
        tables_joined,
        total_records,
    )

    return SummaryResult(
        state_fips=state_fips,
        state_name="",
        output_path=output_path,
        tables_joined=tables_joined,
        record_count=total_records,
    )
