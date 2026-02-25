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

logger = logging.getLogger(__name__)

_LOCAL_OUTPUT = os.environ.get("AFL_LOCAL_OUTPUT_DIR", "/tmp")
_OUTPUT_DIR = os.environ.get("AFL_CENSUS_OUTPUT_DIR",
                             os.path.join(_LOCAL_OUTPUT, "census-output"))


@dataclass
class JoinResult:
    """Result of a geographic join operation."""
    output_path: str
    feature_count: int
    join_field: str
    extraction_date: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


@dataclass
class SummaryResult:
    """Result of a state summary operation."""
    state_fips: str
    state_name: str
    output_path: str
    tables_joined: int
    record_count: int
    extraction_date: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


def join_geo(acs_path: str, tiger_path: str,
             join_field: str = "GEOID") -> JoinResult:
    """Join ACS CSV data with TIGER GeoJSON features.

    Args:
        acs_path: Path to ACS CSV file.
        tiger_path: Path to TIGER GeoJSON file.
        join_field: Field to join on (default GEOID).

    Returns:
        JoinResult with output path and feature count.
    """
    output_dir = os.path.join(_OUTPUT_DIR, "joined")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Load ACS data keyed by join field
    acs_data: dict[str, dict[str, str]] = {}
    if os.path.exists(acs_path):
        with open(acs_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row.get(join_field, "")
                if key:
                    acs_data[key] = dict(row)

    # Load TIGER GeoJSON
    features: list[dict[str, Any]] = []
    if os.path.exists(tiger_path):
        with open(tiger_path) as f:
            geojson = json.load(f)
            features = geojson.get("features", [])

    # Join: enrich TIGER features with ACS attributes + population density
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
                props["population_density_km2"] = (
                    round(pop / area_km2, 2) if area_km2 > 0 else 0.0
                )
            except (ValueError, TypeError):
                pass
        joined_features.append({
            "type": "Feature",
            "properties": props,
            "geometry": feat.get("geometry"),
        })

    acs_stem = Path(acs_path).stem if acs_path else "unknown"
    tiger_stem = Path(tiger_path).stem if tiger_path else "unknown"
    output_path = os.path.join(output_dir,
                               f"{acs_stem}_{tiger_stem}_joined.geojson")

    with open(output_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": joined_features}, f)

    logger.info("Joined %d features (%s + %s)",
                len(joined_features), acs_path, tiger_path)

    return JoinResult(
        output_path=output_path,
        feature_count=len(joined_features),
        join_field=join_field,
    )


def summarize_state(population: dict[str, Any], income: dict[str, Any],
                    housing: dict[str, Any], education: dict[str, Any],
                    commuting: dict[str, Any]) -> SummaryResult:
    """Build a state-level summary from multiple ACS extraction results.

    Args:
        population: ACSResult dict from ExtractPopulation.
        income: ACSResult dict from ExtractIncome.
        housing: ACSResult dict from ExtractHousing.
        education: ACSResult dict from ExtractEducation.
        commuting: ACSResult dict from ExtractCommuting.

    Returns:
        SummaryResult with output path, tables joined, and record count.
    """
    output_dir = os.path.join(_OUTPUT_DIR, "summary")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Collect all input results
    inputs = {
        "population": population,
        "income": income,
        "housing": housing,
        "education": education,
        "commuting": commuting,
    }

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

    logger.info("State summary for %s: %d tables, %d total records",
                state_fips, tables_joined, total_records)

    return SummaryResult(
        state_fips=state_fips,
        state_name="",
        output_path=output_path,
        tables_joined=tables_joined,
        record_count=total_records,
    )
