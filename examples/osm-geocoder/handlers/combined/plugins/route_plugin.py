"""Route extractor plugin — nodes, ways, and relations.

Extracts cycle routes, hiking trails, train lines, bus routes, and
associated infrastructure (bike parking, shelters, stations, etc.).
"""

from __future__ import annotations

import math

from ...routes.route_extractor import ROUTE_TAGS, RouteType
from ..plugin_base import ElementType, ExtractorPlugin, PluginResult, TagInterest

# Build a merged tag interest from all route types
_ALL_KEYS: set[str] = set()
_ALL_KEY_VALUES: dict[str, set[str]] = {}

for _rt in RouteType:
    cfg = ROUTE_TAGS.get(_rt, {})
    for section in ("routes", "ways", "infrastructure"):
        for key, values in cfg.get(section, {}).items():
            _ALL_KEYS.add(key)
            _ALL_KEY_VALUES.setdefault(key, set()).update(values)


class RoutePlugin(ExtractorPlugin):
    """Extract routes and transport infrastructure."""

    @property
    def category(self) -> str:
        return "routes"

    @property
    def element_types(self) -> ElementType:
        return ElementType.NODE | ElementType.WAY | ElementType.RELATION

    @property
    def tag_interest(self) -> TagInterest:
        return TagInterest(keys=_ALL_KEYS)

    def __init__(self) -> None:
        self.infra_features: list[dict] = []
        self.way_features: list[dict] = []
        self.relation_features: list[dict] = []

    def _classify(self, tags: dict[str, str]) -> list[str]:
        """Return list of route types this element belongs to."""
        types = []
        for rt in RouteType:
            cfg = ROUTE_TAGS.get(rt, {})
            for section in ("routes", "ways", "infrastructure"):
                for key, values in cfg.get(section, {}).items():
                    if key in tags and tags[key] in values:
                        types.append(rt.value)
                        break
                else:
                    continue
                break
        return types or ["other"]

    def process_node(self, node_id: int, tags: dict[str, str], lon: float, lat: float) -> None:
        route_types = self._classify(tags)
        self.infra_features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": node_id,
                    "osm_type": "node",
                    "feature_type": "infrastructure",
                    "route_types": route_types,
                    "name": tags.get("name", ""),
                    "amenity": tags.get("amenity", ""),
                    "tourism": tags.get("tourism", ""),
                    "railway": tags.get("railway", ""),
                },
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
            }
        )

    def process_way(
        self,
        way_id: int,
        tags: dict[str, str],
        coords: list[tuple[float, float]],
    ) -> None:
        if len(coords) < 2:
            return
        route_types = self._classify(tags)
        length_km = _haversine_length(coords)
        self.way_features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": way_id,
                    "osm_type": "way",
                    "feature_type": "way",
                    "route_types": route_types,
                    "name": tags.get("name", ""),
                    "highway": tags.get("highway", ""),
                    "route": tags.get("route", ""),
                    "network": tags.get("network", ""),
                    "length_km": round(length_km, 3),
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [list(c) for c in coords],
                },
            }
        )

    def process_relation(self, relation_id: int, tags: dict[str, str], members: list[dict]) -> None:
        route_types = self._classify(tags)
        self.relation_features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": relation_id,
                    "osm_type": "relation",
                    "feature_type": "route",
                    "route_types": route_types,
                    "name": tags.get("name", ""),
                    "route": tags.get("route", ""),
                    "network": tags.get("network", ""),
                    "ref": tags.get("ref", ""),
                    "member_count": len(members),
                },
                "geometry": None,
            }
        )

    def finalize(self, pbf_stem: str, output_dir: str) -> PluginResult:
        all_features = self.infra_features + self.way_features + self.relation_features
        path = f"{output_dir}/{pbf_stem}_routes.geojson"
        count = self._write_geojson(all_features, path)

        total_km = sum(f["properties"].get("length_km", 0) for f in self.way_features)
        return PluginResult(
            category=self.category,
            output_path=path,
            feature_count=count,
            metadata={
                "infrastructure": len(self.infra_features),
                "ways": len(self.way_features),
                "relations": len(self.relation_features),
                "total_way_km": round(total_km, 2),
            },
        )


def _haversine_length(coords: list[tuple[float, float]]) -> float:
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
