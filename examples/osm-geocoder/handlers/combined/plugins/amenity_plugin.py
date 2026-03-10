"""Amenity extractor plugin — node-only.

Extracts amenities (food, shopping, healthcare, etc.) from OSM nodes.
"""

from __future__ import annotations

from ...amenities.amenity_extractor import classify_amenity
from ..plugin_base import ElementType, ExtractorPlugin, PluginResult, TagInterest


class AmenityPlugin(ExtractorPlugin):
    """Extract amenities by category from OSM nodes."""

    @property
    def category(self) -> str:
        return "amenities"

    @property
    def element_types(self) -> ElementType:
        return ElementType.NODE

    @property
    def tag_interest(self) -> TagInterest:
        return TagInterest(keys={"amenity", "shop", "tourism"})

    def __init__(self) -> None:
        self.features: list[dict] = []

    def process_node(self, node_id: int, tags: dict[str, str], lon: float, lat: float) -> None:
        amenity = tags.get("amenity", "")
        shop = tags.get("shop", "")
        tourism = tags.get("tourism", "")
        if not amenity and not shop and not tourism:
            return

        self.features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": node_id,
                    "osm_type": "node",
                    "amenity": amenity,
                    "shop": shop,
                    "tourism": tourism,
                    "category": classify_amenity(tags),
                    "name": tags.get("name", ""),
                    "opening_hours": tags.get("opening_hours", ""),
                    "phone": tags.get("phone", ""),
                    "website": tags.get("website", ""),
                    "cuisine": tags.get("cuisine", ""),
                    "brand": tags.get("brand", ""),
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
            }
        )

    def finalize(self, pbf_stem: str, output_dir: str) -> PluginResult:
        path = f"{output_dir}/{pbf_stem}_amenities.geojson"
        count = self._write_geojson(self.features, path)
        return PluginResult(
            category=self.category,
            output_path=path,
            feature_count=count,
            metadata={"categories": _count_categories(self.features)},
        )


def _count_categories(features: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in features:
        cat = f["properties"].get("category", "other")
        counts[cat] = counts.get(cat, 0) + 1
    return counts
