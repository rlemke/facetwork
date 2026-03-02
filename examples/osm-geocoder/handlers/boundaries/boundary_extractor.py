"""Boundary extraction from OSM PBF files using pyosmium.

Extracts administrative and natural boundaries and outputs GeoJSON.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from afl.runtime.storage import get_storage_backend, localize

from ..shared._output import ensure_dir, open_output, resolve_output_dir

_storage = get_storage_backend()

try:
    import osmium
    from osmium import osm

    HAS_OSMIUM = True
except ImportError:
    HAS_OSMIUM = False
    osmium = None
    osm = None

try:
    from shapely import wkb
    from shapely.geometry import mapping

    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False
    wkb = None
    mapping = None

log = logging.getLogger(__name__)

# Default output directory for extracted boundaries
_LOCAL_OUTPUT = os.environ.get("AFL_LOCAL_OUTPUT_DIR", "/tmp")
DEFAULT_OUTPUT_DIR = Path(os.path.join(_LOCAL_OUTPUT, "osm-boundaries"))

# Admin level mappings
ADMIN_LEVEL_COUNTRY = 2
ADMIN_LEVEL_STATE = 4
ADMIN_LEVEL_COUNTY = 6
ADMIN_LEVEL_CITY = 8

# Natural type tag mappings
NATURAL_TYPE_WATER = {"natural": ["water"], "water": ["lake", "reservoir", "pond"]}
NATURAL_TYPE_FOREST = {"natural": ["wood"], "landuse": ["forest"]}
NATURAL_TYPE_PARK = {"leisure": ["park", "nature_reserve"], "boundary": ["national_park"]}


@dataclass
class BoundaryFeature:
    """A single boundary feature extracted from OSM."""

    osm_id: int
    osm_type: str  # 'way' or 'relation'
    name: str
    admin_level: int | None
    boundary_type: str
    tags: dict[str, str]
    geometry: dict[str, Any] | None = None


@dataclass
class ExtractionResult:
    """Result of a boundary extraction operation."""

    output_path: str
    feature_count: int
    boundary_type: str
    admin_levels: str
    format: str = "GeoJSON"
    extraction_date: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class BoundaryHandler(osmium.SimpleHandler if HAS_OSMIUM else object):
    """Pyosmium handler for extracting boundary features.

    Collects ways and relations matching the specified boundary criteria.
    """

    def __init__(
        self,
        admin_levels: list[int] | None = None,
        natural_types: list[str] | None = None,
    ):
        if HAS_OSMIUM:
            super().__init__()
        self.admin_levels = set(admin_levels) if admin_levels else set()
        self.natural_types = natural_types or []
        self.features: list[BoundaryFeature] = []
        self._wkb_factory = osmium.geom.WKBFactory() if HAS_OSMIUM else None

    def _matches_natural_type(self, tags: osm.TagList) -> str | None:
        """Check if tags match any configured natural type."""
        for natural_type in self.natural_types:
            type_tags = self._get_natural_tags(natural_type)
            for tag_key, tag_values in type_tags.items():
                if tag_key in tags and tags[tag_key] in tag_values:
                    return natural_type
        return None

    def _get_natural_tags(self, natural_type: str) -> dict[str, list[str]]:
        """Get tag mappings for a natural type."""
        if natural_type == "water":
            return NATURAL_TYPE_WATER
        elif natural_type == "forest":
            return NATURAL_TYPE_FOREST
        elif natural_type == "park":
            return NATURAL_TYPE_PARK
        return {}

    def _extract_tags(self, tags: osm.TagList) -> dict[str, str]:
        """Convert OSM tags to a dictionary."""
        return {tag.k: tag.v for tag in tags}

    def _get_geometry(self, obj: Any, obj_type: str) -> dict[str, Any] | None:
        """Extract geometry from an OSM object."""
        if not HAS_SHAPELY or not self._wkb_factory:
            return None
        try:
            if obj_type == "area":
                wkb_data = self._wkb_factory.create_multipolygon(obj)
            else:
                return None

            geom = wkb.loads(wkb_data, hex=True)
            return mapping(geom)
        except Exception as e:
            log.debug("Could not extract geometry: %s", e)
            return None

    def area(self, a: osm.Area) -> None:
        """Process an area (closed way or multipolygon relation)."""
        tags = a.tags

        # Check for administrative boundary
        if "boundary" in tags and tags["boundary"] == "administrative":
            admin_level_str = tags.get("admin_level", "")
            try:
                admin_level = int(admin_level_str)
            except ValueError:
                admin_level = None

            if admin_level is not None and admin_level in self.admin_levels:
                feature = BoundaryFeature(
                    osm_id=a.orig_id(),
                    osm_type="relation" if a.from_way() is False else "way",
                    name=tags.get("name", ""),
                    admin_level=admin_level,
                    boundary_type="administrative",
                    tags=self._extract_tags(tags),
                    geometry=self._get_geometry(a, "area"),
                )
                self.features.append(feature)
                return

        # Check for natural boundary
        natural_type = self._matches_natural_type(tags)
        if natural_type:
            feature = BoundaryFeature(
                osm_id=a.orig_id(),
                osm_type="relation" if a.from_way() is False else "way",
                name=tags.get("name", ""),
                admin_level=None,
                boundary_type=natural_type,
                tags=self._extract_tags(tags),
                geometry=self._get_geometry(a, "area"),
            )
            self.features.append(feature)


def extract_boundaries(
    pbf_path: str | Path,
    admin_levels: list[int] | None = None,
    natural_types: list[str] | None = None,
    output_dir: Path | None = None,
) -> ExtractionResult:
    """Extract boundaries from a PBF file and write to GeoJSON.

    Args:
        pbf_path: Path to the OSM PBF file
        admin_levels: List of admin_level values to extract (e.g., [2, 4] for countries and states)
        natural_types: List of natural boundary types (e.g., ["water", "forest", "park"])
        output_dir: Directory to write output files (defaults to /tmp/osm-boundaries)

    Returns:
        ExtractionResult with output path and statistics
    """
    if not HAS_OSMIUM:
        raise ImportError("pyosmium is required for boundary extraction")

    pbf_str = str(pbf_path)
    backend = get_storage_backend(pbf_str)
    if not backend.exists(pbf_str):
        raise FileNotFoundError(f"PBF file not found: {pbf_path}")
    local_pbf = localize(pbf_str)
    pbf_path = Path(local_pbf)

    if output_dir is None:
        out_base = resolve_output_dir("osm-boundaries")
    else:
        out_base = str(output_dir)

    # Build descriptive filename
    parts = [pbf_path.stem]
    if admin_levels:
        parts.append(f"admin{'-'.join(str(lvl) for lvl in sorted(admin_levels))}")
    if natural_types:
        parts.append("-".join(natural_types))
    output_name = "_".join(parts) + ".geojson"
    output_path = f"{out_base}/{output_name}"
    ensure_dir(output_path)

    # Extract boundaries
    handler = BoundaryHandler(admin_levels=admin_levels, natural_types=natural_types)
    handler.apply_file(str(pbf_path), locations=True)

    # Convert to GeoJSON
    geojson = _features_to_geojson(handler.features)

    # Write output
    with open_output(output_path) as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    log.info(
        "Extracted %d boundaries to %s",
        len(handler.features),
        output_path,
    )

    return ExtractionResult(
        output_path=output_path,
        feature_count=len(handler.features),
        boundary_type=_describe_boundary_type(admin_levels, natural_types),
        admin_levels=",".join(str(lvl) for lvl in (admin_levels or [])),
    )


def _features_to_geojson(features: list[BoundaryFeature]) -> dict[str, Any]:
    """Convert extracted features to GeoJSON FeatureCollection."""
    geojson_features = []
    for feat in features:
        properties = {
            "osm_id": feat.osm_id,
            "osm_type": feat.osm_type,
            "name": feat.name,
            "boundary_type": feat.boundary_type,
        }
        if feat.admin_level is not None:
            properties["admin_level"] = feat.admin_level
        properties.update(feat.tags)

        geojson_features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": feat.geometry,
            }
        )

    return {
        "type": "FeatureCollection",
        "features": geojson_features,
    }


def _describe_boundary_type(admin_levels: list[int] | None, natural_types: list[str] | None) -> str:
    """Generate a human-readable description of the boundary type."""
    parts = []
    if admin_levels:
        level_names = {
            2: "country",
            4: "state",
            6: "county",
            8: "city",
        }
        parts.extend(level_names.get(lvl, f"admin{lvl}") for lvl in admin_levels)
    if natural_types:
        parts.extend(natural_types)
    return ", ".join(parts) if parts else "all"
