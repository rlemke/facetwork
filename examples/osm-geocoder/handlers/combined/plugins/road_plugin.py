"""Road network extractor plugin — way-based.

Extracts roads with classification, speed limits, surface, and lane data.
Coordinates are resolved via ``locations=True`` — no node cache needed.
"""

from __future__ import annotations

import math

from ...roads.road_extractor import (
    ROAD_CLASS_MAP,
    classify_road,
    parse_lanes,
    parse_speed_limit,
)
from ..plugin_base import ElementType, ExtractorPlugin, PluginResult, TagInterest

# All highway values we recognize
_HIGHWAY_VALUES = set(ROAD_CLASS_MAP.keys()) | {
    "living_street",
    "pedestrian",
    "steps",
}


class RoadPlugin(ExtractorPlugin):
    """Extract road network from OSM ways."""

    @property
    def category(self) -> str:
        return "roads"

    @property
    def element_types(self) -> ElementType:
        return ElementType.WAY

    @property
    def tag_interest(self) -> TagInterest:
        return TagInterest(keys={"highway"})

    def __init__(self) -> None:
        self.features: list[dict] = []

    def process_way(
        self,
        way_id: int,
        tags: dict[str, str],
        coords: list[tuple[float, float]],
    ) -> None:
        highway = tags.get("highway", "")
        if highway not in _HIGHWAY_VALUES:
            return
        if len(coords) < 2:
            return

        classification = classify_road(tags)
        speed_limit = parse_speed_limit(tags.get("maxspeed"))
        geometry = {
            "type": "LineString",
            "coordinates": [list(c) for c in coords],
        }
        length_km = _haversine_length(coords)

        self.features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": way_id,
                    "osm_type": "way",
                    "road_class": classification,
                    "highway": highway,
                    "name": tags.get("name", ""),
                    "ref": tags.get("ref", ""),
                    "maxspeed": speed_limit,
                    "lanes": parse_lanes(tags.get("lanes")),
                    "surface": tags.get("surface", ""),
                    "oneway": tags.get("oneway", "") == "yes",
                    "bridge": "bridge" in tags,
                    "tunnel": "tunnel" in tags,
                    "length_km": round(length_km, 3),
                },
                "geometry": geometry,
            }
        )

    def finalize(self, pbf_stem: str, output_dir: str) -> PluginResult:
        path = f"{output_dir}/{pbf_stem}_roads.geojson"
        count = self._write_geojson(self.features, path)

        total_km = sum(f["properties"].get("length_km", 0) for f in self.features)
        with_speed = sum(1 for f in self.features if f["properties"].get("maxspeed"))

        return PluginResult(
            category=self.category,
            output_path=path,
            feature_count=count,
            metadata={
                "total_length_km": round(total_km, 2),
                "with_speed_limit": with_speed,
            },
        )


def _haversine_length(coords: list[tuple[float, float]]) -> float:
    """Calculate length of a coordinate list in km."""
    if len(coords) < 2:
        return 0.0
    total = 0.0
    R = 6371.0
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i]
        lon2, lat2 = coords[i + 1]
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        total += R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return total
