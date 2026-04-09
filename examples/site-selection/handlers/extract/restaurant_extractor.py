"""Restaurant extractor for site-selection.

Uses pyosmium to extract food amenities from PBF files and
produces a GeoJSON FeatureCollection of Point features.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

try:
    import osmium

    HAS_OSMIUM = True
except ImportError:
    HAS_OSMIUM = False

from facetwork.config import get_output_base

logger = logging.getLogger(__name__)

_LOCAL_OUTPUT = get_output_base()
_OUTPUT_DIR = os.environ.get(
    "AFL_SITESEL_OUTPUT_DIR", os.path.join(_LOCAL_OUTPUT, "sitesel-output")
)

FOOD_AMENITIES = {
    "restaurant",
    "fast_food",
    "cafe",
    "bar",
    "pub",
    "food_court",
    "ice_cream",
}


if HAS_OSMIUM:

    class RestaurantHandler(osmium.SimpleHandler):
        """Extract food amenity nodes from PBF."""

        def __init__(self) -> None:
            super().__init__()
            self.features: list[dict[str, Any]] = []

        def node(self, n) -> None:
            amenity = n.tags.get("amenity", "")
            if amenity in FOOD_AMENITIES:
                self.features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [n.location.lon, n.location.lat],
                        },
                        "properties": {
                            "name": n.tags.get("name", ""),
                            "amenity": amenity,
                            "cuisine": n.tags.get("cuisine", ""),
                        },
                    }
                )


def extract_restaurants(pbf_path: str, region: str) -> dict[str, Any]:
    """Extract food amenities from PBF -> GeoJSON file.

    Args:
        pbf_path: Path to the PBF file.
        region: Region name for output filename.

    Returns:
        Dict with output_path, restaurant_count, region.
    """
    if not HAS_OSMIUM:
        logger.warning("pyosmium not available; returning empty result")
        return _empty_result(region)

    output_dir = os.path.join(_OUTPUT_DIR, "restaurants")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    handler = RestaurantHandler()

    if pbf_path and os.path.exists(pbf_path):
        handler.apply_file(pbf_path, locations=True)

    output_path = os.path.join(output_dir, f"{region}_restaurants.geojson")
    geojson = {"type": "FeatureCollection", "features": handler.features}
    with open(output_path, "w") as f:
        json.dump(geojson, f)

    logger.info("Extracted %d restaurants from %s", len(handler.features), pbf_path)

    return {
        "output_path": output_path,
        "restaurant_count": len(handler.features),
        "region": region,
    }


def _empty_result(region: str) -> dict[str, Any]:
    """Return an empty result when pyosmium is not available."""
    output_dir = os.path.join(_OUTPUT_DIR, "restaurants")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(output_dir, f"{region}_restaurants.geojson")
    with open(output_path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": []}, f)
    return {
        "output_path": output_path,
        "restaurant_count": 0,
        "region": region,
    }
