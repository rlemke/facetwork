"""Park/protected area extractor plugin — area-based.

Extracts national parks, state parks, nature reserves, and other protected areas.
"""

from __future__ import annotations

from ...parks.park_extractor import ParkType, calculate_area_km2, classify_park, matches_park_type
from ..plugin_base import ElementType, ExtractorPlugin, PluginResult, TagInterest


class ParkPlugin(ExtractorPlugin):
    """Extract parks and protected areas from OSM areas."""

    @property
    def category(self) -> str:
        return "parks"

    @property
    def element_types(self) -> ElementType:
        return ElementType.AREA

    @property
    def tag_interest(self) -> TagInterest:
        return TagInterest(
            keys={"boundary", "leisure", "protect_class"},
        )

    def __init__(self) -> None:
        self.features: list[dict] = []

    def process_area(
        self,
        area_id: int,
        tags: dict[str, str],
        geometry: dict | None,
        orig_id: int,
        from_way: bool,
    ) -> None:
        if not matches_park_type(tags, ParkType.ALL):
            return

        area_km2 = calculate_area_km2(geometry) if geometry else 0.0
        classification = classify_park(tags)

        self.features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": orig_id,
                    "osm_type": "way" if from_way else "relation",
                    "name": tags.get("name", ""),
                    "park_type": classification,
                    "protect_class": tags.get("protect_class", ""),
                    "designation": tags.get("designation", ""),
                    "operator": tags.get("operator", ""),
                    "area_km2": round(area_km2, 2),
                },
                "geometry": geometry,
            }
        )

    def finalize(self, pbf_stem: str, output_dir: str) -> PluginResult:
        path = f"{output_dir}/{pbf_stem}_parks.geojson"
        count = self._write_geojson(self.features, path)

        total_area = sum(f["properties"].get("area_km2", 0.0) for f in self.features)
        type_counts: dict[str, int] = {}
        for f in self.features:
            pt = f["properties"].get("park_type", "other")
            type_counts[pt] = type_counts.get(pt, 0) + 1

        return PluginResult(
            category=self.category,
            output_path=path,
            feature_count=count,
            metadata={
                "total_area_km2": round(total_area, 2),
                "park_types": type_counts,
            },
        )
