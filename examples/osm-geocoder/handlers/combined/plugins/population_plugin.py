"""Population/places extractor plugin — nodes and relations.

Extracts places (cities, towns, villages, etc.) with population data.
"""

from __future__ import annotations

from ..plugin_base import ElementType, ExtractorPlugin, PluginResult, TagInterest

# Place types recognized by the population extractor
PLACE_TYPES = {
    "city",
    "town",
    "village",
    "hamlet",
    "suburb",
    "country",
    "state",
    "county",
    "municipality",
}


class PopulationPlugin(ExtractorPlugin):
    """Extract populated places from OSM nodes."""

    @property
    def category(self) -> str:
        return "population"

    @property
    def element_types(self) -> ElementType:
        return ElementType.NODE

    @property
    def tag_interest(self) -> TagInterest:
        return TagInterest(keys={"place"})

    def __init__(self) -> None:
        self.features: list[dict] = []

    def process_node(self, node_id: int, tags: dict[str, str], lon: float, lat: float) -> None:
        place = tags.get("place", "")
        if place not in PLACE_TYPES:
            return

        pop_str = tags.get("population", "")
        try:
            population = int(pop_str) if pop_str else 0
        except ValueError:
            population = 0

        self.features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": node_id,
                    "osm_type": "node",
                    "place": place,
                    "name": tags.get("name", ""),
                    "population": population,
                    "admin_level": tags.get("admin_level", ""),
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
            }
        )

    def finalize(self, pbf_stem: str, output_dir: str) -> PluginResult:
        path = f"{output_dir}/{pbf_stem}_population.geojson"
        count = self._write_geojson(self.features, path)

        # Summarize by place type
        type_counts: dict[str, int] = {}
        total_pop = 0
        for f in self.features:
            p = f["properties"]
            pt = p.get("place", "other")
            type_counts[pt] = type_counts.get(pt, 0) + 1
            total_pop += p.get("population", 0)

        return PluginResult(
            category=self.category,
            output_path=path,
            feature_count=count,
            metadata={"place_types": type_counts, "total_population": total_pop},
        )
