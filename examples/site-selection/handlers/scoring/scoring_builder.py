"""Suitability scoring for site-selection.

Combines demographics and restaurant data to score counties by
suitability for new food-service locations.
"""

import json
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from shapely.geometry import Point, shape
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

logger = logging.getLogger(__name__)

_LOCAL_OUTPUT = os.environ.get("AFL_LOCAL_OUTPUT_DIR", "/tmp")
_OUTPUT_DIR = os.environ.get("AFL_SITESEL_OUTPUT_DIR",
                             os.path.join(_LOCAL_OUTPUT, "sitesel-output"))

# Demand index weights (must sum to 1.0)
DEMAND_WEIGHTS: dict[str, float] = {
    "population_density_km2": 0.25,
    "median_income": 0.20,
    "labor_force_participation": 0.15,
    "pct_bachelors_plus": 0.10,
    "pct_owner_occupied": 0.10,
    "inverse_poverty": 0.20,
}


def _normalize_values(values: list[float]) -> list[float]:
    """Normalize values to 0-1 range using min-max scaling."""
    if not values:
        return []
    vmin = min(values)
    vmax = max(values)
    if vmax == vmin:
        return [0.5] * len(values)
    return [(v - vmin) / (vmax - vmin) for v in values]


def _count_restaurants_per_county(
    counties: list[dict[str, Any]],
    restaurants: list[dict[str, Any]],
) -> dict[str, int]:
    """Count restaurants per county using point-in-polygon.

    Falls back to a simple dict (all zeros) if shapely is unavailable.
    """
    county_counts: dict[str, int] = defaultdict(int)

    if not HAS_SHAPELY or not restaurants:
        return dict(county_counts)

    # Build county polygons
    county_shapes = []
    for feat in counties:
        geom = feat.get("geometry")
        props = feat.get("properties", {})
        county_id = props.get("GEOID", props.get("NAME", ""))
        if geom:
            try:
                poly = shape(geom)
                county_shapes.append((county_id, poly))
            except Exception:
                continue

    # Assign each restaurant to a county
    for rest in restaurants:
        geom = rest.get("geometry")
        if not geom or geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        pt = Point(coords[0], coords[1])
        for county_id, poly in county_shapes:
            if poly.contains(pt):
                county_counts[county_id] += 1
                break

    return dict(county_counts)


def score_counties(demographics_path: str, restaurants_path: str,
                   state_fips: str) -> dict[str, Any]:
    """Score counties by food-service suitability.

    1. Load demographics GeoJSON (county polygons with derived metrics)
    2. Load restaurants GeoJSON (point features)
    3. Point-in-polygon: count restaurants per county
    4. Compute competition density: restaurants_per_1000
    5. Compute demand index: weighted normalized score of demographics
    6. Compute suitability score: demand_index * 100 / (1 + competition)
    7. Write scored GeoJSON

    Args:
        demographics_path: Path to demographics GeoJSON.
        restaurants_path: Path to restaurants GeoJSON.
        state_fips: Two-digit state FIPS code.

    Returns:
        Dict with output_path, county_count, top_county, top_score.
    """
    output_dir = os.path.join(_OUTPUT_DIR, "scored")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Load demographics
    demo_features: list[dict[str, Any]] = []
    if demographics_path and os.path.exists(demographics_path):
        with open(demographics_path) as f:
            demo_features = json.load(f).get("features", [])

    # Load restaurants
    rest_features: list[dict[str, Any]] = []
    if restaurants_path and os.path.exists(restaurants_path):
        with open(restaurants_path) as f:
            rest_features = json.load(f).get("features", [])

    # Count restaurants per county
    county_counts = _count_restaurants_per_county(demo_features, rest_features)

    # Collect raw values for normalization
    raw_values: dict[str, list[float]] = {k: [] for k in DEMAND_WEIGHTS}
    for feat in demo_features:
        props = feat.get("properties", {})
        for field in DEMAND_WEIGHTS:
            if field == "inverse_poverty":
                pov = props.get("pct_below_poverty")
                raw_values[field].append(
                    100.0 - float(pov) if pov is not None else 0.0)
            else:
                v = props.get(field)
                raw_values[field].append(float(v) if v is not None else 0.0)

    # Normalize each factor to 0-1
    normalized: dict[str, list[float]] = {}
    for field, vals in raw_values.items():
        normalized[field] = _normalize_values(vals)

    # Score each county
    scored_features: list[dict[str, Any]] = []
    top_county = ""
    top_score = 0.0

    for i, feat in enumerate(demo_features):
        props = feat.get("properties", {})
        county_id = props.get("GEOID", props.get("NAME", ""))
        county_name = props.get("NAME", county_id)

        # Restaurant count and competition density
        rest_count = county_counts.get(county_id, 0)
        population = props.get("population", 0.0)
        if isinstance(population, str):
            try:
                population = float(population)
            except (ValueError, TypeError):
                population = 0.0
        restaurants_per_1000 = (
            rest_count / (population / 1000.0)
            if population > 0 else 0.0
        )

        # Demand index: weighted sum of normalized factors
        demand_index = 0.0
        for field, weight in DEMAND_WEIGHTS.items():
            if i < len(normalized.get(field, [])):
                demand_index += normalized[field][i] * weight
        demand_index = round(demand_index, 4)

        # Suitability score: high demand + low competition = high score
        suitability_score = round(
            demand_index * 100.0 / (1.0 + restaurants_per_1000), 2)

        # Add scoring fields to properties
        props["restaurant_count"] = rest_count
        props["restaurants_per_1000"] = round(restaurants_per_1000, 4)
        props["demand_index"] = demand_index
        props["suitability_score"] = suitability_score

        scored_features.append({
            "type": "Feature",
            "properties": props,
            "geometry": feat.get("geometry"),
        })

        if suitability_score > top_score:
            top_score = suitability_score
            top_county = county_name

    # Sort by suitability score descending
    scored_features.sort(
        key=lambda f: f["properties"].get("suitability_score", 0),
        reverse=True,
    )

    output_path = os.path.join(output_dir, f"{state_fips}_scored.geojson")
    with open(output_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": scored_features}, f)

    logger.info("Scored %d counties for state %s (top: %s = %.2f)",
                len(scored_features), state_fips, top_county, top_score)

    return {
        "output_path": output_path,
        "county_count": len(scored_features),
        "top_county": top_county,
        "top_score": top_score,
    }
