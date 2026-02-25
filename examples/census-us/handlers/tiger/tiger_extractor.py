"""TIGER/Line shapefile extraction.

Reads shapefiles from downloaded TIGER ZIP archives and writes
GeoJSON output filtered by state FIPS code.
"""

import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import fiona
    HAS_FIONA = True
except ImportError:
    HAS_FIONA = False

# Geography level → TIGER file component and FIPS field name
_GEO_CONFIG: dict[str, dict[str, str]] = {
    "COUNTY": {"suffix": "county", "fips_field": "STATEFP"},
    "TRACT": {"suffix": "tract", "fips_field": "STATEFP"},
    "BG": {"suffix": "bg", "fips_field": "STATEFP"},
    "PLACE": {"suffix": "place", "fips_field": "STATEFP"},
}

_LOCAL_OUTPUT = os.environ.get("AFL_LOCAL_OUTPUT_DIR", "/tmp")
_OUTPUT_DIR = os.environ.get("AFL_CENSUS_OUTPUT_DIR",
                             os.path.join(_LOCAL_OUTPUT, "census-output"))


@dataclass
class TIGERExtractionResult:
    """Result of a TIGER shapefile extraction."""
    output_path: str
    feature_count: int
    geography_level: str
    year: str
    format: str = "GeoJSON"
    extraction_date: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )


def extract_tiger(zip_path: str, geo_level: str, state_fips: str,
                  year: str = "2024") -> TIGERExtractionResult:
    """Extract features from a TIGER/Line shapefile ZIP.

    Args:
        zip_path: Path to downloaded TIGER ZIP file.
        geo_level: Geography level (COUNTY, TRACT, BG, PLACE).
        state_fips: Two-digit state FIPS code.
        year: TIGER year.

    Returns:
        TIGERExtractionResult with output path and feature count.
    """
    geo_upper = geo_level.upper()
    config = _GEO_CONFIG.get(geo_upper)
    if config is None:
        raise ValueError(f"Unsupported geo_level: {geo_level}. "
                         f"Supported: {list(_GEO_CONFIG.keys())}")

    output_dir = os.path.join(_OUTPUT_DIR, "tiger", geo_upper.lower())
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(output_dir,
                               f"{state_fips}_{geo_upper.lower()}.geojson")

    features: list[dict[str, Any]] = []

    if HAS_FIONA and os.path.exists(zip_path):
        try:
            with fiona.open(f"zip://{zip_path}") as src:
                for feature in src:
                    props = feature.get("properties", {})
                    if props.get(config["fips_field"]) == state_fips:
                        features.append({
                            "type": "Feature",
                            "properties": dict(props),
                            "geometry": dict(feature["geometry"]),
                        })
        except Exception as exc:
            logger.warning("Failed to read TIGER ZIP %s: %s", zip_path, exc)
    elif os.path.exists(zip_path):
        # Fallback: try to find .geojson inside ZIP
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                geojson_names = [n for n in zf.namelist()
                                 if n.endswith(".geojson")]
                for name in geojson_names:
                    with zf.open(name) as f:
                        data = json.load(f)
                        for feat in data.get("features", []):
                            props = feat.get("properties", {})
                            if props.get(config["fips_field"]) == state_fips:
                                features.append(feat)
        except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
            logger.warning("Failed to read TIGER ZIP %s: %s", zip_path, exc)

    # Write GeoJSON output
    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }
    with open(output_path, "w") as f:
        json.dump(geojson, f)

    logger.info("Extracted %d features for %s (state=%s, level=%s)",
                len(features), geo_upper, state_fips, geo_level)

    return TIGERExtractionResult(
        output_path=output_path,
        feature_count=len(features),
        geography_level=geo_level,
        year=year,
    )
